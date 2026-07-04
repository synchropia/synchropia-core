from datetime import datetime, timezone
import uuid
from typing import List, Optional, Dict, Any
from sqlalchemy import String, ForeignKey, DateTime, Text, JSON, Integer, Boolean, Index, Float
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    """Base class for all SQLAlchemy 2.0 models."""
    pass

class Organization(Base):
    """Represents a tenant/workspace (e.g., GitHub Org or Enterprise Client)."""
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(50), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    github_org_name: Mapped[str] = mapped_column(String(100), nullable=False)
    jira_project_key: Mapped[str] = mapped_column(String(10), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    sessions: Mapped[List["Session"]] = relationship("Session", back_populates="organization", cascade="all, delete-orphan")

class Session(Base):
    """A user conversation/interaction thread."""
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(50), primary_key=True, default=lambda: str(uuid.uuid4()))
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False)  # Creator ID
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    organization: Mapped["Organization"] = relationship("Organization", back_populates="sessions")
    runs: Mapped[List["OrchestrationRun"]] = relationship("OrchestrationRun", back_populates="session", cascade="all, delete-orphan")
    messages: Mapped[List["MessageLog"]] = relationship("MessageLog", back_populates="session", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_session_org", "organization_id"),
        Index("idx_session_user", "user_id"),
    )

class OrchestrationRun(Base):
    """Tracks a single orchestration cycle executed by the PM agent (e.g., 'Draft & Deploy Auth Flow')."""
    __tablename__ = "orchestration_runs"

    id: Mapped[str] = mapped_column(String(50), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="PENDING")  # PENDING, RUNNING, COMPLETED, FAILED
    current_agent: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # e.g., 'pm', 'architect'
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    session: Mapped["Session"] = relationship("Session", back_populates="runs")
    agent_executions: Mapped[List["AgentExecution"]] = relationship("AgentExecution", back_populates="run", cascade="all, delete-orphan")
    artifacts: Mapped[List["Artifact"]] = relationship("Artifact", back_populates="run", cascade="all, delete-orphan")
    code_quality_reports: Mapped[List["CodeQualityReport"]] = relationship("CodeQualityReport", back_populates="run", cascade="all, delete-orphan")

class AgentExecution(Base):
    """Logs individual agent lifecycle execution blocks within an Orchestration Run."""
    __tablename__ = "agent_executions"

    id: Mapped[str] = mapped_column(String(50), primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id: Mapped[str] = mapped_column(ForeignKey("orchestration_runs.id", ondelete="CASCADE"), nullable=False)
    agent_name: Mapped[str] = mapped_column(String(50), nullable=False)  # e.g., 'pm', 'architect', 'backend_dev'
    input_payload: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False)
    output_payload: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="RUNNING")  # RUNNING, COMPLETED, FAILED
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    run: Mapped["OrchestrationRun"] = relationship("OrchestrationRun", back_populates="agent_executions")
    tool_calls: Mapped[List["ToolCall"]] = relationship("ToolCall", back_populates="agent_execution", cascade="all, delete-orphan")

class ToolCall(Base):
    """Tracks every tool executed by any agent (essential for MLflow validation)."""
    __tablename__ = "tool_calls"

    id: Mapped[str] = mapped_column(String(50), primary_key=True, default=lambda: str(uuid.uuid4()))
    agent_execution_id: Mapped[str] = mapped_column(ForeignKey("agent_executions.id", ondelete="CASCADE"), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    arguments: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False)
    response_payload: Mapped[Optional[Text]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="SUCCESS")  # SUCCESS, ERROR
    execution_time_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    agent_execution: Mapped["AgentExecution"] = relationship("AgentExecution", back_populates="tool_calls")

class Artifact(Base):
    """Stores intermediate documents (e.g., SRS, OpenAPI Blueprints, DB migration scripts)."""
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(50), primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id: Mapped[str] = mapped_column(ForeignKey("orchestration_runs.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)  # e.g., 'PRD.md', 'schema.sql'
    artifact_type: Mapped[str] = mapped_column(String(50), nullable=False)  # PRD, SYSTEM_ARCH, CODE_PATCH
    content: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    run: Mapped["OrchestrationRun"] = relationship("OrchestrationRun", back_populates="artifacts")

class MessageLog(Base):
    """Immutable log of conversational steps inside a session."""
    __tablename__ = "message_logs"

    id: Mapped[str] = mapped_column(String(50), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False)
    sender: Mapped[str] = mapped_column(String(50), nullable=False)  # 'user', 'pm', 'architect', 'backend_dev', 'system'
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    session: Mapped["Session"] = relationship("Session", back_populates="messages")


class CodeQualityReport(Base):
    """Stores code quality metrics and Quality Gate status retrieved by the QA agent from SonarQube."""
    __tablename__ = "code_quality_reports"

    id: Mapped[str] = mapped_column(String(50), primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id: Mapped[str] = mapped_column(ForeignKey("orchestration_runs.id", ondelete="CASCADE"), nullable=False)
    project_key: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)  # PASSED, FAILED, WARN
    bugs: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    vulnerabilities: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    code_smells: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    coverage: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    duplicated_lines_density: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    raw_measures: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    run: Mapped["OrchestrationRun"] = relationship("OrchestrationRun", back_populates="code_quality_reports")