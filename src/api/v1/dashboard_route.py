"""
routes/dashboard_route.py
-------------------------
FastAPI endpoints for the web dashboard: recent runs and aggregated stats.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, distinct

from src.deps.auth import get_current_user
from src.models.db_models import (
    Session,
    OrchestrationRun,
    AgentExecution,
    ToolCall,
    Artifact,
    CodeQualityReport,
)
from src.services.database import db_engine

router = APIRouter(prefix="/api/v1")


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


@router.get("/runs", tags=["Dashboard"])
def list_runs(
    limit: int = 50,
    # current_user: dict = Depends(get_current_user),
):
    """
    Recent orchestration runs with their session titles, newest first.
    """
    with db_engine.connect() as conn:
        rows = conn.execute(
            select(
                OrchestrationRun.id,
                OrchestrationRun.status,
                OrchestrationRun.current_agent,
                OrchestrationRun.error_message,
                OrchestrationRun.created_at,
                OrchestrationRun.updated_at,
                Session.title,
                func.count(distinct(AgentExecution.id)).label("agent_count"),
            )
            .join(Session, Session.id == OrchestrationRun.session_id)
            .outerjoin(
                AgentExecution,
                AgentExecution.run_id == OrchestrationRun.id,
            )
            .group_by(
                OrchestrationRun.id,
                Session.title,
            )
            .order_by(OrchestrationRun.created_at.desc())
            .limit(limit)
        ).mappings().all()

    return [
        {
            "id": r["id"],
            "status": r["status"],
            "current_agent": r["current_agent"],
            "error_message": r["error_message"],
            "title": r["title"],
            "agent_count": r["agent_count"] or 0,
            "created_at": _iso(r["created_at"]),
            "updated_at": _iso(r["updated_at"]),
        }
        for r in rows
    ]


@router.get("/dashboard/stats", tags=["Dashboard"])
def dashboard_stats(
    days: int = 14,
    # current_user: dict = Depends(get_current_user),
):
    """
    Aggregated metrics for the dashboard: run status distribution,
    runs over time, tool-call totals, and code quality snapshots.
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)

    with db_engine.connect() as conn:
        # Total + status distribution for all runs
        status_counts = dict(
            conn.execute(
                select(OrchestrationRun.status, func.count(OrchestrationRun.id))
                .group_by(OrchestrationRun.status)
            ).all()
        )

        # Runs grouped by day (last `days` days)
        run_days = conn.execute(
            select(
                func.date(OrchestrationRun.created_at).label("day"),
                func.count(OrchestrationRun.id),
            )
            .where(OrchestrationRun.created_at >= since)
            .group_by(func.date(OrchestrationRun.created_at))
            .order_by(func.date(OrchestrationRun.created_at))
        ).all()

        # Tool call totals
        tool_stats = dict(
            conn.execute(
                select(ToolCall.status, func.count(ToolCall.id))
                .group_by(ToolCall.status)
            ).all()
        )

        # Recent quality reports
        quality_rows = conn.execute(
            select(
                CodeQualityReport.project_key,
                CodeQualityReport.status,
                CodeQualityReport.bugs,
                CodeQualityReport.vulnerabilities,
                CodeQualityReport.code_smells,
                CodeQualityReport.coverage,
                CodeQualityReport.created_at,
            )
            .order_by(CodeQualityReport.created_at.desc())
            .limit(10)
        ).mappings().all()

        # Agent execution counts
        agent_stats = dict(
            conn.execute(
                select(AgentExecution.status, func.count(AgentExecution.id))
                .group_by(AgentExecution.status)
            ).all()
        )

    total_runs = sum(status_counts.values())
    total_tool_calls = sum(tool_stats.values())

    # Fill every day in the window so the chart has a continuous axis
    run_series = {d[0]: d[1] for d in run_days}
    day_series = []
    for i in range(days):
        day = (since + timedelta(days=i)).date()
        day_series.append({"date": day.isoformat(), "runs": int(run_series.get(day, 0))})

    return {
        "summary": {
            "total_runs": total_runs,
            "total_tool_calls": total_tool_calls,
            "total_agents": sum(agent_stats.values()),
            "active_runs": status_counts.get("RUNNING", 0) + status_counts.get("PENDING", 0),
        },
        "runs_by_status": {
            "PENDING": status_counts.get("PENDING", 0),
            "RUNNING": status_counts.get("RUNNING", 0),
            "COMPLETED": status_counts.get("COMPLETED", 0),
            "FAILED": status_counts.get("FAILED", 0),
        },
        "runs_by_day": day_series,
        "tool_calls": {
            "SUCCESS": tool_stats.get("SUCCESS", 0),
            "ERROR": tool_stats.get("ERROR", 0),
            "total": total_tool_calls,
        },
        "agents_by_status": {
            "RUNNING": agent_stats.get("RUNNING", 0),
            "COMPLETED": agent_stats.get("COMPLETED", 0),
            "FAILED": agent_stats.get("FAILED", 0),
        },
        "quality": [
            {
                "project_key": r["project_key"],
                "status": r["status"],
                "bugs": r["bugs"],
                "vulnerabilities": r["vulnerabilities"],
                "code_smells": r["code_smells"],
                "coverage": r["coverage"],
                "created_at": _iso(r["created_at"]),
            }
            for r in quality_rows
        ],
    }
