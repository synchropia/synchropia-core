"""
src/services/lsp_schemas.py
---------------------------
Pydantic models for LSP JSON-RPC message structures used by lsp_service.py.
Mirrors the LSP 3.17 specification types. Used as structured output schemas
for LangChain tool responses and internal message routing.
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Core LSP position/range types
# ---------------------------------------------------------------------------

class Position(BaseModel):
    """LSP Position — zero-indexed line and character offset."""
    line: int = Field(description="Zero-based line number.")
    character: int = Field(description="Zero-based character offset on the line (UTF-16 code units).")

    def to_dict(self) -> Dict[str, int]:
        return {"line": self.line, "character": self.character}

    def __str__(self) -> str:
        return f"L{self.line + 1}:{self.character}"


class Range(BaseModel):
    """LSP Range — start and end position within a document."""
    start: Position
    end: Position

    def __str__(self) -> str:
        return f"{self.start}-{self.end}"

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Range":
        return cls(
            start=Position(**d["start"]),
            end=Position(**d["end"]),
        )


class Location(BaseModel):
    """LSP Location — a URI + Range."""
    uri: str = Field(description="Document URI (file://) where the symbol lives.")
    range: Range

    def __str__(self) -> str:
        # Convert file:// URI to a readable path
        path = self.uri.replace("file:///", "").replace("file://", "")
        return f"{path}:{self.range}"


# ---------------------------------------------------------------------------
# Diagnostic
# ---------------------------------------------------------------------------

class DiagnosticSeverity:
    ERROR = 1
    WARNING = 2
    INFORMATION = 3
    HINT = 4

    LABELS = {1: "ERROR", 2: "WARNING", 3: "INFORMATION", 4: "HINT"}


class Diagnostic(BaseModel):
    """A single LSP diagnostic (error / warning / info / hint)."""
    range: Range
    severity: Optional[int] = Field(default=None, description="1=Error 2=Warning 3=Info 4=Hint")
    code: Optional[Union[str, int]] = Field(default=None, description="Diagnostic code from the language server.")
    source: Optional[str] = Field(default=None, description="Source tool name (e.g. 'Pyright', 'eslint').")
    message: str = Field(description="Human-readable diagnostic message.")
    related_information: Optional[List[Dict[str, Any]]] = None

    @property
    def severity_label(self) -> str:
        return DiagnosticSeverity.LABELS.get(self.severity or 0, "UNKNOWN")

    def to_agent_dict(self) -> Dict[str, Any]:
        """Compact representation for LLM consumption."""
        return {
            "severity": self.severity_label,
            "message": self.message,
            "line": self.range.start.line + 1,       # 1-indexed for readability
            "column": self.range.start.character + 1,
            "end_line": self.range.end.line + 1,
            "end_column": self.range.end.character + 1,
            "code": self.code,
            "source": self.source,
        }

    @classmethod
    def from_lsp_dict(cls, d: Dict[str, Any]) -> "Diagnostic":
        return cls(
            range=Range.from_dict(d["range"]),
            severity=d.get("severity"),
            code=d.get("code"),
            source=d.get("source"),
            message=d.get("message", ""),
            related_information=d.get("relatedInformation"),
        )


# ---------------------------------------------------------------------------
# Symbol info
# ---------------------------------------------------------------------------

class SymbolKind:
    LABELS = {
        1: "File", 2: "Module", 3: "Namespace", 4: "Package", 5: "Class",
        6: "Method", 7: "Property", 8: "Field", 9: "Constructor", 10: "Enum",
        11: "Interface", 12: "Function", 13: "Variable", 14: "Constant",
        15: "String", 16: "Number", 17: "Boolean", 18: "Array", 19: "Object",
        20: "Key", 21: "Null", 22: "EnumMember", 23: "Struct", 24: "Event",
        25: "Operator", 26: "TypeParameter",
    }

    @classmethod
    def label(cls, kind: int) -> str:
        return cls.LABELS.get(kind, f"Kind({kind})")


class WorkspaceSymbol(BaseModel):
    """A symbol returned by workspace/symbol."""
    name: str
    kind: int = Field(description="SymbolKind integer.")
    location: Location
    container_name: Optional[str] = None

    @property
    def kind_label(self) -> str:
        return SymbolKind.label(self.kind)

    def to_agent_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind_label,
            "location": str(self.location),
            "container": self.container_name,
        }

    @classmethod
    def from_lsp_dict(cls, d: Dict[str, Any]) -> "WorkspaceSymbol":
        loc_d = d.get("location", {})
        return cls(
            name=d.get("name", ""),
            kind=d.get("kind", 13),
            location=Location(
                uri=loc_d.get("uri", ""),
                range=Range.from_dict(loc_d.get("range", {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 0}})),
            ),
            container_name=d.get("containerName"),
        )


# ---------------------------------------------------------------------------
# Hover
# ---------------------------------------------------------------------------

class HoverResult(BaseModel):
    """Result from textDocument/hover."""
    contents: str = Field(description="Markdown or plain-text hover content.")
    range: Optional[Range] = None

    @classmethod
    def from_lsp_dict(cls, d: Dict[str, Any]) -> Optional["HoverResult"]:
        if not d:
            return None
        contents = d.get("contents", "")
        if isinstance(contents, dict):
            contents = contents.get("value", str(contents))
        elif isinstance(contents, list):
            parts = []
            for c in contents:
                if isinstance(c, dict):
                    parts.append(c.get("value", ""))
                else:
                    parts.append(str(c))
            contents = "\n".join(parts)
        r = d.get("range")
        return cls(
            contents=str(contents),
            range=Range.from_dict(r) if r else None,
        )


# ---------------------------------------------------------------------------
# Code Action
# ---------------------------------------------------------------------------

class CodeAction(BaseModel):
    """A code action / quick-fix available for a diagnostic."""
    title: str
    kind: Optional[str] = None           # e.g. "quickfix", "refactor"
    is_preferred: Optional[bool] = None

    @classmethod
    def from_lsp_dict(cls, d: Dict[str, Any]) -> "CodeAction":
        return cls(
            title=d.get("title", ""),
            kind=d.get("kind"),
            is_preferred=d.get("isPreferred"),
        )


# ---------------------------------------------------------------------------
# LSP tool input schemas (for LangChain args_schema)
# ---------------------------------------------------------------------------

class DiagnosticsInput(BaseModel):
    path: str = Field(description="Absolute path to the file to check for diagnostics.")
    wait_ms: int = Field(default=2000, description="Milliseconds to wait for diagnostics to arrive (default 2000).")


class DefinitionInput(BaseModel):
    path: str = Field(description="Absolute path to the file.")
    line: int = Field(description="1-indexed line number of the symbol.")
    character: int = Field(description="1-indexed character offset within the line.")


class ReferencesInput(BaseModel):
    path: str = Field(description="Absolute path to the file.")
    line: int = Field(description="1-indexed line number of the symbol.")
    character: int = Field(description="1-indexed character offset within the line.")
    include_declaration: bool = Field(default=True, description="Include the declaration in results.")


class HoverInput(BaseModel):
    path: str = Field(description="Absolute path to the file.")
    line: int = Field(description="1-indexed line number.")
    character: int = Field(description="1-indexed character offset.")


class WorkspaceSymbolsInput(BaseModel):
    query: str = Field(description="Symbol name or prefix to search for across the workspace (empty string returns all).")
    workspace_path: str = Field(description="Absolute path to the workspace root directory.")
    language: str = Field(default="python", description="Language hint for server selection (e.g. 'python', 'typescript', 'go').")


class CodeActionsInput(BaseModel):
    path: str = Field(description="Absolute path to the file.")
    line: int = Field(description="1-indexed line of the diagnostic.")
    character: int = Field(description="1-indexed character of the diagnostic.")


class FormatDocumentInput(BaseModel):
    path: str = Field(description="Absolute path to the file to format.")
    tab_size: int = Field(default=4, description="Tab size for formatting.")
    insert_spaces: bool = Field(default=True, description="Use spaces instead of tabs.")
