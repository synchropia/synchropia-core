from typing import Any, Dict, List, Optional, Union
from loguru import logger

# Import prebuilt LangChain middlewares
from langchain.agents.middleware import (
    AgentMiddleware,
    AgentState,
    Runtime,
    ModelRetryMiddleware,
    ModelFallbackMiddleware,
    LLMToolSelectorMiddleware,
    ToolRetryMiddleware,
    HumanInTheLoopMiddleware,
    ModelCallLimitMiddleware,
    ToolCallLimitMiddleware
)
from langchain_core.messages import AIMessage
from src.utils.rate_limiter import RedisRateLimiter

class RateLimitMiddleware(AgentMiddleware):
    """Custom middleware to apply sliding-window rate limiting to agent execution runs.
    
    Aborts execution early by returning jump_to="end" if rate limits are exceeded.
    """
    
    def __init__(self, rate_limiter: RedisRateLimiter, limit: int = 60, window_seconds: int = 60):
        super().__init__()
        self.rate_limiter = rate_limiter
        self.limit = limit
        self.window_seconds = window_seconds
        self.tools = []

    def before_agent(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        # Extract user_id from state or use default
        user_id = state.get("user_id") or "default_user"
        if not self.rate_limiter.is_allowed(user_id, self.limit, self.window_seconds):
            logger.warning(f"[RateLimitMiddleware] Rate limit exceeded for {user_id}: {self.limit} req per {self.window_seconds}s.")
            # Halts execution early and yields an assistant message notifying the limit
            return {
                "messages": [AIMessage(content=f"⚠️ Rate limit exceeded ({self.limit} runs/{self.window_seconds}s). Execution halted.")],
                "jump_to": "end"
            }
        return None

    async def abefore_agent(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        return self.before_agent(state, runtime)

def get_default_middleware_stack(
    agent_name: str,
    rate_limiter: Optional[RedisRateLimiter] = None,
    rate_limit: int = 30,
    rate_window: int = 60,
    max_model_retries: int = 2,
    fallback_models: Optional[List[str]] = None,
    tool_selector_model: Optional[str] = None,
    max_tools: Optional[int] = None,
    always_include_tools: Optional[List[str]] = None,
    interrupt_tools: Optional[Dict[str, Any]] = None,
    max_model_calls: int = 10,
    max_tool_calls: int = 15
) -> List[AgentMiddleware]:
    """Constructs a pre-configured stack of agent middlewares custom to Synchropia's needs.
    
    Includes:
    - RateLimitMiddleware (sliding window protection using Redis)
    - ModelFallbackMiddleware (automatic model failover for high availability)
    - ModelRetryMiddleware (automatic backoff retry for transient LLM issues)
    - LLMToolSelectorMiddleware (filters active tools down to reduce prompt pollution)
    - ToolRetryMiddleware (retries transient network issues on tool execution)
    - HumanInTheLoopMiddleware (interruption gate for dangerous/costly operations)
    - ModelCallLimitMiddleware & ToolCallLimitMiddleware (loop protection)
    """
    stack: List[AgentMiddleware] = []

    # 1. Rate Limiting Middleware
    if rate_limiter is not None:
        logger.info(f"[MiddlewareBuilder] Adding RateLimitMiddleware for {agent_name}")
        stack.append(RateLimitMiddleware(rate_limiter, limit=rate_limit, window_seconds=rate_window))

    # 2. Model Fallback Middleware
    if fallback_models:
        logger.info(f"[MiddlewareBuilder] Adding ModelFallbackMiddleware for {agent_name} with: {fallback_models}")
        # Pass first model, and then any additional ones as unpacked positional arguments
        first = fallback_models[0]
        additional = fallback_models[1:]
        stack.append(ModelFallbackMiddleware(first, *additional))

    # 3. Model Retry Middleware
    logger.info(f"[MiddlewareBuilder] Adding ModelRetryMiddleware for {agent_name} (retries={max_model_retries})")
    stack.append(ModelRetryMiddleware(max_retries=max_model_retries, backoff_factor=2.0))

    # 4. LLM Tool Selector Middleware
    if max_tools is not None or tool_selector_model is not None:
        logger.info(f"[MiddlewareBuilder] Adding LLMToolSelectorMiddleware for {agent_name} (max_tools={max_tools})")
        stack.append(LLMToolSelectorMiddleware(
            model=tool_selector_model,
            max_tools=max_tools,
            always_include=always_include_tools
        ))

    # 5. Tool Retry Middleware
    logger.info(f"[MiddlewareBuilder] Adding ToolRetryMiddleware for {agent_name}")
    from httpx import RequestError
    stack.append(ToolRetryMiddleware(
        max_retries=2,
        retry_on=(RequestError, Exception),
        backoff_factor=1.5
    ))

    # 6. Human In The Loop Middleware (Interruption Gate)
    if interrupt_tools:
        logger.info(f"[MiddlewareBuilder] Adding HumanInTheLoopMiddleware for {agent_name} on tools: {list(interrupt_tools.keys())}")
        stack.append(HumanInTheLoopMiddleware(interrupt_on=interrupt_tools))

    # 7. Loop Protections
    logger.info(f"[MiddlewareBuilder] Adding Runaway Loop Protection Middlewares (model_calls={max_model_calls}, tool_calls={max_tool_calls})")
    stack.append(ModelCallLimitMiddleware(max_calls=max_model_calls))
    stack.append(ToolCallLimitMiddleware(max_calls=max_tool_calls))

    return stack

__all__ = [
    "RateLimitMiddleware",
    "get_default_middleware_stack",
    "ModelRetryMiddleware",
    "ModelFallbackMiddleware",
    "LLMToolSelectorMiddleware",
    "ToolRetryMiddleware",
    "HumanInTheLoopMiddleware",
    "ModelCallLimitMiddleware",
    "ToolCallLimitMiddleware"
]
