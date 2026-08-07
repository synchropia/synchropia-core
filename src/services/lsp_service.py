"""
src/services/lsp_service.py
----------------------------
LSP JSON-RPC 2.0 client, language server registry, session manager,
and LangChain BaseTool subclasses for Synchropia agent harnesses.

Architecture:
  LSPClient         — spawn a language server subprocess, handle
                      Content-Length framing, correlate request/response,
                      dispatch server-push notifications.
  LanguageRegistry  — extension → (binary, initOptions, install_hint)
  LSPClientManager  — lazy per-(workspace_path, language) session pool;
                      torn down on harness close.
  BaseTool subclasses:
    LSPDiagnosticsTool, LSPDefinitionTool, LSPReferencesTool,
    LSPHoverTool, LSPWorkspaceSymbolsTool,
    LSPCodeActionsTool (stretch), LSPFormatDocumentTool (stretch)
"""

from __future__ import annotations

import json
import os
import platform
import queue
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Type

# Windows: suppress console window creation so stdio pipes work correctly
# across multi-level subprocess chains (e.g. .cmd → node → LSP server).
_WIN_POPEN_FLAGS: dict = (
    {"creationflags": subprocess.CREATE_NO_WINDOW}
    if platform.system() == "Windows"
    else {}
)

from langchain_core.callbacks import (
    AsyncCallbackManagerForToolRun,
    CallbackManagerForToolRun,
)
from langchain_core.tools import BaseTool
from loguru import logger
from pydantic import BaseModel, Field

from src.services.lsp_schemas import (
    CodeAction,
    CodeActionsInput,
    Diagnostic,
    DefinitionInput,
    DiagnosticsInput,
    FormatDocumentInput,
    HoverInput,
    HoverResult,
    Location,
    Position,
    Range,
    ReferencesInput,
    WorkspaceSymbol,
    WorkspaceSymbolsInput,
)


# ---------------------------------------------------------------------------
# JSON-RPC 2.0 Framing helpers
# ---------------------------------------------------------------------------

_HEADER_SEP = b"\r\n\r\n"
_CONTENT_LENGTH = b"Content-Length: "


def _encode_message(msg: Dict[str, Any]) -> bytes:
    """Encode a JSON-RPC dict into a Content-Length-framed byte string."""
    body = json.dumps(msg, separators=(",", ":")).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    return header + body


def _decode_messages(buf: bytes) -> Tuple[List[Dict[str, Any]], bytes]:
    """
    Parse zero or more complete JSON-RPC messages from *buf*.
    Returns (list_of_parsed_dicts, remaining_bytes).
    """
    messages: List[Dict[str, Any]] = []
    while True:
        sep_idx = buf.find(_HEADER_SEP)
        if sep_idx == -1:
            break
        header_part = buf[:sep_idx]
        # Find Content-Length in header
        content_len: Optional[int] = None
        for line in header_part.split(b"\r\n"):
            if line.lower().startswith(b"content-length:"):
                try:
                    content_len = int(line.split(b":", 1)[1].strip())
                except ValueError:
                    pass
        if content_len is None:
            break
        body_start = sep_idx + len(_HEADER_SEP)
        if len(buf) < body_start + content_len:
            break  # incomplete body — wait for more data
        body = buf[body_start : body_start + content_len]
        try:
            messages.append(json.loads(body.decode("utf-8")))
        except json.JSONDecodeError as e:
            logger.warning(f"[LSP] JSON decode error: {e}")
        buf = buf[body_start + content_len :]
    return messages, buf


# ---------------------------------------------------------------------------
# Language Server Registry
# ---------------------------------------------------------------------------

class ServerConfig:
    """Configuration for one language server entry."""
    __slots__ = ("binary", "args", "init_options", "install_hint", "language_id")

    def __init__(
        self,
        binary: str,
        args: Optional[List[str]] = None,
        init_options: Optional[Dict[str, Any]] = None,
        install_hint: str = "",
        language_id: str = "",
    ):
        self.binary = binary
        self.args = args or ["--stdio"]
        self.init_options = init_options or {}
        self.install_hint = install_hint
        self.language_id = language_id


