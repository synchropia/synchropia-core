from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class EventType(str, Enum):
    TOKEN = "token"                               # Live streaming LLM token
    AGENT_START = "agent_start"                   # Agent activated
    AGENT_RESPONSE = "agent_response"             # Agent finished a turn
    AGENT_END = "agent_end"                       # Agent execution ended
    TOOL_CALL = "tool_call"                       # Tool execution initiated
    TOOL_RESPONSE = "tool_response"               # Tool execution succeeded
    TOOL_ERROR = "tool_error"                     # Tool execution failed
    AGENT_ERROR = "agent_error"                   # Agent crashed/errored
    HANDOFF = "handoff"                           # Direct transfer of control
    # ── SonarQube / Quality Gate events ──────────────────────────────────
    QUALITY_GATE_PASSED = "quality_gate_passed"   # SonarQube gate status = OK
    QUALITY_GATE_FAILED = "quality_gate_failed"   # SonarQube gate status = ERROR / WARN
    QA_FIX_REQUESTED = "qa_fix_requested"         # QA agent escalating issues to Backend for remediation


class SwarmEvent(BaseModel):
    event_id: str = Field(description="Unique UUID for this event instance")
    run_id: str = Field(description="Matches the active OrchestrationRun ID")
    session_id: str = Field(description="Matches the active chat/user Session ID")
    event_type: EventType = Field(description="The functional type of the event")

    # "Who Did What" Context
    source_agent: str = Field(description="The agent producing the event (e.g., 'pm', 'architect', 'backend_dev')")
    target_agent: Optional[str] = Field(None, description="The intended recipient agent if this is a handoff")
    tool_name: Optional[str] = Field(None, description="The name of the tool being invoked, populated for TOOL_CALL and TOOL_RESPONSE events")

    # Content & Payloads
    payload: Dict[str, Any] = Field(
        default_factory=dict,
        description="Event-specific data (e.g., {'token': 'import'} or {'tool': 'create_branch', 'args': {...}})"
    )

    error_details: Optional[str] = Field(None, description="Stack trace or error message if status is failed")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Standard UTC timezone-aware generation timestamp"
    )


class QualityGateMeasures(BaseModel):
    """Structured snapshot of SonarQube metric values."""
    bugs: Optional[int] = None
    vulnerabilities: Optional[int] = None
    code_smells: Optional[int] = None
    coverage: Optional[float] = None
    duplicated_lines_density: Optional[float] = None
    complexity: Optional[int] = None
    cognitive_complexity: Optional[int] = None
    sqale_debt_ratio: Optional[float] = None       # Technical debt ratio (%)
    technical_debt: Optional[str] = None           # Human-readable (e.g. "2h 30min")
    security_hotspots: Optional[int] = None
    reliability_rating: Optional[str] = None       # A-E letter rating
    security_rating: Optional[str] = None
    maintainability_rating: Optional[str] = None   # sqale_rating


class QualityIssueSummary(BaseModel):
    """A single SonarQube issue surfaced in an event."""
    key: str
    type: str                # BUG, VULNERABILITY, CODE_SMELL
    severity: str            # BLOCKER, CRITICAL, MAJOR, MINOR, INFO
    message: str
    component: str           # file path within the project
    line: Optional[int] = None
    effort: Optional[str] = None  # remediation effort e.g. "30min"


class QualityGateEvent(SwarmEvent):
    """
    Specialised SwarmEvent for SonarQube quality gate results.

    Produced by the QA Agent after calling sonarqube_get_project_status and
    sonarqube_get_project_measures. Consumed by downstream services, UI, and
    the Backend Agent when fixes are needed.
    """
    project_key: str = Field(description="SonarCloud project key (e.g. 'synchropia_synchropia-core')")
    gate_status: str = Field(description="Raw SonarCloud gate status: 'OK', 'ERROR', 'WARN', or 'NONE'")
    measures: QualityGateMeasures = Field(default_factory=QualityGateMeasures)
    critical_issues: List[QualityIssueSummary] = Field(
        default_factory=list,
        description="Top BLOCKER/CRITICAL issues that caused the gate to fail"
    )
    remediation_jira_keys: List[str] = Field(
        default_factory=list,
        description="Jira ticket keys created for remediation tasks"
    )