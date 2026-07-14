"""
utils/handoffs.py
--------------------
Unified handoff tools to transfer control between agents in the multi-agent swarm.
Uses the Command.PARENT mechanism to navigate between graph nodes.
Supports passing instructions or query payloads between agents during control transfers.
"""

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool
from langchain.tools import ToolRuntime
from langgraph.types import Command
from src.utils.logger import logger
import json

def _create_transfer_command(agent_name: str, instructions: str, runtime: ToolRuntime) -> Command:
    """Helper to construct the handoff Command with custom instructions."""
    source_agent = "unknown_agent"
    if runtime.config and "metadata" in runtime.config:
        source_agent = runtime.config["metadata"].get("agent_name") or source_agent
    if source_agent == "unknown_agent" and "active_agent" in runtime.state:
        source_agent = runtime.state["active_agent"]

    logger.info(f"Agent Handoff: '{source_agent}' transferring conversation control to '{agent_name}' agent.")
    if instructions:
        logger.info(f"Handoff Instructions: {instructions}")
    
    # Find the last AI message that triggered the tool call
    last_ai_message = next(
        (msg for msg in reversed(runtime.state["messages"]) if isinstance(msg, AIMessage)),
        None
    )
    if not last_ai_message:
        # Fallback to creating a mock AI message if none exists
        last_ai_message = AIMessage(content=f"Initiating transfer to {agent_name}")
    
    content = f"Transferred to {agent_name} agent."
    if instructions:
        content += f" Specific instructions or task payload from the referring agent: '{instructions}'."
    else:
        content += f" The {agent_name} agent is now active and must respond to address the user's requirements."

    transfer_message = ToolMessage(
        content=content,
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
def transfer_to_architect(instructions: str = "", runtime: ToolRuntime = None) -> Command:
    """Handoff the conversation to the Systems Architect Agent.
    
    Use this tool when you need the Architect to create database schemas, system topology layouts, 
    Mermaid blueprints, RESTful/gRPC API specifications, or perform high-level incident triage.
    
    Args:
        instructions: Specific design constraints, requirements, or queries detailing what the Architect Agent should analyze or build next.
    """
    return _create_transfer_command("architect", instructions, runtime)

@tool
def transfer_to_pm(instructions: str = "", runtime: ToolRuntime = None) -> Command:
    """Handoff the conversation to the Product Manager (PM) Agent.
    
    Use this tool when you need the PM to coordinate planning stages, ingest vague user requests, 
    verify database state configurations, update Jira tickets, or draft Markdown PRD/SRS documentation.
    
    Args:
        instructions: Requirements specifications or instructions for the PM to act on (e.g. what issue to update or document to compile).
    """
    return _create_transfer_command("pm", instructions, runtime)

@tool
def transfer_to_devops(instructions: str = "", runtime: ToolRuntime = None) -> Command:
    """Handoff the conversation to the DevOps Agent.
    
    Use this tool when you need to construct CI/CD pipelines, execute Docker container setups, 
    configure repository deployments, or provision infrastructure using IaC scripts.
    
    Args:
        instructions: DevOps specific context (e.g., target environment config, Docker setup requirements, or deployment specs).
    """
    return _create_transfer_command("devops", instructions, runtime)

@tool
def transfer_to_qa(instructions: str = "", runtime: ToolRuntime = None) -> Command:
    """Handoff the conversation to the Quality Assurance (QA) Agent.
    
    Use this tool to trigger test execution suites, review code coverage reports, run unit/integration 
    validations, or verify that backend/frontend features meet functional requirements specifications.
    
    Args:
        instructions: Directives explaining which files to validate, which test suites to execute, or requirements metrics to verify.
    """
    return _create_transfer_command("qa", instructions, runtime)

@tool
def transfer_to_frontend(instructions: str = "", runtime: ToolRuntime = None) -> Command:
    """Handoff the conversation to the Frontend specialist Agent.
    
    Use this tool when you need to adjust user interface logic, build React components, 
    tweak styling stylesheets, or integrate front-end routes with API gateway endpoints.
    
    Args:
        instructions: Visual descriptions, layout updates, CSS tweaks, or mock API responses that the Frontend Agent should implement.
    """
    return _create_transfer_command("frontend", instructions, runtime)

@tool
def transfer_to_backend(instructions: str = "", runtime: ToolRuntime = None) -> Command:
    """Handoff the conversation to the Backend specialist Agent.
    
    Use this tool when you need to write API endpoints, implement data validation logic, 
    construct SQL query scripts, update routing controllers, or create database models.
    
    Args:
        instructions: API specifications, payload criteria, database column constraints, or controller logic details to implement.
    """
    return _create_transfer_command("backend", instructions, runtime)

@tool
def transfer_to_data_engineer(instructions: str = "", runtime: ToolRuntime = None) -> Command:
    """Handoff the conversation to the Data Engineer Agent.
    
    Use this tool when you need to write ELT/ETL pipeline scripts, run migration commands, 
    optimize SQL indexes, build database data warehouses, or manage streaming pipelines.
    
    Args:
        instructions: Data ingestion details, source-target mapping schemas, or SQL table optimization tasks to coordinate.
    """
    return _create_transfer_command("data_engineer", instructions, runtime)

@tool
def transfer_to_data_scientist(instructions: str = "", runtime: ToolRuntime = None) -> Command:
    """Handoff the conversation to the Data Scientist Agent.
    
    Use this tool to conduct exploratory data analysis, clean structured datasets, generate 
    predictive models, run python analysis scripts, or render data charts and visual plots.
    
    Args:
        instructions: Data parameters, query details, Jupyter notebook actions, or forecasting tasks to configure.
    """
    return _create_transfer_command("data_scientist", instructions, runtime)

@tool
def transfer_to_mlops(instructions: str = "", runtime: ToolRuntime = None) -> Command:
    """Handoff the conversation to the MLOps Agent.
    
    Use this tool to register trained weights in the model registry, deploy containerized prediction 
    endpoints, configure MLflow servers, or manage model performance monitoring.
    
    Args:
        instructions: Model paths, registry tags, MLflow configurations, or metrics verification specs for the MLOps Agent.
    """
    return _create_transfer_command("mlops", instructions, runtime)

@tool
def transfer_to_ai(instructions: str = "", runtime: ToolRuntime = None) -> Command:
    """Handoff the conversation to the AI Engineer Agent.
    
    Use this tool to adjust vector embedding parameters, fine-tune prompt setups, test semantic caches, 
    configure LLM gateways, or run model response evaluations.
    
    Args:
        instructions: Specific prompt modifications, semantic cache criteria, or LLM response evaluations to trigger.
    """
    return _create_transfer_command("ai", instructions, runtime)

# Expose a list of transfer tools for easy configuration
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
