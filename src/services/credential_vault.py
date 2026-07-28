from __future__ import annotations
import os
from dotenv import load_dotenv
from dataclasses import dataclass
from typing import Optional
from loguru import logger

import infisical_client as infisical

load_dotenv()

@dataclass
class VaultConfig:
    """How the vault authenticates with Infisical.

    Priority:
      1. `access_token` passed directly
      2. `INFISICAL_TOKEN` env var
      3. Logged-in CLI session (local vault)
    """

    access_token: Optional[str] = None
    site_url: str = "https://app.infisical.com"
    environment: str = "dev"
    project_id: str = ""


class CredentialVault:
    """Per-organization credential vault backed by Infisical.

    Two namespaces:
      `/`           — global env-var style secrets (API keys, DB creds)
      `/{org_id}/`  — per-org OAuth tokens for connected services

    Usage:
        vault = CredentialVault.get()  # singleton
        github_token = vault.get_org_token(org_id, "github")
        db_pass = vault.get_secret("POSTGRES_PASSWORD")
    """

    _instance: Optional["CredentialVault"] = None

    def __init__(self, config: Optional[VaultConfig] = None) -> None:
        cfg = config or VaultConfig()

        token = cfg.access_token or os.getenv("INFISICAL_TOKEN")
        if not token:
            logger.warning(
                "No INFISICAL_TOKEN set — falling back to local CLI session"
            )

        settings = infisical.ClientSettings(
            access_token=token,
            site_url=cfg.site_url,
        )
        self._client = infisical.InfisicalClient(settings=settings)
        self._env = cfg.environment or "dev"
        self._project_id = cfg.project_id or os.getenv("INFISICAL_PROJECT_ID", "")

    # ------------------------------------------------------------------
    # Singleton
    # ------------------------------------------------------------------

    @classmethod
    def get(cls, config: Optional[VaultConfig] = None) -> "CredentialVault":
        if cls._instance is None:
            cls._instance = cls(config)
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        cls._instance = None

    # ------------------------------------------------------------------
    # Global secrets
    # ------------------------------------------------------------------

    def get_secret(self, key: str) -> str:
        """Fetch a global secret from `/` (API keys, DB creds, etc.)."""
        return self._get_secret(key, path="/")

    def list_secrets(self, path: str = "/") -> dict[str, str]:
        """List all secrets at a given path."""
        try:
            opts = infisical.ListSecretsOptions(
                environment=self._env,
                project_id=self._project_id,
                path=path,
                attach_to_process_env=False,
            )
            secrets = self._client.listSecrets(opts) or []
            return {s.secret_key: s.secret_value for s in secrets}
        except Exception as exc:
            logger.error(
                "Failed to list secrets path={} env={} project={}: {}",
                path, self._env, self._project_id, exc,
            )
            return {}

    # ------------------------------------------------------------------
    # Per-org OAuth tokens
    # ------------------------------------------------------------------

    def get_org_token(
        self, org_id: str, service: str
    ) -> Optional[dict[str, str]]:
        """Fetch all OAuth token fields for (org, service) at `/{org_id}/{service}/`.

        Returns dict with keys like: access_token, refresh_token, scope, expires_at
        or None if no connection exists.
        """
        path = f"/{org_id}/{service}"
        secrets = self.list_secrets(path=path)
        if not secrets:
            logger.warning("No secrets found for org={} service={}", org_id, service)
            return None
        return secrets

    def get_org_token_field(
        self, org_id: str, service: str, field: str = "access_token"
    ) -> Optional[str]:
        """Fetch a single token field, e.g. get_org_token_field(org, "github", "access_token")."""
        return self.get_secret_at_path(f"/{org_id}/{service}/{field}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def get_secret_at_path(self, key: str, path: str = "/") -> Optional[str]:
        """Fetch a single secret at an arbitrary path."""
        try:
            opts = infisical.GetSecretOptions(
                environment=self._env,
                project_id=self._project_id,
                secret_name=key,
                path=path,
                include_imports=True,
            )
            result = self._client.getSecret(opts)
            return result.secret_value if result else None
        except Exception as exc:
            logger.warning("Secret not found key={} path={}: {}", key, path, exc)
            return None

    def set_secret(
        self,
        key: str,
        value: str,
        path: str = "/",
    ) -> None:
        """Create or update a secret."""
        try:
            opts = infisical.UpdateSecretOptions(
                environment=self._env,
                project_id=self._project_id,
                secret_name=key,
                secret_value=value,
                path=path,
            )
            self._client.updateSecret(opts)
            logger.info("Secret set key={} path={}", key, path)
        except Exception as exc:
            logger.error("Failed to set secret key={}: {}", key, exc)

    def delete_secret(self, key: str, path: str = "/") -> None:
        """Delete a secret."""
        try:
            opts = infisical.DeleteSecretOptions(
                environment=self._env,
                project_id=self._project_id,
                secret_name=key,
                path=path,
            )
            self._client.deleteSecret(opts)
            logger.info("Secret deleted key={} path={}", key, path)
        except Exception as exc:
            logger.error("Failed to delete secret key={}: {}", key, exc)

    def _get_secret(self, key: str, path: str = "/") -> str:
        val = self.get_secret_at_path(key, path=path)
        if val is None:
            raise ValueError(f"Secret not found: {key} at path={path}")
        return val
