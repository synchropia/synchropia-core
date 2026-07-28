"""
development/qa/index.py
--------------------
Interactive QA Agent logic.
Builds the QA swarm agent using the Unified LLM Gateway and sandbox harness.

Key additions:
- _persist_sonar_report(): fetches SonarQube measures/gate and writes a
  structured CodeQualityReport row to the database.
- Publishes QUALITY_GATE_PASSED / QUALITY_GATE_FAILED Kafka events after
  every QA run (when a project_key is supplied).
- run_qa() accepts an optional project_key parameter.
"""

import os
import uuid
import json
from typing import Any, Dict, List, Optional
from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.runnables import RunnableConfig
from deepagents import create_deep_agent
from src.core.llm_gateway import unified_llm_gateway
from src.utils.logger import logger
from src.services.semantic_caching import semantic_cache
from src.services.kafka_service import KafkaAgentCallbackHandler, kafka_client
from src.services.database import get_db_session
from src.models.db_models import (
    Organization, Session as DBSession, OrchestrationRun,
    AgentExecution, MessageLog, Artifact, CodeQualityReport,
)
from src.models.event_schemas import EventType
from src.services.sonarqube_testing_service import sonarqube_client
from datetime import datetime, timezone
from langgraph.checkpoint.memory import MemorySaver
memory_checkpointer = MemorySaver()

from .prompt import QA_SYSTEM_PROMPT
from .harness import QAAgentHarness
from src.utils.code_scanner import get_dir_code_contents
from deepagents.middleware._tool_exclusion import _ToolExclusionMiddleware


# ---------------------------------------------------------------------------
# SonarQube persistence helper
# ---------------------------------------------------------------------------

async def _persist_sonar_report(
    run_id: str,
    project_key: str,
) -> Optional[Dict[str, Any]]:
    """
    Fetch the current SonarQube Quality Gate status and measures for
    *project_key*, persist a structured ``CodeQualityReport`` row, and
    return a summary dict suitable for Kafka event payloads.

    Returns None on any error (non-fatal — QA continues regardless).
    """
    try:
        # 1. Parallel async fetch: gate status + component measures
        gate_res = await sonarqube_client.make_async_request(
            "GET",
            "api/qualitygates/project_status",
            params={"projectKey": project_key},
        )
        measures_res = await sonarqube_client.make_async_request(
            "GET",
            "api/measures/component",
            params={
                "component": project_key,
                "metricKeys": (
                    "bugs,vulnerabilities,code_smells,coverage,"
                    "duplicated_lines_density,complexity,cognitive_complexity,"
                    "sqale_debt_ratio,sqale_index,violations,"
                    "reliability_rating,security_rating,sqale_rating"
                ),
            },
        )

        # 2. Parse gate status  (SonarCloud: "OK" | "ERROR" | "WARN" | "NONE")
        gate_status = "UNKNOWN"
        if "projectStatus" in gate_res:
            gate_status = gate_res["projectStatus"].get("status", "UNKNOWN")

        # 3. Parse measures into flat dict
        raw_measures: Dict[str, Any] = {}
        if "component" in measures_res and "measures" in measures_res["component"]:
            for m in measures_res["component"]["measures"]:
                raw_measures[m["metric"]] = m.get("value")

        def _int(key: str) -> Optional[int]:
            v = raw_measures.get(key)
            return int(v) if v is not None else None

        def _float(key: str) -> Optional[float]:
            v = raw_measures.get(key)
            return float(v) if v is not None else None

        # 4. Persist CodeQualityReport row
        try:
            with get_db_session() as db:
                report = CodeQualityReport(
                    run_id=run_id,
                    project_key=project_key,
                    status=gate_status,
                    bugs=_int("bugs"),
                    vulnerabilities=_int("vulnerabilities"),
                    code_smells=_int("code_smells"),
                    coverage=_float("coverage"),
                    duplicated_lines_density=_float("duplicated_lines_density"),
                    raw_measures=raw_measures,
                )
                db.add(report)
                db.commit()
                logger.success(
                    f"CodeQualityReport persisted for {project_key} "
                    f"(gate={gate_status}, run={run_id})"
                )
        except Exception as dbe:
            logger.warning(f"Failed to write CodeQualityReport to DB: {dbe}")

        return {
            "project_key": project_key,
            "gate_status": gate_status,
            "measures": raw_measures,
        }

    except Exception as e:
        logger.warning(f"_persist_sonar_report failed for {project_key}: {e}")
        return None


