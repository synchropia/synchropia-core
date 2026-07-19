"""
agents/utils/harness_base.py
--------------------
Shared base class and sandbox execution backends for Synchropia agent harnesses.
"""
import platform
import asyncio
import os
import subprocess
import sys
from functools import cached_property
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from typing import Any, List

from typing_extensions import override, AsyncContextManager, Self

from deepagents.backends.sandbox import BaseSandbox
from deepagents.backends.protocol import ExecuteResponse, FileUploadResponse, FileDownloadResponse

try:
    from openshell.sandbox import SandboxClient, SandboxError
    HAS_OPENSHELL = True
except ModuleNotFoundError:
    HAS_OPENSHELL = False
    SandboxClient = None
    SandboxError = Exception

from src.services import * 
from src.config.settings import settings
from src.agents.utils.handoffs import HANDOFF_TOOLS
from src.agents.utils.filesystem_tools import CUSTOM_FILESYSTEM_TOOLS

io_executor = ThreadPoolExecutor(max_workers=4)


# ---------------------------------------------------------------------------
# Backend implementations
# ---------------------------------------------------------------------------

class OpenShellBackend(BaseSandbox, AsyncContextManager):
    """Remote OpenShell-backed sandbox (primary)."""

    def __init__(self, sandbox_name: str):
        self.sandbox_name = sandbox_name
        self.client = None
        self.session = None

    def _init_sandbox(self):
        if not HAS_OPENSHELL:
            raise RuntimeError("OpenShell is not installed or available.")
        self.client = SandboxClient.from_active_cluster()
        try:
            self.session = self.client.get_session(self.sandbox_name)
        except Exception:
            self.session = self.client.create_session()
            self.client.wait_ready(self.session.sandbox.name)

    async def __aenter__(self) -> Self:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(io_executor, self._init_sandbox)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.client:
            await asyncio.get_running_loop().run_in_executor(io_executor, self.client.close)

    @property
    def id(self) -> str:
        return self.session.sandbox.name if self.session else ""

    @override
    def execute(self, command: str) -> ExecuteResponse:
        try:
            cmd_list = ["/bin/bash", "-c", command]
            result = self.session.exec(cmd_list)
            return ExecuteResponse(
                output=result.stdout + result.stderr,
                exit_code=result.exit_code,
            )
        except SandboxError as e:
            return ExecuteResponse(output=str(e), exit_code=-1)

    @override
    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return [self.client.upload_file(self.id, path, content) for path, content in files]

    @override
    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return [self.client.download_file(self.id, path) for path in paths]

    @property
    def status(self):
        return self.client.get_session(self.id).sandbox.phase if self.client else "unknown"

    @cached_property
    def base_os(self):
        return self.execute("cat /etc/os-release").output

    def close(self):
        if self.client:
            self.client.close()