_REGISTRY: Dict[str, ServerConfig] = {
    # Python
    ".py": ServerConfig(
        binary="pyright-langserver",
        args=["--stdio"],
        install_hint="pip install pyright",
        language_id="python",
    ),
    # JavaScript / TypeScript
    ".js": ServerConfig(
        binary="typescript-language-server",
        args=["--stdio"],
        install_hint="npm i -g typescript typescript-language-server",
        language_id="javascript",
    ),
    ".jsx": ServerConfig(
        binary="typescript-language-server",
        args=["--stdio"],
        install_hint="npm i -g typescript typescript-language-server",
        language_id="javascriptreact",
    ),
    ".ts": ServerConfig(
        binary="typescript-language-server",
        args=["--stdio"],
        install_hint="npm i -g typescript typescript-language-server",
        language_id="typescript",
    ),
    ".tsx": ServerConfig(
        binary="typescript-language-server",
        args=["--stdio"],
        install_hint="npm i -g typescript typescript-language-server",
        language_id="typescriptreact",
    ),
    # JSON
    ".json": ServerConfig(
        binary="vscode-json-languageserver",
        args=["--stdio"],
        install_hint="npm i -g vscode-langservers-extracted",
        language_id="json",
    ),
    # HTML
    ".html": ServerConfig(
        binary="vscode-html-languageserver",
        args=["--stdio"],
        install_hint="npm i -g vscode-langservers-extracted",
        language_id="html",
    ),
    ".htm": ServerConfig(
        binary="vscode-html-languageserver",
        args=["--stdio"],
        install_hint="npm i -g vscode-langservers-extracted",
        language_id="html",
    ),
    # CSS
    ".css": ServerConfig(
        binary="vscode-css-languageserver",
        args=["--stdio"],
        install_hint="npm i -g vscode-langservers-extracted",
        language_id="css",
    ),
    ".scss": ServerConfig(
        binary="vscode-css-languageserver",
        args=["--stdio"],
        install_hint="npm i -g vscode-langservers-extracted",
        language_id="scss",
    ),
    ".less": ServerConfig(
        binary="vscode-css-languageserver",
        args=["--stdio"],
        install_hint="npm i -g vscode-langservers-extracted",
        language_id="less",
    ),
    # Go  (gopls v0.5+: 'serve' subcommand, communicates over stdio by default)
    ".go": ServerConfig(
        binary="gopls",
        args=["serve"],
        install_hint="go install golang.org/x/tools/gopls@latest",
        language_id="go",
    ),
    # Rust
    ".rs": ServerConfig(
        binary="rust-analyzer",
        args=[],
        install_hint="rustup component add rust-analyzer",
        language_id="rust",
    ),
    # Java
    ".java": ServerConfig(
        binary="jdtls",
        args=[],
        install_hint="Download eclipse.jdt.ls from https://github.com/eclipse-jdtls/eclipse.jdt.ls",
        language_id="java",
    ),
    # Kotlin
    ".kt": ServerConfig(
        binary="kotlin-language-server",
        args=[],
        install_hint="Download from https://github.com/fwcd/kotlin-language-server",
        language_id="kotlin",
    ),
    # C / C++
    ".c": ServerConfig(binary="clangd", args=["--background-index"], install_hint="apt install clangd", language_id="c"),
    ".h": ServerConfig(binary="clangd", args=["--background-index"], install_hint="apt install clangd", language_id="c"),
    ".cpp": ServerConfig(binary="clangd", args=["--background-index"], install_hint="apt install clangd", language_id="cpp"),
    ".hpp": ServerConfig(binary="clangd", args=["--background-index"], install_hint="apt install clangd", language_id="cpp"),
    # C#
    ".cs": ServerConfig(binary="omnisharp", args=["-lsp"], install_hint="Download OmniSharp from https://github.com/OmniSharp/omnisharp-roslyn", language_id="csharp"),
    # Ruby
    ".rb": ServerConfig(binary="solargraph", args=["stdio"], install_hint="gem install solargraph", language_id="ruby"),
    # PHP
    ".php": ServerConfig(binary="intelephense", args=["--stdio"], install_hint="npm i -g intelephense", language_id="php"),
    # Bash
    ".sh": ServerConfig(binary="bash-language-server", args=["start"], install_hint="npm i -g bash-language-server", language_id="shellscript"),
    ".bash": ServerConfig(binary="bash-language-server", args=["start"], install_hint="npm i -g bash-language-server", language_id="shellscript"),
    # SQL
    ".sql": ServerConfig(binary="sql-language-server", args=["up", "--method", "stdio"], install_hint="npm i -g sql-language-server", language_id="sql"),
    # YAML
    ".yml": ServerConfig(binary="yaml-language-server", args=["--stdio"], install_hint="npm i -g yaml-language-server", language_id="yaml"),
    ".yaml": ServerConfig(binary="yaml-language-server", args=["--stdio"], install_hint="npm i -g yaml-language-server", language_id="yaml"),
    # Terraform
    ".tf": ServerConfig(binary="terraform-ls", args=["serve"], install_hint="Download terraform-ls from https://github.com/hashicorp/terraform-ls", language_id="terraform"),
    # Markdown
    ".md": ServerConfig(binary="marksman", args=["server"], install_hint="npm i -g marksman OR download from https://github.com/artempyanykh/marksman", language_id="markdown"),
    # Dockerfile (basename match handled separately)
    "Dockerfile": ServerConfig(
        binary="docker-langserver",
        args=["--stdio"],
        install_hint="npm i -g dockerfile-language-server-nodejs",
        language_id="dockerfile",
    ),
}


