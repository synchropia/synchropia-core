"""
development/frontend/index.py
--------------------
Interactive Frontend Agent logic.
Builds the Frontend swarm agent using the Unified LLM Gateway and sandbox harness.
"""

import os
import uuid
from typing import Any, Dict, Optional
from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.runnables import RunnableConfig
from deepagents import create_deep_agent
from src.core.llm_gateway import unified_llm_gateway
from src.utils.logger import logger
from src.services.semantic_caching import semantic_cache
from src.services.kafka_service import KafkaAgentCallbackHandler, kafka_client
from src.services.database import get_db_session
from src.models.db_models import Organization, Session as DBSession, OrchestrationRun, AgentExecution, MessageLog, Artifact
from datetime import datetime, timezone

from langgraph.checkpoint.memory import MemorySaver
memory_checkpointer = MemorySaver()

from .prompt import FRONTEND_SYSTEM_PROMPT
from .harness import FrontendAgentHarness

async def build_frontend_agent(
    harness: FrontendAgentHarness,
    provider: str = "selfhosted",
    org: str = "",
    **llm_kwargs
) -> Any:
    """
    Builds the Frontend LangGraph agent using create_deep_agent.
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
        logger.warning(f"Failed to fetch models for Frontend agent from provider {provider}: {e}. Falling back to default.")
        llm = unified_llm_gateway.get_chat_model(provider_id="selfhosted", model_id="gemma_4", **llm_kwargs)

    # Base prompt mapping
    base_prompt = f"## Github organization = {org}\n\n{FRONTEND_SYSTEM_PROMPT}" if org else FRONTEND_SYSTEM_PROMPT

    agent = create_deep_agent(
        system_prompt=base_prompt,
        backend=harness.backend,
        tools=harness.tools,
        model=llm,
        checkpointer=memory_checkpointer,
    )
    return agent

async def run_frontend(
    message: str,
    *,
    provider: str = "selfhosted",
    sandbox_name: str = "frontend-sandbox",
    run_id: str | None = None,
    org: str,
    user_id: str = "default_user"
) -> Any:
    """
    Orchestrates the Frontend Agent execution loop, wrapping it in database logging,
    Kafka event streaming, and semantic caching.
    """
    active_run_id = run_id or str(uuid.uuid4())

    # 1. Perform Asynchronous Semantic Cache Lookup
    try:
        cached_result = await semantic_cache.lookup(message)
        if cached_result:
            logger.trace("Semantic Cache HIT for current Frontend prompt")
            cached_event = {
                "event": "agent_response",
                "source": "frontend",
                "text": cached_result,
                "run_id": active_run_id,
                "cached": True
            }
            try:
                await kafka_client.publish("synchropia-agent-responses", cached_event)
            except Exception as ke:
                logger.warning(f"Failed to publish Frontend cached response to Kafka: {ke}")
                
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
                            title=f"Frontend Session for {org}"
                        )
                        db.add(db_session)
                        db.commit()
                        db.refresh(db_session)

                    user_msg = MessageLog(session_id=db_session.id, sender="user", message=message)
                    ai_msg = MessageLog(session_id=db_session.id, sender="frontend", message=cached_result)
                    db.add(user_msg)
                    db.add(ai_msg)

                    db_run = OrchestrationRun(
                        id=active_run_id,
                        session_id=db_session.id,
                        status="COMPLETED",
                        current_agent="frontend"
                    )
                    db.add(db_run)

                    db_execution = AgentExecution(
                        run_id=active_run_id,
                        agent_name="frontend",
                        input_payload={"message": message},
                        output_payload={"response": cached_result, "cached": True},
                        status="COMPLETED",
                        completed_at=datetime.now(timezone.utc)
                    )
                    db.add(db_execution)
                    db.commit()
            except Exception as dbe:
                logger.warning(f"Failed to write Frontend cache hit record to database: {dbe}")

            return {"messages": [{"role": "assistant", "content": cached_result}]}
    except Exception as ce:
        logger.warning(f"Failed to query Frontend semantic cache: {ce}")

    # 2. Build Agent
    harness = FrontendAgentHarness(sandbox_name=sandbox_name)
    agent = await build_frontend_agent(
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
                    title=f"Frontend Session for {org}"
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
                    current_agent="frontend"
                )
                db.add(db_run)
            else:
                db_run.status = "RUNNING"
                db_run.current_agent = "frontend"
                db_run.updated_at = datetime.now(timezone.utc)

            db_execution = AgentExecution(
                run_id=active_run_id,
                agent_name="frontend",
                input_payload={"message": message},
                status="RUNNING"
            )
            db.add(db_execution)

            user_msg = MessageLog(session_id=db_session.id, sender="user", message=message)
            db.add(user_msg)
            db.commit()
            execution_id = db_execution.id
    except Exception as dbe:
        logger.warning(f"Frontend Database pre-execution logging failed: {dbe}")

    # 4. Stream Callbacks
    kafka_handler = KafkaAgentCallbackHandler(
        kafka_client=kafka_client,
        default_agent_name="frontend",
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

        # 5. Caching and post execution log
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
                        db_execution.output_payload = {"response": final_text}
                        db_execution.status = "COMPLETED"
                        db_execution.completed_at = datetime.now(timezone.utc)

                    db_run = db.query(OrchestrationRun).filter_by(id=active_run_id).first()
                    if db_run:
                        db_run.status = "COMPLETED"
                        db_run.updated_at = datetime.now(timezone.utc)

                    ai_msg = MessageLog(session_id=user_id, sender="frontend", message=final_text)
                    db.add(ai_msg)

                    new_artifact = Artifact(
                        run_id=active_run_id,
                        name="Frontend UI Codebase File",
                        artifact_type="FRONTEND_UI",
                        content=final_text
                    )
                    db.add(new_artifact)
                    db.commit()
            except Exception as dbe:
                logger.warning(f"Frontend Database post-execution logging failed: {dbe}")

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
                logger.warning(f"Frontend Database error logging failed: {dbe}")
        raise
