"""Minimal Infisical client for bootstrapping env vars before Settings() loads.

This module intentionally does NOT import anything from src.services or src.config
to avoid circular imports with settings.py.
"""

from __future__ import annotations

import os
from typing import Optional

from loguru import logger

import infisical_client as infisical


def bootstrap_env_from_vault() -> None:
    """Pull any missing env vars from Infisical into os.environ.

    Call this before pydantic BaseSettings() is constructed so that
    all required fields resolve without needing a .env file.
    """
    token = os.getenv("INFISICAL_TOKEN")
    project_id = os.getenv("INFISICAL_PROJECT_ID", "")
    environment = os.getenv("INFISICAL_ENVIRONMENT", "dev")

    if not token or not project_id:
        logger.debug(
            "INFISICAL_TOKEN or INFISICAL_PROJECT_ID not set — skipping vault bootstrap"
        )
        return

    try:
        settings = infisical.ClientSettings(
            access_token=token,
            site_url=os.getenv("INFISICAL_SITE_URL", "https://app.infisical.com"),
        )
        client = infisical.InfisicalClient(settings=settings)
    except Exception as exc:
        logger.warning("Failed to init Infisical client: {}", exc)
        return

    _REQUIRED_FIELDS = [
        "GITHUB_APP_ID",
        "GITHUB_APP_PRIVATE_KEY_PATH",
        "GITHUB_INSTALLATION_ID",
        "GITHUB_PAT_TOKEN",
        "SONARQUBE_API_KEY",
        "JIRA_API_TOKEN",
        "JIRA_URL",
        "JIRA_EMAIL",
        "SLACK_BOT_TOKEN",
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_DB",
        "DATABASE_URL_DEV",
        "REDIS_HOST",
        "REDIS_PORT",
        "REDIS_PASSWORD",
        "REDIS_DB",
        "COHERE_EMBED_MODEL",
        "JWT_SECRET_KEY",
        "SEMANTIC_CACHE_THRESHOLD",
        "SEMANTIC_CACHE_TTL",
        "GROQ_API_KEY",
        "OPENROUTER_API_KEY",
        "CEREBRAS_API_KEY",
        "GOOGLE_API_KEY",
        "COHERE_API_KEY",
        "MISTRAL_API_KEY",
        "SAMBANOVA_API_KEY",
        "NVIDIA_API_KEY",
        "GITLAB_PRIVATE_TOKEN",
        "JIRA_API_TOKEN_2",
        "MEM0_API_KEY",
        "LANGSMITH_TRACING",
        "LANGSMITH_ENDPOINT",
        "LANGSMITH_API_KEY",
        "LANGSMITH_PROJECT",
        "STRIPE_PUBLIC_KEY",
        "STRIPE_SECRET_KEY",
        "KAFKA_BOOTSTRAP_SERVERS",
    ]

    for key in _REQUIRED_FIELDS:
        if key not in os.environ:
            try:
                opts = infisical.GetSecretOptions(
                    environment=environment,
                    project_id=project_id,
                    secret_name=key,
                    path="/",
                    include_imports=True,
                )
                result = client.getSecret(opts)
                if result and result.secret_value:
                    os.environ[key] = result.secret_value
                    logger.debug("Pulled {} from Infisical vault", key)
            except Exception:
                pass  # pydantic will raise if field is required