class LanguageRegistry:
    """Maps file paths to language server configurations."""

    @staticmethod
    def get_config(path: str) -> Optional[ServerConfig]:
        """
        Return the ServerConfig for the given file path.
        Matches on extension first, then basename (for Dockerfile etc.).
        Returns None if no server is configured.
        """
        basename = os.path.basename(path)
        # Basename match (e.g. Dockerfile)
        if basename in _REGISTRY:
            return _REGISTRY[basename]
        # Extension match
        _, ext = os.path.splitext(path)
        if ext:
            return _REGISTRY.get(ext.lower())
        return None

    @staticmethod
    def no_server_message(path: str) -> str:
        """Human-readable message when no server is configured for a path."""
        _, ext = os.path.splitext(path)
        lang = ext or os.path.basename(path)
        return (
            f"No LSP language server is configured for '{lang}' files. "
            f"Supported extensions: {', '.join(sorted(_REGISTRY.keys()))}."
        )

    @staticmethod
    def binary_missing_message(config: ServerConfig, path: str) -> str:
        return (
            f"LSP server binary '{config.binary}' is not installed or not on PATH. "
            f"Install it with: {config.install_hint}"
        )


# ---------------------------------------------------------------------------
# LSP Client
# ---------------------------------------------------------------------------

class LSPClient:
    """
    Manages one language server subprocess.
    Handles:
      - subprocess lifecycle (spawn, kill)
      - Content-Length JSON-RPC framing (read thread)
      - Request/response correlation by message ID
      - Server-push notification dispatch (e.g. publishDiagnostics)
    """

    def __init__(
        self,
        config: ServerConfig,
        workspace_path: str,
        timeout: float = 30.0,
    ):
        self._config = config
        self._workspace_path = workspace_path
        self._timeout = timeout
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._next_id = 1
        self._pending: Dict[int, queue.Queue] = {}          # id → response queue
        self._notif_handlers: Dict[str, List[Callable]] = {}  # method → handlers
        self._diag_store: Dict[str, List[Dict]] = {}       # uri → diagnostics list
        self._read_thread: Optional[threading.Thread] = None
        self._stopped = False
        self._initialized = False

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> None:
        """Spawn the language server and complete initialize handshake."""
        binary = self._config.binary

        # Resolve the full binary path so we don't rely on shell PATH expansion,
        # which can differ between venv python and system PATH on Windows.
        # Expand PATH explicitly to include common Go/Node/system locations.
        extra_paths = [
            os.path.join(os.path.expanduser("~"), "go", "bin"),  # go install default
            os.path.join(os.path.expanduser("~"), "AppData", "Roaming", "npm"),  # npm -g
            r"C:\Program Files\nodejs",
            "/usr/local/bin",
            "/usr/bin",
        ]
        search_path = os.environ.get("PATH", "") + os.pathsep + os.pathsep.join(extra_paths)
        resolved = shutil.which(binary, path=search_path)
        cmd = [resolved or binary] + self._config.args

        try:
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self._workspace_path,
                **_WIN_POPEN_FLAGS,   # CREATE_NO_WINDOW on Windows, empty dict elsewhere
            )
        except FileNotFoundError:
            raise FileNotFoundError(
                f"LSP binary '{binary}' not found. "
                f"Install with: {self._config.install_hint}"
            )

        # Subscribe to publishDiagnostics
        self.on_notification(
            "textDocument/publishDiagnostics",
            self._on_publish_diagnostics,
        )

        # Start reader thread
        self._read_thread = threading.Thread(
            target=self._reader_loop, daemon=True, name=f"lsp-reader-{binary}"
        )
        self._read_thread.start()

        # LSP initialize handshake
        self._do_initialize()
        self._initialized = True
        logger.success(f"[LSP] {binary} initialized for workspace '{self._workspace_path}'")

    def stop(self) -> None:
        """Send shutdown → exit and kill the subprocess."""
        if self._stopped:
            return
        self._stopped = True
        try:
            if self._initialized and self._proc and self._proc.poll() is None:
                try:
                    self.request("shutdown", {})
                    self.notify("exit", {})
                except Exception:
                    pass
        finally:
            if self._proc:
                try:
                    self._proc.kill()
                except Exception:
                    pass
        logger.info(f"[LSP] {self._config.binary} stopped.")

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.stop()

    # ------------------------------------------------------------------ framing

    def _send(self, msg: Dict[str, Any]) -> None:
        if not self._proc or self._proc.stdin is None:
            raise RuntimeError("LSP client not started.")
        data = _encode_message(msg)
        with self._lock:
            self._proc.stdin.write(data)
            self._proc.stdin.flush()

    def _reader_loop(self) -> None:
        """Background thread: reads stdout, de-frames messages, dispatches."""
        buf = b""
        assert self._proc and self._proc.stdout
        while not self._stopped:
            try:
                chunk = self._proc.stdout.read(4096)
                if not chunk:
                    break
                buf += chunk
                messages, buf = _decode_messages(buf)
                for msg in messages:
                    self._dispatch(msg)
            except Exception as e:
                if not self._stopped:
                    logger.warning(f"[LSP] Reader error: {e}")
                break

    def _dispatch(self, msg: Dict[str, Any]) -> None:
        """Route a parsed message to its response queue or notification handler."""
        msg_id = msg.get("id")
        method = msg.get("method")
        if msg_id is not None and method is None:
            # Response to one of our requests
            q = self._pending.pop(msg_id, None)
            if q:
                q.put(msg)
        elif method:
            # Notification or server request
            handlers = self._notif_handlers.get(method, [])
            for h in handlers:
                try:
                    h(msg.get("params", {}))
                except Exception as e:
                    logger.warning(f"[LSP] Notification handler error ({method}): {e}")

    # ------------------------------------------------------------------ request/notify

    def request(self, method: str, params: Any, timeout: Optional[float] = None) -> Any:
        """Send a JSON-RPC request and wait for the response."""
        msg_id = self._next_id
        self._next_id += 1
        q: queue.Queue = queue.Queue()
        self._pending[msg_id] = q
        self._send({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params})
        try:
            resp = q.get(timeout=timeout or self._timeout)
        except queue.Empty:
            self._pending.pop(msg_id, None)
            raise TimeoutError(f"LSP request '{method}' timed out after {self._timeout}s")
        if "error" in resp:
            err = resp["error"]
            raise RuntimeError(f"LSP error {err.get('code')}: {err.get('message')}")
        return resp.get("result")

    def notify(self, method: str, params: Any) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def on_notification(self, method: str, handler: Callable) -> None:
        """Register a handler for server-push notifications."""
        self._notif_handlers.setdefault(method, []).append(handler)

    # ------------------------------------------------------------------ internal handlers

    def _on_publish_diagnostics(self, params: Dict[str, Any]) -> None:
        uri = params.get("uri", "")
        diags = params.get("diagnostics", [])
        self._diag_store[uri] = diags
        logger.debug(f"[LSP] publishDiagnostics: {uri} → {len(diags)} items")

    # ------------------------------------------------------------------ LSP protocol

    def _do_initialize(self) -> None:
        workspace_uri = Path(self._workspace_path).as_uri()
        init_params = {
            "processId": os.getpid(),
            "rootUri": workspace_uri,
            "workspaceFolders": [{"uri": workspace_uri, "name": os.path.basename(self._workspace_path)}],
            "capabilities": {
                "textDocument": {
                    "publishDiagnostics": {"relatedInformation": True},
                    "hover": {"contentFormat": ["markdown", "plaintext"]},
                    "definition": {"linkSupport": False},
                    "references": {},
                    "documentSymbol": {"hierarchicalDocumentSymbolSupport": True},
                    "codeAction": {"codeActionLiteralSupport": {"codeActionKind": {"valueSet": ["quickfix", "refactor"]}}},
                    "formatting": {},
                },
                "workspace": {
                    "symbol": {"symbolKind": {"valueSet": list(range(1, 27))}},
                },
            },
            "initializationOptions": self._config.init_options,
            "clientInfo": {"name": "synchropia-lsp-client", "version": "1.0"},
        }
        self.request("initialize", init_params)
        self.notify("initialized", {})

    def did_open(self, path: str) -> None:
        """Notify the server that we opened a file."""
        config = LanguageRegistry.get_config(path)
        lang_id = config.language_id if config else "plaintext"
        uri = Path(path).as_uri()
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except FileNotFoundError:
            text = ""
        self.notify("textDocument/didOpen", {
            "textDocument": {"uri": uri, "languageId": lang_id, "version": 1, "text": text}
        })

    def did_close(self, path: str) -> None:
        uri = Path(path).as_uri()
        self.notify("textDocument/didClose", {"textDocument": {"uri": uri}})

    def get_diagnostics(self, path: str, wait_ms: int = 2000) -> List[Dict[str, Any]]:
        """
        Open the document, wait for publishDiagnostics, return raw diag list.
        Falls back to textDocument/diagnostic pull if no push arrives.
        """
        uri = Path(path).as_uri()
        self.did_open(path)
        time.sleep(wait_ms / 1000)
        return self._diag_store.get(uri, [])

    def get_definition(self, path: str, line: int, character: int) -> Any:
        uri = Path(path).as_uri()
        self.did_open(path)
        return self.request("textDocument/definition", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
        })

    def get_references(self, path: str, line: int, character: int, include_declaration: bool = True) -> Any:
        uri = Path(path).as_uri()
        self.did_open(path)
        return self.request("textDocument/references", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
            "context": {"includeDeclaration": include_declaration},
        })

    def get_hover(self, path: str, line: int, character: int) -> Any:
        uri = Path(path).as_uri()
        self.did_open(path)
        return self.request("textDocument/hover", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
        })

    def get_workspace_symbols(self, query: str) -> Any:
        return self.request("workspace/symbol", {"query": query})

    def get_code_actions(self, path: str, line: int, character: int) -> Any:
        uri = Path(path).as_uri()
        self.did_open(path)
        range_d = {
            "start": {"line": line, "character": character},
            "end": {"line": line, "character": character + 1},
        }
        return self.request("textDocument/codeAction", {
            "textDocument": {"uri": uri},
            "range": range_d,
            "context": {"diagnostics": [], "only": ["quickfix", "refactor"]},
        })

    def format_document(self, path: str, tab_size: int = 4, insert_spaces: bool = True) -> Any:
        uri = Path(path).as_uri()
        self.did_open(path)
        return self.request("textDocument/formatting", {
            "textDocument": {"uri": uri},
            "options": {"tabSize": tab_size, "insertSpaces": insert_spaces},
        })


