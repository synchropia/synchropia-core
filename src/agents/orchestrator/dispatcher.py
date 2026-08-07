"""
orchestrator/swarm_orchestrator.py
--------------------
Multi-Agent Swarm Orchestration Graph with Subgraph Handoffs.
Assembles all swarm agents into a coordinate parent StateGraph.
"""

from anthropic import __name
import asyncio
import uuid
from typing import Annotated, Any, Dict, List, Literal
from typing_extensions import NotRequired, TypedDict
from langchain_core.messages import BaseMessage, AIMessage, ToolMessage,HumanMessage
from langgraph.graph.message import add_messages
from langgraph.graph import StateGraph, START, END
from langchain_core.runnables import RunnableConfig

from src.core.llm_gateway import unified_llm_gateway
from src.utils.logger import logger
from src.services.semantic_caching import semantic_cache
from src.services.kafka_service import KafkaAgentCallbackHandler, kafka_client
from src.services.database import get_db_session
from src.models.db_models import Organization, Session as DBSession, OrchestrationRun, AgentExecution, MessageLog, Artifact
from langgraph.types import interrupt, Command
from langchain_core.messages import HumanMessage
    # Import agent builders and harnesses dynamically
from src.agents.orchestrator.architect.index import build_architect_agent, ArchitectAgentHarness
from src.agents.pm.index import build_pm_agent, PMAgentHarness
from src.agents.development.devops.index import build_devops_agent, DevOpsAgentHarness
from src.agents.development.qa.index import build_qa_agent, QAAgentHarness
from src.agents.development.frontend.index import build_frontend_agent, FrontendAgentHarness
from src.agents.development.backend.index import build_backend_agent, BackendAgentHarness
from src.agents.data_engineering.data_engineer.index import build_data_engineer_agent, DataEngineerAgentHarness
from src.agents.data_engineering.data_scientist.index import build_data_scientist_agent, DataScientistAgentHarness
from src.agents.data_engineering.mlops.index import build_mlops_agent, MLOpsAgentHarness
from src.agents.data_engineering.ai.index import build_ai_agent, AIEngineerAgentHarness
from datetime import datetime, timezone

from langgraph.checkpoint.memory import MemorySaver

memory_checkpointer = MemorySaver()

class SwarmState(TypedDict):
    messages: Annotated[List[BaseMessage], add_messages]
    active_agent: NotRequired[str]
    # ── SonarQube quality gate tracking ──────────────────────────────────
    quality_gate_status: NotRequired[str]   # "OK" | "ERROR" | "WARN" | "NONE" | "UNKNOWN"
    quality_gate_retries: NotRequired[int]  # count of QA→Backend→QA remediation cycles


_MAX_QA_RETRIES = 3  # maximum QA→Backend→QA feedback cycles before escalating to architect


