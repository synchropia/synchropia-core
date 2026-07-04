from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field

class EventType(str, Enum):
    TOKEN = "token"                           # Live streaming LLM token
    AGENT_START = "agent_start"               # Agent activated
    AGENT_RESPONSE = "agent_response"         # Agent finished a turn
    AGENT_END = "agent_end"                   # Agent execution ended
    TOOL_CALL = "tool_call"                   # Tool execution initiated
    TOOL_RESPONSE = "tool_response"           # Tool execution succeeded
    TOOL_ERROR = "tool_error"                 # Tool execution failed
    AGENT_ERROR = "agent_error"               # Agent crashed/errored
    HANDOFF = "handoff"                       # Direct transfer of control

class SwarmEvent(BaseModel):
    event_id: str = Field(description="Unique UUID for this event instance")
    run_id: str = Field(description="Matches the active OrchestrationRun ID")
    session_id: str = Field(description="Matches the active chat/user Session ID")
    event_type: EventType = Field(description="The functional type of the event")
    
    # "Who Did What" Context
    source_agent: str = Field(description="The agent producing the event (e.g., 'pm', 'architect', 'backend_dev')")
    target_agent: Optional[str] = Field(None, description="The intended recipient agent if this is a handoff")
    
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