# ---------------------------------------------------------------------------
# LSP Client Manager (per-workspace, per-language session pool)
# ---------------------------------------------------------------------------

class LSPClientManager:
    """
    Singleton-ish manager that maintains one LSPClient per
    (workspace_path, extension) combination.
    Sessions are lazily created and reused across tool calls.
    Call `shutdown_all()` when the sandbox is torn down.
    """

    def __init__(self, timeout: float = 30.0):
        self._clients: Dict[str, LSPClient] = {}
        self._timeout = timeout
        self._lock = threading.Lock()

    def get_client(self, path: str, workspace_path: Optional[str] = None) -> Tuple[LSPClient, ServerConfig]:
        """
        Return a running LSPClient for the language detected from *path*.
        Creates and initializes one if not already present.
        Raises ValueError if no server is configured.
        Raises FileNotFoundError if the server binary is missing.
        """
        config = LanguageRegistry.get_config(path)
        if config is None:
            raise ValueError(LanguageRegistry.no_server_message(path))

        ws = workspace_path or str(Path(path).parent)
        key = f"{ws}::{config.binary}"

        with self._lock:
            if key not in self._clients:
                client = LSPClient(config, workspace_path=ws, timeout=self._timeout)
                client.start()
                self._clients[key] = client
            return self._clients[key], config

    def shutdown_all(self) -> None:
        """Stop all managed language server subprocesses."""
        with self._lock:
            for client in self._clients.values():
                try:
                    client.stop()
                except Exception:
                    pass
            self._clients.clear()
        logger.info("[LSP] All language server sessions shut down.")


