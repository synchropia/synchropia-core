# C:\Users\Raghu\Downloads\MAS_Agents\synchropia\src\agents\architect\index.py

from __future__ import annotations

import asyncio
from typing import Any

from deepagents import create_deep_agent

from src.core.llm_gateway import unified_llm_gateway
from src.utils.logger import logger
from src.services.kafka_service import KafkaAgentCallbackHandler, kafka_client

from src.agents.architect.prompt import ARCHITECT_SYSTEM_PROMPT
from src.agents.architect.harness import ArchitectAgentHarness

from langchain_core.callbacks import BaseCallbackHandler


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------

async def build_architect_agent(
    harness: ArchitectAgentHarness,
    provider: str = "selfhosted",
    **llm_kwargs,
):
    """
    Construct a deep agent bound to the harness backend, tool-list,
    and a fully-configured streaming LLM module.
    """
    models = await unified_llm_gateway.get_models_for_provider(provider)
    resolved_model = "gemma_4"
    logger.info(f"Resolved model: {resolved_model}")
    
    # 1. Fetch the actual LangChain model with streaming=True pre-configured
    llm_instance = unified_llm_gateway.get_chat_model(
        provider_id=provider,
        model_id=resolved_model,
        **llm_kwargs
    )
    
    # 2. Instantiate deep agent passing the stream-capable ChatModel instance
    agent = create_deep_agent(
        system_prompt=ARCHITECT_SYSTEM_PROMPT,
        backend=harness.backend,
        tools=harness.tools,
        model=llm_instance,   
    )
    return agent, provider, resolved_model


# ---------------------------------------------------------------------------
# Core invocation helper
# ---------------------------------------------------------------------------



class ConsoleStreamingCallbackHandler(BaseCallbackHandler):
    """Callback handler to stream LLM tokens directly to the console."""
    def on_llm_new_token(self, token: str, **kwargs: Any) -> None:
        import sys
        sys.stdout.write(token)
        sys.stdout.flush()

async def _invoke_agent(agent, provider: str, model_id: str, request: AgentRequest) -> str:
    """
    Invokes the deep agent reasoning and tool loops directly, allowing
    its execution callbacks to automatically publish real-time events 
    to the Kafka brokers.
    """
    # Instantiate the Kafka Callback Handler specifically for this run_id
    kafka_handler = KafkaAgentCallbackHandler(
        kafka_client=kafka_client,
        default_agent_name="architect"
    )
    # Ensure correct run_id configuration on handler
    kafka_handler.run_id = request.run_id

    # Construct the LangChain execution config block
    config = {
        "callbacks": [kafka_handler, ConsoleStreamingCallbackHandler()],
        "run_id": request.run_id
    }

    logger.info(f"Executing Deep Agent with streaming callbacks. run_id={request.run_id}")

    try:
        # Standard Runnable/Executor interfaces supported by deepagents
        if hasattr(agent, "arun"):
            response_text = await agent.arun(request.message, config=config)
        elif hasattr(agent, "ainvoke"):
            res = await agent.ainvoke({"input": request.message}, config=config)
            response_text = res if isinstance(res, str) else res.get("output", str(res))
        else:
            res = await agent(request.message, config=config)
            response_text = res if isinstance(res, str) else res.get("output", str(res))
    except Exception as e:
        logger.error(f"Failed to execute deep agent invocation loop: {e}")
        raise

    return response_text


# ---------------------------------------------------------------------------
# Public entry-point
# ---------------------------------------------------------------------------

async def run_architect(
    message: str,
    *,
    provider: str = "selfhosted",
    sandbox_name: str = "architect-sandbox",
    extra_metadata: dict[str, Any] | None = None,
    run_id: str | None = None,
) -> AgentResponse:
    """
    High-level coroutine to invoke the Architect agent end-to-end.
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
    agent, resolved_provider, resolved_model = await build_architect_agent(
        harness=harness,
        provider=provider,
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
    if run_id:
        request_ctx.run_id = run_id


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
    import sys

    _provider = sys.argv[1] if len(sys.argv) > 1 else "selfhosted"
    _model    = sys.argv[2] if len(sys.argv) > 2 else None

    print(f"\n{'='*60}")
    print(f"  Architect Agent  —  provider: {_provider}  model: {_model or 'default'}")
    print(f"  Type 'exit' or 'quit' to stop.  Empty line re-prompts.")
    print(f"{'='*60}\n")

    async def _repl():
        while True:
            try:
                task = input("architect> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nBye.")
                break

            if not task:
                continue
            if task.lower() in ("exit", "quit", "q"):
                print("Bye.")
                break

            try:
                result = await run_architect(
                    task,
                    provider=_provider
                )
                print(f"\n{'─'*60}")
                print(result.text)
                print(f"{'─'*60}")
                print(
                    f"run_id={result.run_id}  "
                    f"duration={result.duration_ms:.0f}ms  "
                    f"tokens≈{result.token_count_estimate}  "
                    f"retries={result.retries}\n"
                )
            except ValueError as e:
                print(f"\n[blocked] {e}\n")
            except Exception as e:
                print(f"\n[error] {e}\n")

    asyncio.run(_repl())