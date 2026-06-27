"""
architect/middleware.py
-----------------------
Request / response middleware pipeline for the Architect agent.

Middleware layers execute *around* the agent call and are independent of the
guardrail logic (which is a hard accept/reject gate).  Middleware is for
cross-cutting concerns: tracing, rate-limiting, token accounting, retry, etc.

Usage in index.py:
    pipeline = MiddlewarePipeline([
        LoggingMiddleware(),
        RateLimitMiddleware(max_rpm=30),
        TokenBudgetMiddleware(max_input_tokens=8192),
        RetryMiddleware(max_retries=2),
    ])

    response = await pipeline.run(invoke_fn, request_ctx)
"""

from __future__ import annotations

import asyncio
import time
import uuid
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from src.utils.logger import logger


# ---------------------------------------------------------------------------
# Request / Response context objects
# ---------------------------------------------------------------------------

@dataclass
class AgentRequest:
    """Immutable-ish snapshot of a single agent invocation request."""
    message: str
    provider: str
    model:str
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentResponse:
    """Container for a completed agent invocation result."""
    text: str
    run_id: str
    provider: str
    model: str
    duration_ms: float = 0.0
    token_count_estimate: int = 0
    retries: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


# Type alias for the core agent invocation callable
InvokeFn = Callable[[AgentRequest], Awaitable[str]]


# ---------------------------------------------------------------------------
# Base middleware
# ---------------------------------------------------------------------------

class BaseMiddleware(ABC):
    """
    All middleware must implement ``process``.

    Call ``await call_next(request)`` to pass control to the next layer.
    """

    @abstractmethod
    async def process(
        self,
        request: AgentRequest,
        call_next: Callable[[AgentRequest], Awaitable[AgentResponse]],
    ) -> AgentResponse:
        ...


# ---------------------------------------------------------------------------
# Concrete middleware implementations
# ---------------------------------------------------------------------------

class LoggingMiddleware(BaseMiddleware):
    """Logs request arrival and response summary (timing, token estimate)."""

    async def process(self, request: AgentRequest, call_next) -> AgentResponse:
        logger.info(
            f"[Middleware:Logging] → run_id={request.run_id} "
            f"provider={request.provider} model={request.model} "
            f"msg_len={len(request.message)}"
        )
        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1_000
        response.duration_ms = elapsed_ms
        logger.info(
            f"[Middleware:Logging] ← run_id={response.run_id} "
            f"duration={elapsed_ms:.1f}ms retries={response.retries} "
            f"resp_len={len(response.text)}"
        )
        return response


class RateLimitMiddleware(BaseMiddleware):
    """
    Sliding-window rate limiter.

    Raises RuntimeError if more than ``max_rpm`` calls arrive within 60 s.
    """

    def __init__(self, max_rpm: int = 30):
        self.max_rpm = max_rpm
        self._timestamps: deque[float] = deque()

    async def process(self, request: AgentRequest, call_next) -> AgentResponse:
        now = time.monotonic()

        # Evict timestamps older than 60 s
        while self._timestamps and self._timestamps[0] < now - 60:
            self._timestamps.popleft()

        if len(self._timestamps) >= self.max_rpm:
            wait_s = 60 - (now - self._timestamps[0])
            logger.warning(
                f"[Middleware:RateLimit] Rate limit reached "
                f"({self.max_rpm} rpm). Waiting {wait_s:.1f}s."
            )
            await asyncio.sleep(wait_s)

        self._timestamps.append(now)
        return await call_next(request)