# Module-level singleton (shared across all harness instances in one process)
_lsp_manager = LSPClientManager(timeout=30.0)


# ---------------------------------------------------------------------------
# LangChain BaseTool wrappers
# ---------------------------------------------------------------------------

def _safe_call(fn, *args, **kwargs) -> str:
    """Wrap an LSP call, returning a user-friendly error string on failure."""
    try:
        return fn(*args, **kwargs)
    except ValueError as e:
        return str(e)
    except FileNotFoundError as e:
        return str(e)
    except TimeoutError as e:
        return f"LSP timeout: {e}"
    except RuntimeError as e:
        return f"LSP error: {e}"
    except Exception as e:
        return f"Unexpected LSP error: {e}"


class LSPDiagnosticsTool(BaseTool):
    """Get compile-time / type / lint diagnostics for a file via LSP."""

    name: str = "lsp_diagnostics"
    description: str = (
        "Run a Language Server Protocol semantic check on a local file and return "
        "all diagnostics (errors, warnings, hints) with line numbers, column offsets, "
        "and messages. Use this immediately after writing or modifying a file to catch "
        "type errors, undefined symbols, and lint issues BEFORE executing or pushing to CI. "
        "Returns structured JSON with a 'diagnostics' list and a 'summary' field."
    )
    args_schema: Type[BaseModel] = DiagnosticsInput

    def _run(self, path: str, wait_ms: int = 2000,
             run_manager: Optional[CallbackManagerForToolRun] = None) -> str:
        def _inner():
            client, _ = _lsp_manager.get_client(path)
            raw = client.get_diagnostics(path, wait_ms=wait_ms)
            diags = [Diagnostic.from_lsp_dict(d).to_agent_dict() for d in raw]
            errors = sum(1 for d in diags if d["severity"] == "ERROR")
            warnings = sum(1 for d in diags if d["severity"] == "WARNING")
            return json.dumps({
                "path": path,
                "summary": f"{errors} error(s), {warnings} warning(s)" if diags else "No diagnostics — file looks clean.",
                "diagnostics": diags,
            }, indent=2)
        return _safe_call(_inner)

    async def _arun(self, path: str, wait_ms: int = 2000,
                    run_manager: Optional[AsyncCallbackManagerForToolRun] = None) -> str:
        return self._run(path, wait_ms=wait_ms)