class LocalPowerShellBackend(BaseSandbox, AsyncContextManager):
    """
    Local PowerShell Backend (fallback).

    Runs commands on the local machine using Windows PowerShell (or pwsh on
    other platforms) and manages files relative to self.root_dir.
    """

    def __init__(self, root_dir: str | Path | None = None, sandbox_name: str = "local-powershell"):
        self.sandbox_name = sandbox_name
        self.root_dir = Path(root_dir).resolve() if root_dir else Path.cwd().resolve()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

    @cached_property
    def _powershell_cmd(self) -> str:
        for cmd in ["powershell.exe", "powershell", "pwsh"]:
            try:
                res = subprocess.run(
                    [cmd, "-NoProfile", "-NonInteractive", "-Command", "exit 0"],
                    capture_output=True,
                    timeout=5,
                )
                if res.returncode == 0:
                    return cmd
            except Exception:
                continue
        return "powershell"

    @property
    def id(self) -> str:
        return self.sandbox_name

    @property
    def status(self) -> str:
        return "running"

    @cached_property
    def base_os(self) -> str:
        return "Windows PowerShell" if sys.platform == "win32" else "PowerShell Core"

    def close(self):
        pass

    @override
    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """Execute a PowerShell command locally."""
        # Use python instead of python3 for local Windows compatibility
        command = command.replace("python3 ", "python ")
        try:
            cmd_list = [
                self._powershell_cmd,
                "-NoProfile", "-NonInteractive",
                "-Command", command,
            ]
            env = os.environ.copy()
            result = subprocess.run(
                cmd_list,
                capture_output=True,
                text=True,
                cwd=str(self.root_dir),
                timeout=timeout or 120,
                env=env,
                errors="replace",
            )

            parts = []
            if result.stdout:
                parts.append(result.stdout)
            if result.stderr:
                parts.extend(f"[stderr] {l}" for l in result.stderr.strip().splitlines())

            output = "\n".join(parts) if parts else "<no output>"

            MAX_BYTES = 100_000
            truncated = False
            if len(output) > MAX_BYTES:
                output = output[:MAX_BYTES] + f"\n\n... Output truncated at {MAX_BYTES} bytes."
                truncated = True

            return ExecuteResponse(output=output, exit_code=result.returncode, truncated=truncated)
        except subprocess.TimeoutExpired:
            return ExecuteResponse(
                output=f"Error: Command timed out after {timeout or 120} seconds.",
                exit_code=124,
                truncated=False,
            )
        except Exception as e:
            return ExecuteResponse(output=f"Error executing command: {e}", exit_code=1, truncated=False)

    def _safe_resolve(self, path_str: str) -> Path:
        """Strip drive letters/leading slashes and resolve relative to root_dir."""
        p = path_str
        if len(p) >= 2 and p[1] == ":" and p[0].isalpha():
            p = p[2:]
        p = p.lstrip("/\\")
        target = (self.root_dir / p).resolve()
        if not target.is_relative_to(self.root_dir):
            raise PermissionError(f"Path escapes root directory: {path_str}")
        return target

    @override
    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        responses = []
        for path_str, content in files:
            try:
                target = self._safe_resolve(path_str)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                responses.append(FileUploadResponse(path=path_str))
            except Exception as e:
                responses.append(FileUploadResponse(path=path_str, error=str(e)))
        return responses

    @override
    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        responses = []
        for path_str in paths:
            try:
                target = self._safe_resolve(path_str)
                if not target.exists():
                    responses.append(FileDownloadResponse(path=path_str, error="file_not_found"))
                elif target.is_dir():
                    responses.append(FileDownloadResponse(path=path_str, error="is_directory"))
                else:
                    responses.append(FileDownloadResponse(path=path_str, content=target.read_bytes()))
            except Exception as e:
                responses.append(FileDownloadResponse(path=path_str, error=str(e)))
        return responses


# ---------------------------------------------------------------------------
# Base Harness Class
# ---------------------------------------------------------------------------

class BaseAgentHarness:
    """
    Base class for Synchropia agent harnesses.
    Handles sandbox resolution and provides tool registration helper utilities.

    ['transfer_to_architect', 'transfer_to_pm', 'transfer_to_devops', 'transfer_to_qa', 'transfer_to_frontend', 'transfer_to_backend', 'transfer_to_data_engineer', 'transfer_to_data_scientist', 'transfer_to_mlops', 'transfer_to_ai', 'inspect_a_file', 'write_code_to_file', 'refactoring_code', 'extract_content_within_a_file', 'look_for_directory', 'current_directory', 'change_directory', 'navigate_entire_codebase_given_path', 'execute_python_code', 'windows_powershell_command']

    are the Non-negotiable tools for all the swarm agents excluding the PM Agent 

    """

    def __init__(self, sandbox_name: str):
        # 1. Resolve backend: OpenShell → LocalPowerShell fallback
        self.backend: BaseSandbox | None = None
        if HAS_OPENSHELL:
            try:
                self.backend = OpenShellBackend(sandbox_name=sandbox_name)
            except Exception as e:
                print(
                    f"[{self.__class__.__name__}] Warning: OpenShellBackend failed ({e}). "
                    "Falling back to LocalPowerShellBackend."
                )

        if self.backend is None:
            self.backend = LocalPowerShellBackend(sandbox_name=sandbox_name)

        # Initialize tool list with unified handoff tools
  
        self.tools: List[Any] = list(HANDOFF_TOOLS) + list(CUSTOM_FILESYSTEM_TOOLS)

    @staticmethod
    def _try_init(cls, *args, **kwargs):
        """Attempt to instantiate a client; return None on any error."""
        try:
            return cls(*args, **kwargs)
        except Exception as e:
            print(f"[HarnessBase] Could not init {cls.__name__}: {e}")
            return None

    def _register_tools(self, tool_classes: list, **client_kwargs):
        """Instantiate each tool class with the relevant client keyword arg."""
        for t_cls in tool_classes:
            try:
                self.tools.append(t_cls(**client_kwargs))
            except Exception as e:
                print(f"[{self.__class__.__name__}] Skipping {t_cls.__name__}: {e}")

if __name__ == "__main__":
    baseagentharness = BaseAgentHarness("test-sandbox")
    print([t.name for t in baseagentharness.tools])
    