# ---------------------------------------------------------------------------
# Agent builder
# ---------------------------------------------------------------------------

async def build_qa_agent(
    harness: QAAgentHarness,
    provider: str = "selfhosted",
    org: str = "",
    dir_path: str | None = None,
    **llm_kwargs
) -> Any:
    """
    Builds the QA LangGraph agent using create_deep_agent.
    """
    try:
        models = await unified_llm_gateway.get_models_for_provider(provider)
        resolved_model = "gemma_4"
        llm = unified_llm_gateway.get_chat_model(
            provider_id=provider,
            model_id=resolved_model,
            **llm_kwargs
        )
    except Exception as e:
        logger.warning(f"Failed to fetch models for QA agent from provider {provider}: {e}. Falling back to default.")
        llm = unified_llm_gateway.get_chat_model(provider_id="selfhosted", model_id="gemma_4", **llm_kwargs)

    base_prompt = f"## Github organization = {org}\n\n{QA_SYSTEM_PROMPT}" if org else QA_SYSTEM_PROMPT
    base_prompt += """

### AVAILABLE SYSTEM AND FILESYSTEM TOOLS
- transfer_to_architect: Transfer control to the Architect Agent.
- transfer_to_pm: Transfer control to the PM Agent.
- transfer_to_devops: Transfer control to the DevOps Agent.
- transfer_to_qa: Transfer control to the QA Agent.
- transfer_to_frontend: Transfer control to the Frontend Agent.
- transfer_to_backend: Transfer control to the Backend Agent.
- transfer_to_data_engineer: Transfer control to the Data Engineer Agent.
- transfer_to_data_scientist: Transfer control to the Data Scientist Agent.
- transfer_to_mlops: Transfer control to the MLOps Agent.
- transfer_to_ai: Transfer control to the AI Agent.
- inspect_a_file(path: str): Reads and returns the content of a file.
- execute_python_code(path: str): Executes a Python file and returns output/errors.
- write_code_to_file(path: str, code: str): Writes code to a file, creating directories if needed.
- refactoring_code(refactored_code: str, error_file_path: str): Overwrites a file with refactored code.
- extract_content_within_a_file(path: str): Extracts and returns the content of a file.
- navigate_entire_codebase_given_path(path: str): Lists all files and directories recursively from a path.
- run_uvicorn_and_capture_logs(...): Runs a FastAPI app with Uvicorn and captures logs.
- look_for_directory(path: str): Lists all directories in a given path.
- run_node_js_server(cmd: str, cwd: str, max_lines: int): Runs a Node.js server command and captures logs.
- current_directory(): Returns the current working directory.
- change_directory(path: str): Changes the current working directory.
- error_detection(error: str, path: str): Logs and returns error information for a file.
- generate_project_structure(framework: str, use_case: str): Generates a project folder structure.
- generate_architecture_plan(framework: str, use_case: str, tree_structure: str): Generates an architecture plan.
- generate_folder_creation_script(tree_structure: str): Generates a Python script to create a folder structure.
- generate_file_content(...): Generates code for a specific file based on project context.
- explain_code(path: str): Explains what a code file does.
- run_shell_commands(command: str, cwd: str, timeout: int): Runs a shell command and returns logs. But this is not secure 
- run_shell_command_in_sandbox(command: str, cwd: str = "/", timeout: int = 60): Executes shell commands in a sandboxed environment.
- agent_refactor_code(path: str): Refactors and fixes errors in a Python file.
- create_project_structure_at_path(tree_structure: str, sub_root_dir: str): Creates a project structure at a path.
- look_for_file_or_directory(name: str, root_path: str): Searches for a file or directory by name.
- create_or_delete_file(path: str): Creates or deletes a file at the given path.
- scaffold_and_generate_files(framework: str, use_case: str, project_root: str): Scaffolds a project and generates files.

### SONARQUBE CODE QUALITY TOOLS
- sonarqube_search_projects(organization: str, query: str): Discover all SonarCloud-analysed projects for a GitHub org.
- sonarqube_get_project_status(project_key: str): Get Quality Gate PASSED/FAILED verdict for a project.
- sonarqube_get_project_measures(project_key: str, metric_keys: str): Retrieve detailed metrics including complexity, cognitive complexity, and technical debt.
- sonarqube_get_metric_history(project_key: str, metrics: str, page_size: int): Retrieve historical trend data per metric across analyses.
- sonarqube_list_issues(project_key: str, types: str, severities: str, resolved: bool): List specific open issues with file path and line number.
- sonarqube_list_github_repositories(organization: str, alm_setting: str): List all GitHub repos bound to a SonarCloud organization.

### JIRA TOOLS (incl. ISSUE CREATION for remediation)
- jira_create_issue(project_key, summary, description, issue_type, labels, priority): Create a new Jira ticket for a fix task.
- jira_search_issues(jql): Search existing Jira issues.
- jira_add_comment(issue_key, body): Add a comment to a Jira issue.
- jira_get_issue(issue_key): Retrieve full issue details.
- jira_transition_issue(issue_key, transition): Move an issue to a new status.
"""

    is_local_sandbox = (
        harness.backend is not None
        and harness.backend.__class__.__name__ == "LocalPowerShellBackend"
    )
    if is_local_sandbox and dir_path:
        code_context = get_dir_code_contents(dir_path)
        if code_context:
            base_prompt += f"\n\n### LOCAL SANDBOX CODEBASE CONTEXT\nBelow is the content of the files in the directory '{dir_path}' which you can modify or use for reference:\n\n{code_context}\n"

    agent = create_deep_agent(
        system_prompt=base_prompt,
        backend=harness.backend,
        tools=harness.tools,
        model=llm,
        checkpointer=memory_checkpointer,
        middleware=[
            _ToolExclusionMiddleware(
                excluded=frozenset(["ls", "read_file", "write_file", "edit_file", "glob", "grep"])
            )
        ]
    )
    return agent