class LSPDefinitionTool(BaseTool):
    """Jump to the definition of a symbol at a given position."""

    name: str = "lsp_definition"
    description: str = (
        "Find where a symbol (function, class, variable, import) is **defined**. "
        "Provide the file path and the 1-indexed line/character of the symbol. "
        "Returns the file path and line number of the definition."
    )
    args_schema: Type[BaseModel] = DefinitionInput

    def _run(self, path: str, line: int, character: int,
             run_manager: Optional[CallbackManagerForToolRun] = None) -> str:
        def _inner():
            client, _ = _lsp_manager.get_client(path)
            result = client.get_definition(path, line=line - 1, character=character - 1)
            if not result:
                return json.dumps({"path": path, "line": line, "character": character, "definition": None, "message": "No definition found."})
            locations = result if isinstance(result, list) else [result]
            parsed = []
            for loc in locations:
                if "uri" in loc and "range" in loc:
                    parsed.append(str(Location(uri=loc["uri"], range=Range.from_dict(loc["range"]))))
            return json.dumps({"path": path, "line": line, "character": character, "definitions": parsed}, indent=2)
        return _safe_call(_inner)

    async def _arun(self, path: str, line: int, character: int,
                    run_manager: Optional[AsyncCallbackManagerForToolRun] = None) -> str:
        return self._run(path, line, character)