async def build_swarm_orchestrator(
    org: str,
    provider: str = "selfhosted",
    dir_path: str | None = None,
    org_id: str | None = None,
    **llm_kwargs
) -> Any:
    """
    Assembles the Parent StateGraph connecting all 10 specialized agent subgraphs.
    """
    logger.info("Compiling individual subgraphs for Swarm Orchestrator...")

    # 1. Compile subgraphs
    architect_agent = await build_architect_agent(ArchitectAgentHarness(org_id=org_id), provider, org, dir_path=dir_path, **llm_kwargs)
    pm_agent = await build_pm_agent(PMAgentHarness(), provider, org, dir_path=dir_path, **llm_kwargs)
    devops_agent = await build_devops_agent(DevOpsAgentHarness(org_id=org_id), provider, org, dir_path=dir_path, **llm_kwargs)
    qa_agent = await build_qa_agent(QAAgentHarness(), provider, org, dir_path=dir_path, **llm_kwargs)
    frontend_agent = await build_frontend_agent(FrontendAgentHarness(org_id=org_id), provider, org, dir_path=dir_path, **llm_kwargs)
    backend_agent = await build_backend_agent(BackendAgentHarness(org_id=org_id), provider, org, dir_path=dir_path, **llm_kwargs)
    data_engineer_agent = await build_data_engineer_agent(DataEngineerAgentHarness(), provider, org, dir_path=dir_path, **llm_kwargs)
    data_scientist_agent = await build_data_scientist_agent(DataScientistAgentHarness(), provider, org, dir_path=dir_path, **llm_kwargs)
    mlops_agent = await build_mlops_agent(MLOpsAgentHarness(), provider, org, dir_path=dir_path, **llm_kwargs)
    ai_agent = await build_ai_agent(AIEngineerAgentHarness(), provider, org, dir_path=dir_path, **llm_kwargs)

    # Helper to inject agent name into metadata configuration
    def _with_agent_metadata(config: RunnableConfig, agent_name: str) -> RunnableConfig:
        local_config = dict(config or {})
        metadata = dict(local_config.get("metadata") or {})
        metadata["agent_name"] = agent_name
        local_config["metadata"] = metadata
        return local_config

    # 2. Define node wrappers calling child subgraphs
    async def call_architect(state: SwarmState, config: RunnableConfig):
        res = await architect_agent.ainvoke(state, _with_agent_metadata(config, "architect"))
        if isinstance(res, dict):
            res["active_agent"] = "architect"
        return res

    async def call_pm(state: SwarmState, config: RunnableConfig):
        res = await pm_agent.ainvoke(state, _with_agent_metadata(config, "pm"))
        print("PM AGENT RESPONSE: ", res)
        if isinstance(res, dict):
            res["active_agent"] = "pm"
        return res

    async def call_devops(state: SwarmState, config: RunnableConfig):
        res = await devops_agent.ainvoke(state, _with_agent_metadata(config, "devops"))
        if isinstance(res, dict):
            res["active_agent"] = "devops"
        return res

    async def call_qa(state: SwarmState, config: RunnableConfig):
        # Enforce retry cap to prevent infinite QA ↔ Backend loop
        retries = state.get("quality_gate_retries", 0)
        if retries >= _MAX_QA_RETRIES:
            logger.warning(
                f"Quality gate remediation loop hit retry limit ({retries}/{_MAX_QA_RETRIES}). "
                "Escalating to architect."
            )
            return {
                "active_agent": "architect",
                "messages": [
                    AIMessage(
                        content=(
                            f"⚠️ Quality Gate Remediation Limit Reached\n\n"
                            f"The QA→Backend fix cycle has run {retries} times without achieving "
                            f"a passing Quality Gate. Manual intervention is required.\n"
                            f"Last gate status: {state.get('quality_gate_status', 'UNKNOWN')}\n\n"
                            f"Please review the SonarQube dashboard and CodeQualityReport "
                            f"records for this run and decide on next steps."
                        )
                    )
                ],
            }

        res = await qa_agent.ainvoke(state, _with_agent_metadata(config, "qa"))
        if isinstance(res, dict):
            res["active_agent"] = "qa"
        return res

    async def call_frontend(state: SwarmState, config: RunnableConfig):
        res = await frontend_agent.ainvoke(state, _with_agent_metadata(config, "frontend"))
        if isinstance(res, dict):
            res["active_agent"] = "frontend"
        return res

    async def call_backend(state: SwarmState, config: RunnableConfig):
        res = await backend_agent.ainvoke(state, _with_agent_metadata(config, "backend"))
        if isinstance(res, dict):
            res["active_agent"] = "backend"
            # If backend was invoked as part of a QA quality-gate remediation cycle,
            # increment the retry counter so QA knows how many cycles have occurred.
            if state.get("quality_gate_status") in ("ERROR", "WARN"):
                res["quality_gate_retries"] = state.get("quality_gate_retries", 0) + 1
                logger.info(
                    f"QA remediation cycle {res['quality_gate_retries']}/{_MAX_QA_RETRIES} "
                    f"— backend finished, control returning via handoff."
                )
        return res

    async def call_data_engineer(state: SwarmState, config: RunnableConfig):
        res = await data_engineer_agent.ainvoke(state, _with_agent_metadata(config, "data_engineer"))
        if isinstance(res, dict):
            res["active_agent"] = "data_engineer"
        return res

    async def call_data_scientist(state: SwarmState, config: RunnableConfig):
        res = await data_scientist_agent.ainvoke(state, _with_agent_metadata(config, "data_scientist"))
        if isinstance(res, dict):
            res["active_agent"] = "data_scientist"
        return res

    async def call_mlops(state: SwarmState, config: RunnableConfig):
        res = await mlops_agent.ainvoke(state, _with_agent_metadata(config, "mlops"))
        if isinstance(res, dict):
            res["active_agent"] = "mlops"
        return res

    async def call_ai(state: SwarmState, config: RunnableConfig):
        res = await ai_agent.ainvoke(state, _with_agent_metadata(config, "ai"))
        if isinstance(res, dict):
            res["active_agent"] = "ai"
        return res

    # 3. State router configuration
    agent_names = [
        "architect", "pm", "devops", "qa", "frontend",
        "backend"
    ]
    all_routing_targets = agent_names + ["handoff_checkpoint"]

    async def handoff_checkpoint(state: SwarmState, config: RunnableConfig):
     
        # Expose a generic message to mask backend agent names (blackbox)
        user_prompt = interrupt("Please enter your query for the active agent:")
        
        # When resumed, append the user's message and route to the active agent node
        target_agent = state.get("active_agent") or "architect"
        return Command(
            goto=target_agent,
            update={
                "messages": [HumanMessage(content=user_prompt)]
            }
        )

    def route_initial(state: SwarmState) -> str:
        return state.get("active_agent") or "architect"

    def route_after_agent(state: SwarmState) -> str:
        messages = state.get("messages", [])
        active = state.get("active_agent") or "architect"
        last_desc = messages[-1].__class__.__name__ if messages else "None"
        print(f"DEBUG: route_after_agent active_agent={active}, last_msg_type={last_desc}")
        if messages:
            last_msg = messages[-1]
            # Route to handoff_checkpoint if the last message is a handoff tool response
            if isinstance(last_msg, ToolMessage) and last_msg.content and last_msg.content.startswith("Transferred to "):
                print("DEBUG: route_after_agent detecting handoff tool response, routing to handoff_checkpoint")
                return "handoff_checkpoint"
            # If the last message is an AI message and has no pending tool/handoff calls:
            # End the session if the coordinator (architect) returned the final response,
            # otherwise route control back to the architect so the swarm keeps running.
            if isinstance(last_msg, AIMessage) and not last_msg.tool_calls:
                if active == "architect":
                    print("DEBUG: route_after_agent returning END")
                    return "__end__"
                else:
                    print(f"DEBUG: route_after_agent worker '{active}' finished, routing back to architect")
                    return "architect"
        print(f"DEBUG: route_after_agent returning {active}")
        return active

    # 4. Construct parent graph
    builder = StateGraph(SwarmState)
    
    # Add nodes
    builder.add_node("architect", call_architect)
    builder.add_node("pm", call_pm)
    builder.add_node("devops", call_devops)
    builder.add_node("qa", call_qa)
    builder.add_node("frontend", call_frontend)
    builder.add_node("backend", call_backend)
    builder.add_node("handoff_checkpoint", handoff_checkpoint)

    # Start with initial router
    builder.add_conditional_edges(START, route_initial, agent_names)

    # Add routing edges for each node
    for name in all_routing_targets:
        builder.add_conditional_edges(name, route_after_agent, all_routing_targets + [END])

    return builder.compile(checkpointer=memory_checkpointer)