# ---------------------------------------------------------------------------
# QA runner
# ---------------------------------------------------------------------------

async def run_qa(
    message: str,
    *,
    provider: str = "selfhosted",
    sandbox_name: str = "qa-sandbox",
    run_id: str | None = None,
    org: str,
    user_id: str = "default_user",
    project_key: Optional[str] = None,
) -> Any:
    """
    Orchestrates the QA Agent execution loop, wrapping it in database logging,
    Kafka event streaming, and semantic caching.

    Parameters
    ----------
    project_key : str, optional
        The SonarCloud project key (e.g. ``'synchropia_synchropia-core'``).
        When provided, a ``CodeQualityReport`` DB row is persisted after the
        run and a ``QUALITY_GATE_PASSED`` or ``QUALITY_GATE_FAILED`` Kafka
        event is published.
    """
    active_run_id = run_id or str(uuid.uuid4())

    # 1. Perform Asynchronous Semantic Cache Lookup
    try:
        cached_result = await semantic_cache.lookup(message)
        if cached_result:
            logger.trace("Semantic Cache HIT for current QA prompt")
            cached_event = {
                "event": "agent_response",
                "source": "qa",
                "text": cached_result,
                "run_id": active_run_id,
                "cached": True
            }
            try:
                await kafka_client.publish("synchropia-agent-responses", cached_event)
            except Exception as ke:
                logger.warning(f"Failed to publish QA cached response to Kafka: {ke}")
                
            try:
                with get_db_session() as db:
                    db_org = db.query(Organization).filter_by(github_org_name=org).first()
                    if not db_org:
                        db_org = Organization(name=org, github_org_name=org)
                        db.add(db_org)
                        db.commit()
                        db.refresh(db_org)

                    db_session = db.query(DBSession).filter_by(id=user_id).first()
                    if not db_session:
                        db_session = DBSession(
                            id=user_id,
                            organization_id=db_org.id,
                            user_id=user_id,
                            title=f"QA Session for {org}"
                        )
                        db.add(db_session)
                        db.commit()
                        db.refresh(db_session)

                    user_msg = MessageLog(session_id=db_session.id, sender="user", message=message)
                    ai_msg = MessageLog(session_id=db_session.id, sender="qa", message=cached_result)
                    db.add(user_msg)
                    db.add(ai_msg)

                    db_run = OrchestrationRun(
                        id=active_run_id,
                        session_id=db_session.id,
                        status="COMPLETED",
                        current_agent="qa"
                    )
                    db.add(db_run)

                    db_execution = AgentExecution(
                        run_id=active_run_id,
                        agent_name="qa",
                        input_payload={"message": message},
                        output_payload={"response": cached_result, "cached": True},
                        status="COMPLETED",
                        completed_at=datetime.now(timezone.utc)
                    )
                    db.add(db_execution)
                    db.commit()
            except Exception as dbe:
                logger.warning(f"Failed to write QA cache hit record to database: {dbe}")

            return {"messages": [{"role": "assistant", "content": cached_result}]}
    except Exception as ce:
        logger.warning(f"Failed to query QA semantic cache: {ce}")

    # 2. Build Agent
    harness = QAAgentHarness(sandbox_name=sandbox_name)
    agent = await build_qa_agent(
        harness=harness,
        provider=provider,
        org=org
    )

    # 3. DB Logging
    execution_id = None
    try:
        with get_db_session() as db:
            db_org = db.query(Organization).filter_by(github_org_name=org).first()
            if not db_org:
                db_org = Organization(name=org, github_org_name=org)
                db.add(db_org)
                db.commit()
                db.refresh(db_org)

            db_session = db.query(DBSession).filter_by(id=user_id).first()
            if not db_session:
                db_session = DBSession(
                    id=user_id,
                    organization_id=db_org.id,
                    user_id=user_id,
                    title=f"QA Session for {org}"
                )
                db.add(db_session)
                db.commit()
                db.refresh(db_session)

            db_run = db.query(OrchestrationRun).filter_by(id=active_run_id).first()
            if not db_run:
                db_run = OrchestrationRun(
                    id=active_run_id,
                    session_id=db_session.id,
                    status="RUNNING",
                    current_agent="qa"
                )
                db.add(db_run)
            else:
                db_run.status = "RUNNING"
                db_run.current_agent = "qa"
                db_run.updated_at = datetime.now(timezone.utc)

            db_execution = AgentExecution(
                run_id=active_run_id,
                agent_name="qa",
                input_payload={"message": message},
                status="RUNNING"
            )
            db.add(db_execution)

            user_msg = MessageLog(session_id=db_session.id, sender="user", message=message)
            db.add(user_msg)
            db.commit()
            execution_id = db_execution.id
    except Exception as dbe:
        logger.warning(f"QA Database pre-execution logging failed: {dbe}")

    # 4. Stream Callbacks
    kafka_handler = KafkaAgentCallbackHandler(
        kafka_client=kafka_client,
        default_agent_name="qa",
        session_id=user_id
    )
    kafka_handler.run_id = active_run_id

    config: RunnableConfig = {
        "callbacks": [kafka_handler],
        "run_id": active_run_id,
        "configurable": {
            "thread_id": user_id
        }
    }

    state_input = {
        "messages": [
            {
                "role": "user",
                "content": message,
            }
        ]
    }

    try:
        response = await agent.ainvoke(state_input, config=config)
        final_text = ""
        if isinstance(response, dict) and "messages" in response:
            last_msg = response["messages"][-1]
            if hasattr(last_msg, "content"):
                final_text = last_msg.content
            elif isinstance(last_msg, dict):
                final_text = last_msg.get("content", str(last_msg))
            else:
                final_text = str(last_msg)
        else:
            final_text = str(response)

        # 5. SonarQube: persist CodeQualityReport + publish quality gate Kafka event
        sonar_result: Optional[Dict[str, Any]] = None
        if project_key:
            sonar_result = await _persist_sonar_report(
                run_id=active_run_id,
                project_key=project_key,
            )

        if sonar_result:
            gate_status = sonar_result.get("gate_status", "UNKNOWN")
            # SonarCloud: "OK" = passed, "ERROR" = failed, "WARN" = warning
            kafka_event_type = (
                EventType.QUALITY_GATE_PASSED
                if gate_status == "OK"
                else EventType.QUALITY_GATE_FAILED
            )
            kafka_payload = {
                "event_id": str(uuid.uuid4()),
                "run_id": active_run_id,
                "session_id": user_id,
                "event_type": kafka_event_type.value,
                "source_agent": "qa",
                "target_agent": "backend" if gate_status != "OK" else None,
                "project_key": project_key,
                "gate_status": gate_status,
                "measures": sonar_result.get("measures", {}),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            try:
                await kafka_client.publish("synchropia-agent-responses", kafka_payload)
                logger.info(
                    f"Published {kafka_event_type.value} event for project "
                    f"'{project_key}' (gate={gate_status})"
                )
            except Exception as ke:
                logger.warning(f"Failed to publish quality gate Kafka event: {ke}")

        # 6. Semantic cache + post-execution log
        if final_text:
            try:
                await semantic_cache.set(message, final_text)
            except Exception as se:
                logger.warning(f"Failed to write to semantic cache: {se}")

        if execution_id:
            try:
                with get_db_session() as db:
                    db_execution = db.query(AgentExecution).filter_by(id=execution_id).first()
                    if db_execution:
                        db_execution.output_payload = {
                            "response": final_text,
                            "sonar_gate_status": sonar_result.get("gate_status") if sonar_result else None,
                        }
                        db_execution.status = "COMPLETED"
                        db_execution.completed_at = datetime.now(timezone.utc)

                    db_run = db.query(OrchestrationRun).filter_by(id=active_run_id).first()
                    if db_run:
                        db_run.status = "COMPLETED"
                        db_run.updated_at = datetime.now(timezone.utc)

                    ai_msg = MessageLog(session_id=user_id, sender="qa", message=final_text)
                    db.add(ai_msg)

                    new_artifact = Artifact(
                        run_id=active_run_id,
                        name="QA Test Results Summary",
                        artifact_type="QA_REPORT",
                        content=final_text
                    )
                    db.add(new_artifact)
                    db.commit()
            except Exception as dbe:
                logger.warning(f"QA Database post-execution logging failed: {dbe}")

        return response
    except Exception as e:
        if execution_id:
            try:
                with get_db_session() as db:
                    db_execution = db.query(AgentExecution).filter_by(id=execution_id).first()
                    if db_execution:
                        db_execution.status = "FAILED"
                        db_execution.completed_at = datetime.now(timezone.utc)
                    db_run = db.query(OrchestrationRun).filter_by(id=active_run_id).first()
                    if db_run:
                        db_run.status = "FAILED"
                        db_run.error_message = str(e)
                        db_run.updated_at = datetime.now(timezone.utc)
                    db.commit()
            except Exception as dbe:
                logger.warning(f"QA Database error logging failed: {dbe}")
        raise