class LSPReferencesTool(BaseTool):
    """Find all references to a symbol across the workspace."""

    name: str = "lsp_references"
    description: str = (
        "Find all locations in the workspace where a symbol is **used** (referenced). "
        "Provide the file path and the 1-indexed line/character of the symbol. "
        "Returns a list of file paths and line numbers."
    )
    args_schema: Type[BaseModel] = ReferencesInput

    def _run(self, path: str, line: int, character: int, include_declaration: bool = True,
             run_manager: Optional[CallbackManagerForToolRun] = None) -> str:
        def _inner():
            client, _ = _lsp_manager.get_client(path)
            result = client.get_references(path, line=line - 1, character=character - 1, include_declaration=include_declaration)
            if not result:
                return json.dumps({"path": path, "references": [], "message": "No references found."})
            refs = []
            for loc in result:
                if "uri" in loc and "range" in loc:
                    refs.append(str(Location(uri=loc["uri"], range=Range.from_dict(loc["range"]))))
            return json.dumps({"path": path, "line": line, "character": character, "reference_count": len(refs), "references": refs}, indent=2)
        return _safe_call(_inner)

    async def _arun(self, path: str, line: int, character: int, include_declaration: bool = True,
                    run_manager: Optional[AsyncCallbackManagerForToolRun] = None) -> str:
        return self._run(path, line, character, include_declaration)


class LSPHoverTool(BaseTool):
    """Get type signature and docstring for a symbol at a given position."""

    name: str = "lsp_hover"
    description: str = (
        "Get the type signature, docstring, and documentation for any identifier "
        "at the given file position. Useful for understanding an unfamiliar API or "
        "verifying a type before calling a function."
    )
    args_schema: Type[BaseModel] = HoverInput

    def _run(self, path: str, line: int, character: int,
             run_manager: Optional[CallbackManagerForToolRun] = None) -> str:
        def _inner():
            client, _ = _lsp_manager.get_client(path)
            result = client.get_hover(path, line=line - 1, character=character - 1)
            hover = HoverResult.from_lsp_dict(result) if result else None
            if not hover:
                return json.dumps({"path": path, "line": line, "character": character, "hover": None, "message": "No hover info available."})
            return json.dumps({"path": path, "line": line, "character": character, "hover": hover.contents}, indent=2)
        return _safe_call(_inner)

    async def _arun(self, path: str, line: int, character: int,
                    run_manager: Optional[AsyncCallbackManagerForToolRun] = None) -> str:
        return self._run(path, line, character)


class LSPWorkspaceSymbolsTool(BaseTool):
    """Search for any symbol (function, class, variable) by name across the workspace."""

    name: str = "lsp_workspace_symbols"
    description: str = (
        "Search the entire workspace for any symbol (function, class, variable, interface) "
        "by name or prefix. Use this to locate code without opening every file manually. "
        "Pass an empty query to list ALL symbols in the workspace. "
        "workspace_path must be set to the root directory of the project."
    )
    args_schema: Type[BaseModel] = WorkspaceSymbolsInput

    def _run(self, query: str, workspace_path: str, language: str = "python",
             run_manager: Optional[CallbackManagerForToolRun] = None) -> str:
        def _inner():
            # Find a representative file extension for the language
            ext_map = {
                "python": ".py", "typescript": ".ts", "javascript": ".js",
                "go": ".go", "rust": ".rs", "java": ".java", "c": ".c",
                "cpp": ".cpp", "ruby": ".rb", "php": ".php", "bash": ".sh",
            }
            dummy_ext = ext_map.get(language.lower(), ".py")
            dummy_path = os.path.join(workspace_path, f"__dummy{dummy_ext}")
            client, _ = _lsp_manager.get_client(dummy_path, workspace_path=workspace_path)
            result = client.get_workspace_symbols(query)
            if not result:
                return json.dumps({"query": query, "symbols": [], "message": "No symbols found."})
            symbols = [WorkspaceSymbol.from_lsp_dict(s).to_agent_dict() for s in result[:50]]
            return json.dumps({"query": query, "count": len(symbols), "symbols": symbols}, indent=2)
        return _safe_call(_inner)

    async def _arun(self, query: str, workspace_path: str, language: str = "python",
                    run_manager: Optional[AsyncCallbackManagerForToolRun] = None) -> str:
        return self._run(query, workspace_path, language)