class TokenBudgetMiddleware(BaseMiddleware):
    """
    Rough token-budget check based on a simple whitespace tokeniser.

    A proper tiktoken-based count can replace the estimate if the dependency
    is available.
    """

    CHARS_PER_TOKEN: float = 4.0  # conservative average

    def __init__(self, max_input_tokens: int = 8_192):
        self.max_input_tokens = max_input_tokens

    def _estimate_tokens(self, text: str) -> int:
        return max(1, int(len(text) / self.CHARS_PER_TOKEN))

    async def process(self, request: AgentRequest, call_next) -> AgentResponse:
        estimated = self._estimate_tokens(request.message)
        if estimated > self.max_input_tokens:
            logger.warning(
                f"[Middleware:TokenBudget] Estimated input tokens ({estimated}) "
                f"exceeds budget ({self.max_input_tokens}). "
                "Request will still proceed but may be rejected by the model."
            )
        response = await call_next(request)
        response.token_count_estimate = estimated + self._estimate_tokens(response.text)
        return response


class RetryMiddleware(BaseMiddleware):
    """
    Exponential back-off retry on transient errors.

    Catches any Exception from downstream and retries up to ``max_retries``
    times.  Non-retryable semantic errors (e.g. guardrail rejections) should
    be raised as a specific subclass and excluded here if needed.
    """

    def __init__(self, max_retries: int = 2, base_delay_s: float = 1.0):
        self.max_retries = max_retries
        self.base_delay_s = base_delay_s

    async def process(self, request: AgentRequest, call_next) -> AgentResponse:
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = await call_next(request)
                response.retries = attempt
                return response
            except Exception as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    delay = self.base_delay_s * (2 ** attempt)
                    logger.warning(
                        f"[Middleware:Retry] Attempt {attempt + 1} failed: {exc}. "
                        f"Retrying in {delay:.1f}s…"
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        f"[Middleware:Retry] All {self.max_retries + 1} attempts failed."
                    )
        raise last_exc  # type: ignore[misc]


class MetadataInjectionMiddleware(BaseMiddleware):
    """
    Injects run-level metadata (e.g. tenant ID, trace ID) into every request
    so downstream services can correlate logs without cluttering call sites.
    """

    def __init__(self, static_metadata: dict[str, Any] | None = None):
        self.static_metadata = static_metadata or {}

    async def process(self, request: AgentRequest, call_next) -> AgentResponse:
        request.metadata.update(self.static_metadata)
        request.metadata.setdefault("trace_id", request.run_id)
        return await call_next(request)


# ---------------------------------------------------------------------------
# Pipeline executor
# ---------------------------------------------------------------------------

class MiddlewarePipeline:
    """
    Chains middleware in FIFO order and wraps the final agent invocation.

    Example::

        pipeline = MiddlewarePipeline([
            LoggingMiddleware(),
            RateLimitMiddleware(max_rpm=30),
            TokenBudgetMiddleware(max_input_tokens=8192),
            RetryMiddleware(max_retries=2),
        ])

        response = await pipeline.run(invoke_fn, request)
    """

    def __init__(self, layers: list[BaseMiddleware]):
        self._layers = layers

    async def run(self, invoke_fn: InvokeFn, request: AgentRequest) -> AgentResponse:
        """Execute the middleware stack and finally call ``invoke_fn``."""

        async def _core(req: AgentRequest) -> AgentResponse:
            text = await invoke_fn(req)
            return AgentResponse(
                text=text,
                run_id=req.run_id,
                provider=req.provider,
                model=req.model,
            )

        async def _build_chain(index: int) -> Callable[[AgentRequest], Awaitable[AgentResponse]]:
            if index >= len(self._layers):
                return _core

            layer = self._layers[index]
            next_handler = await _build_chain(index + 1)

            async def _handler(req: AgentRequest) -> AgentResponse:
                return await layer.process(req, next_handler)

            return _handler

        handler = await _build_chain(0)
        return await handler(request)


# ---------------------------------------------------------------------------
# Default pipeline (ready-to-use in index.py)
# ---------------------------------------------------------------------------

default_pipeline = MiddlewarePipeline([
    MetadataInjectionMiddleware(),
    LoggingMiddleware(),
    RateLimitMiddleware(max_rpm=30),
    TokenBudgetMiddleware(max_input_tokens=8_192),
    RetryMiddleware(max_retries=2),
])
