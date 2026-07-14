"""
utils/handoffs.py
--------------------
Unified handoff tools to transfer control between agents in the multi-agent swarm.
Uses the Command.PARENT mechanism to navigate between graph nodes.
"""

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool
from langchain.tools import ToolRuntime
from langgraph.types import Command
from src.utils.logger import logger

def _create_transfer_command(agent_name: str, runtime: ToolRuntime) -> Command:
    """Helper to construct the handoff Command."""
    source_agent = "unknown_agent"
    if runtime.config and "metadata" in runtime.config:
        source_agent = runtime.config["metadata"].get("agent_name") or source_agent
    if source_agent == "unknown_agent" and "active_agent" in runtime.state:
        source_agent = runtime.state["active_agent"]

    logger.info(f"Agent Handoff: '{source_agent}' transferring conversation control to '{agent_name}' agent.")
    
    # Find the last AI message that triggered the tool call
    last_ai_message = next(
        (msg for msg in reversed(runtime.state["messages"]) if isinstance(msg, AIMessage)),
        None
    )
    if not last_ai_message:
        # Fallback to creating a mock AI message if none exists
        last_ai_message = AIMessage(content=f"Initiating transfer to {agent_name}")
    transfer_message = ToolMessage(
        content=f"Transferred to {agent_name} agent.",
        tool_call_id=runtime.tool_call_id,
    )

    return Command(
        goto=agent_name,
        update={
            "active_agent": agent_name,
            "messages": [last_ai_message, transfer_message],
        },
        graph=Command.PARENT,
    )

@tool
def transfer_to_architect(runtime: ToolRuntime) -> Command:
    """Handoff the conversation to the Architect Agent for system design, high-level coordination, or incident triage."""
    return _create_transfer_command("architect", runtime)

@tool
def transfer_to_pm(runtime: ToolRuntime) -> Command:
    """Handoff the conversation to the Product Manager (PM) Agent to collect requirements, draft PRDs/SRSs, or manage planning stages."""
    return _create_transfer_command("pm", runtime)

@tool
def transfer_to_devops(runtime: ToolRuntime) -> Command:
    """Handoff the conversation to the DevOps Agent to build CI/CD pipelines, configure environments, or provision AWS infrastructure via OpenTofu/Terraform."""
    return _create_transfer_command("devops", runtime)

@tool
def transfer_to_qa(runtime: ToolRuntime) -> Command:
    """Handoff the conversation to the QA Agent to execute verification suites, check test coverage, or verify requirements compliance."""
    return _create_transfer_command("qa", runtime)

@tool
def transfer_to_frontend(runtime: ToolRuntime) -> Command:
    """Handoff the conversation to the Frontend Agent to edit React/FastAPI pages, manage styling, or adjust user interface code."""
    return _create_transfer_command("frontend", runtime)

@tool
def transfer_to_backend(runtime: ToolRuntime) -> Command:
    """Handoff the conversation to the Backend Agent to update API logic, adjust database schemas, or implement service endpoints."""
    return _create_transfer_command("backend", runtime)

@tool
def transfer_to_data_engineer(runtime: ToolRuntime) -> Command:
    """Handoff the conversation to the Data Engineer Agent to construct ETL/ELT pipelines, configure database integration flows, or run migrations."""
    return _create_transfer_command("data_engineer", runtime)

@tool
def transfer_to_data_scientist(runtime: ToolRuntime) -> Command:
    """Handoff the conversation to the Data Scientist Agent to conduct exploratory data analysis, run Jupyter notebooks, or perform predictive modeling."""
    return _create_transfer_command("data_scientist", runtime)

@tool
def transfer_to_mlops(runtime: ToolRuntime) -> Command:
    """Handoff the conversation to the MLOps Agent to register trained models, monitor metrics drift, or deploy containerized endpoints via MLflow."""
    return _create_transfer_command("mlops", runtime)

@tool
def transfer_to_ai(runtime: ToolRuntime) -> Command:
    """Handoff the conversation to the AI Engineer Agent to optimize prompt templates, evaluate LLM outputs, or adjust vector semantic cache parameters."""
    return _create_transfer_command("ai", runtime)

# Expose a dictionary of transfer tools for easy configuration
HANDOFF_TOOLS = [
    transfer_to_architect,
    transfer_to_pm,
    transfer_to_devops,
    transfer_to_qa,
    transfer_to_frontend,
    transfer_to_backend,
    transfer_to_data_engineer,
    transfer_to_data_scientist,
    transfer_to_mlops,
    transfer_to_ai,
]