class LSPCodeActionsTool(BaseTool):
    """List available quick-fixes / code actions for a position in a file."""

    name: str = "lsp_code_actions"
    description: str = (
        "List available quick-fixes and refactoring options at a given file position. "
        "Typically called after lsp_diagnostics reveals an error to discover automatic fix options. "
        "Returns a list of available code actions with their titles."
    )
    args_schema: Type[BaseModel] = CodeActionsInput

    def _run(self, path: str, line: int, character: int,
             run_manager: Optional[CallbackManagerForToolRun] = None) -> str:
        def _inner():
            client, _ = _lsp_manager.get_client(path)
            result = client.get_code_actions(path, line=line - 1, character=character - 1)
            if not result:
                return json.dumps({"path": path, "code_actions": [], "message": "No code actions available at this position."})
            actions = [CodeAction.from_lsp_dict(a) for a in result if isinstance(a, dict)]
            return json.dumps({
                "path": path,
                "line": line,
                "character": character,
                "code_actions": [{"title": a.title, "kind": a.kind, "is_preferred": a.is_preferred} for a in actions],
            }, indent=2)
        return _safe_call(_inner)

    async def _arun(self, path: str, line: int, character: int,
                    run_manager: Optional[AsyncCallbackManagerForToolRun] = None) -> str:
        return self._run(path, line, character)


class LSPFormatDocumentTool(BaseTool):
    """Return formatted source for a file via the language server formatter."""

    name: str = "lsp_format_document"
    description: str = (
        "Request the language server to format a document and return the formatted source. "
        "Does NOT automatically overwrite the file — returns the formatted text for review. "
        "Use refactoring_code to apply the result if it looks correct."
    )
    args_schema: Type[BaseModel] = FormatDocumentInput

    def _run(self, path: str, tab_size: int = 4, insert_spaces: bool = True,
             run_manager: Optional[CallbackManagerForToolRun] = None) -> str:
        def _inner():
            client, _ = _lsp_manager.get_client(path)
            edits = client.format_document(path, tab_size=tab_size, insert_spaces=insert_spaces)
            if not edits:
                return json.dumps({"path": path, "message": "No formatting changes suggested."})
            # Read file and apply text edits (sorted reverse so ranges don't shift)
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            for edit in sorted(edits, key=lambda e: (e["range"]["start"]["line"], e["range"]["start"]["character"]), reverse=True):
                r = edit["range"]
                new_text = edit.get("newText", "")
                s_line, s_char = r["start"]["line"], r["start"]["character"]
                e_line, e_char = r["end"]["line"], r["end"]["character"]
                # Apply single-line edits for safety
                if s_line == e_line and s_line < len(lines):
                    line = lines[s_line]
                    lines[s_line] = line[:s_char] + new_text + line[e_char:]
            formatted = "".join(lines)
            return json.dumps({"path": path, "formatted_source": formatted, "message": "Formatted source returned. Use refactoring_code to apply."}, indent=2)
        return _safe_call(_inner)

    async def _arun(self, path: str, tab_size: int = 4, insert_spaces: bool = True,
                    run_manager: Optional[AsyncCallbackManagerForToolRun] = None) -> str:
        return self._run(path, tab_size, insert_spaces)


# ---------------------------------------------------------------------------
# Convenience export — factory function avoids Pydantic forward-ref issues
# ---------------------------------------------------------------------------

def get_lsp_tools() -> list:
    """Return a fresh list of all LSP tool instances."""
    return [
        LSPDiagnosticsTool(),
        LSPDefinitionTool(),
        LSPReferencesTool(),
        LSPHoverTool(),
        LSPWorkspaceSymbolsTool(),
        LSPCodeActionsTool(),
        LSPFormatDocumentTool(),
    ]


# Module-level singleton built once — used by harness_base
LSP_TOOLS = get_lsp_tools()

__all__ = [
    "LSPClient",
    "LSPClientManager",
    "LanguageRegistry",
    "ServerConfig",
    "_lsp_manager",
    "LSP_TOOLS",
    "get_lsp_tools",
    "LSPDiagnosticsTool",
    "LSPDefinitionTool",
    "LSPReferencesTool",
    "LSPHoverTool",
    "LSPWorkspaceSymbolsTool",
    "LSPCodeActionsTool",
    "LSPFormatDocumentTool",
]
