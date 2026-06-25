"""
architect/index.py
------------------
Entry-point for the Architect agent.

Responsibilities:
  1. Create the harness (sandbox + tools).
  2. Initialise the deep agent via create_deep_agent.
  3. Wire up guardrails and the middleware pipeline.
  4. Expose run_architect() as the single public coroutine callers invoke.
"""

from __future__ import annotations

import asyncio
from typing import Any

from deepagents import create_deep_agent

from src.core.llm_gateway import unified_llm_gateway
from src.utils.logger import logger

from src.agents.architect.prompt import ARCHITECT_SYSTEM_PROMPT
from src.agents.architect.harness import ArchitectAgentHarness
from src.agents.architect.guardrails import input_guardrail, output_guardrail
from src.agents.architect.middleware import (
    AgentRequest,
    AgentResponse,
    MiddlewarePipeline,
    MetadataInjectionMiddleware,
    LoggingMiddleware,
    RateLimitMiddleware,
    TokenBudgetMiddleware,
    RetryMiddleware,
)


# ---------------------------------------------------------------------------
# Middleware pipeline (can be customised per-deployment)
# ---------------------------------------------------------------------------

architect_pipeline = MiddlewarePipeline([
    MetadataInjectionMiddleware(static_metadata={"agent": "architect"}),
    LoggingMiddleware(),
    RateLimitMiddleware(max_rpm=30),
    TokenBudgetMiddleware(max_input_tokens=8_192),
    RetryMiddleware(max_retries=2),
])


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------

def build_architect_agent(
    harness: ArchitectAgentHarness,
    provider: str = "groq",
    model_id: str | None = None,
):
    """
    Construct a deep agent bound to the harness backend and tool-list.

    Parameters
    ----------
    harness:
        Initialised ArchitectAgentHarness (provides .backend and .tools).
    provider:
        LLM provider key recognised by UnifiedLLMGateway (default: groq).
    model_id:
        Specific model string; defaults to a sensible fallback per provider.
    """
    default_models = {
        "groq": "llama-3.3-70b-versatile",
        "google": "gemini-2.5-flash",
        "mistral": "mistral-large-latest",
        "cerebras": "llama3.1-70b",
        "openrouter": "openai/gpt-4o",
        "nvidia": "meta/llama-3.1-70b-instruct",
        "sambanova": "Meta-Llama-3.1-70B-Instruct",
    }
    resolved_model = model_id or default_models.get(provider, "llama-3.3-70b-versatile")

    agent = create_deep_agent(
        system_prompt=ARCHITECT_SYSTEM_PROMPT,
        backend=harness.backend,
        tools=harness.tools,
        # Pass the provider + model via metadata so the gateway can route calls
        model=resolved_model,
    )
    return agent, provider, resolved_model


# ---------------------------------------------------------------------------
# Core invocation helper
# ---------------------------------------------------------------------------

async def _invoke_agent(agent, provider: str, model_id: str, request: AgentRequest) -> str:
    """
    Thin wrapper that calls the deep agent and routes through the
    UnifiedLLMGateway for token streaming + semantic caching.
    """
    # build a composite prompt: system context is already baked into the agent
    response_text = await unified_llm_gateway.generate_response(
        provider_id=provider,
        model_id=model_id,
        prompt=request.message,
        run_id=request.run_id,
    )
    return response_text


# ---------------------------------------------------------------------------
# Public entry-point
# ---------------------------------------------------------------------------

async def run_architect(
    message: str,
    *,
    provider: str = "groq",
    model_id: str | None = None,
    sandbox_name: str = "architect-sandbox",
    extra_metadata: dict[str, Any] | None = None,
) -> AgentResponse:
    """
    High-level coroutine to invoke the Architect agent end-to-end.

    Flow:
      1.  Input guardrail check
      2.  Initialise harness (sandbox + tools)
      3.  Build deep agent
      4.  Execute through middleware pipeline
      5.  Output guardrail check
      6.  Return AgentResponse

    Parameters
    ----------
    message:
        User message / task description for the architect.
    provider:
        LLM provider (groq | google | mistral | cerebras | openrouter | …).
    model_id:
        Override model; leave None to use the provider default.
    sandbox_name:
        Logical name for the execution sandbox session.
    extra_metadata:
        Arbitrary key/value metadata forwarded through the pipeline.

    Returns
    -------
    AgentResponse
        Contains the final response text plus timing / token / retry stats.

    Raises
    ------
    ValueError
        When the input guardrail rejects the message.
    RuntimeError
        When the output guardrail rejects an empty / malformed response.
    """
    # 1. Input guardrail
    in_result = input_guardrail.run(message)
    if not in_result.passed:
        logger.warning(f"[run_architect] Input guardrail blocked request: {in_result.reason}")
        raise ValueError(f"Request blocked by input guardrail: {in_result.reason}")

    effective_message = in_result.modified_content or message

    # 2. Harness initialisation
    logger.info(f"[run_architect] Initialising ArchitectAgentHarness (sandbox={sandbox_name})")
    harness = ArchitectAgentHarness(sandbox_name=sandbox_name)

    # 3. Build agent
    agent, resolved_provider, resolved_model = build_architect_agent(
        harness=harness,
        provider=provider,
        model_id=model_id,
    )
    logger.info(
        f"[run_architect] Agent built: provider={resolved_provider} model={resolved_model}"
    )

    # 4. Build request context
    request_ctx = AgentRequest(
        message=effective_message,
        provider=resolved_provider,
        model=resolved_model,
        metadata=extra_metadata or {},
    )

    # 5. Run through middleware pipeline
    async def _invoke(req: AgentRequest) -> str:
        return await _invoke_agent(agent, resolved_provider, resolved_model, req)

    response: AgentResponse = await architect_pipeline.run(_invoke, request_ctx)

    # 6. Output guardrail
    out_result = output_guardrail.run(response.text)
    if not out_result.passed:
        logger.error(f"[run_architect] Output guardrail blocked response: {out_result.reason}")
        raise RuntimeError(f"Response blocked by output guardrail: {out_result.reason}")

    if out_result.modified_content is not None:
        response.text = out_result.modified_content

    logger.success(
        f"[run_architect] Completed run_id={response.run_id} "
        f"duration={response.duration_ms:.1f}ms tokens≈{response.token_count_estimate}"
    )
    return response


# ---------------------------------------------------------------------------
# CLI smoke-test (python -m src.agents.architect.index)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    while True:
        