async def run_swarm(
    message: str,
    *,
    provider: str = "selfhosted",
    run_id: str | None = None,
    org: str,
    user_id: str = "default_user",
    resume: bool = False,
    dir_path: str | None = None,
    org_id: str | None = None
) -> Any:
    """
    Invokes the parent Swarm Orchestration Graph with full logging and Kafka streaming.
    """
    active_run_id = run_id or str(uuid.uuid4())

    # 1. Semantic Cache Check
    try:
        cached_result = await semantic_cache.lookup(message)
        if cached_result:
            logger.trace("Semantic Cache HIT for swarm coordinator prompt")
            cached_event = {
                "event": "agent_response",
                "source": "swarm",
                "text": cached_result,
                "run_id": active_run_id,
                "cached": True
            }
            try:
                await kafka_client.publish("synchropia-agent-responses", cached_event)
            except Exception as ke:
                logger.warning(f"Failed to publish swarm cached response to Kafka: {ke}")
                
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
                            title=f"Swarm Session for {org}"
                        )
                        db.add(db_session)
                        db.commit()
                        db.refresh(db_session)

                    user_msg = MessageLog(session_id=db_session.id, sender="user", message=message)
                    ai_msg = MessageLog(session_id=db_session.id, sender="swarm", message=cached_result)
                    db.add(user_msg)
                    db.add(ai_msg)

                    db_run = OrchestrationRun(
                        id=active_run_id,
                        session_id=db_session.id,
                        status="COMPLETED",
                        current_agent="swarm"
                    )
                    db.add(db_run)

                    db_execution = AgentExecution(
                        run_id=active_run_id,
                        agent_name="swarm",
                        input_payload={"message": message},
                        output_payload={"response": cached_result, "cached": True},
                        status="COMPLETED",
                        completed_at=datetime.now(timezone.utc)
                    )
                    db.add(db_execution)
                    db.commit()
            except Exception as dbe:
                logger.warning(f"Failed to write swarm cache hit record to database: {dbe}")

            return {"messages": [{"role": "assistant", "content": cached_result}]}
    except Exception as ce:
        logger.warning(f"Failed to query semantic cache: {ce}")

    # 2. Build parent swarm graph
    agent = await build_swarm_orchestrator(org=org, provider=provider, dir_path=dir_path, org_id=org_id)

    # 3. Log pre-execution parameters
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
                    title=f"Swarm Session for {org}"
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
                    current_agent="swarm"
                )
                db.add(db_run)
            else:
                db_run.status = "RUNNING"
                db_run.current_agent = "swarm"
                db_run.updated_at = datetime.now(timezone.utc)

            db_execution = AgentExecution(
                run_id=active_run_id,
                agent_name="swarm",
                input_payload={"message": message},
                status="RUNNING"
            )
            db.add(db_execution)

            user_msg = MessageLog(session_id=db_session.id, sender="user", message=message)
            db.add(user_msg)
            db.commit()
            execution_id = db_execution.id
    except Exception as dbe:
        logger.warning(f"Swarm Database pre-execution logging failed: {dbe}")

    # 4. Stream Callbacks
    kafka_handler = KafkaAgentCallbackHandler(
        kafka_client=kafka_client,
        default_agent_name="swarm",
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

    if resume:
        from langgraph.types import Command
        state_input = Command(resume=message)
    else:
        # Default input. Do NOT override active_agent so it preserves checkpointer value
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
        
        # Check if the execution has paused due to a handoff checkpoint interrupt
        try:
            state_info = await agent.aget_state(config)
            if state_info.tasks and any(task.interrupts for task in state_info.tasks):
                logger.info("Swarm execution paused at handoff checkpoint. Publishing handoff_interrupted event.")
                interrupted_event = {
                    "event_id": str(uuid.uuid4()),
                    "run_id": active_run_id,
                    "session_id": user_id,
                    "event_type": "handoff_interrupted",
                    "source_agent": "swarm",
                    "target_agent": state_info.values.get("active_agent") if state_info.values else None,
                    "tool_name": None,
                    "payload": {
                        "prompt_message": "Please enter your query for the active agent:"
                    },
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
                await kafka_client.publish("synchropia-agent-responses", interrupted_event)
        except Exception as se:
            logger.warning(f"Failed to check thread state for interrupts: {se}")
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

                    ai_msg = MessageLog(session_id=user_id, sender="swarm", message=final_text)
                    db.add(ai_msg)

                    new_artifact = Artifact(
                        run_id=active_run_id,
                        name="Multi-Agent Execution Output",
                        artifact_type="SWARM_OUTPUT",
                        content=final_text
                    )
                    db.add(new_artifact)
                    db.commit()
            except Exception as dbe:
                logger.warning(f"Swarm Database post-execution logging failed: {dbe}")

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
                logger.warning(f"Swarm Database error logging failed: {dbe}")
        raise

async def run_test():
    run_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    
    swarm_graph = await build_swarm_orchestrator(org="Synchropia")

    messages = [HumanMessage(content="Hello , can u transfer the handoff only to a PM Agent , I want to talk to it")]
    state = await swarm_graph.ainvoke({"messages": messages}, config={"configurable": {"thread_id": session_id}})

    for message in state['messages']:
        if message.type == 'ai':
            print("AI:", message.content)

if __name__ == "__main__":
    asyncio.run(run_test())


    