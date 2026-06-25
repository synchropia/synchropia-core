"""
architect/guardrails.py
-----------------------
Input + output guardrails for the Architect agent.

Two layers are provided:

  InputGuardrail  — runs before the agent sees the user message.
                    Blocks or sanitises harmful / off-topic requests.

  OutputGuardrail — runs before the agent's response is returned.
                    Redacts secrets, trims oversized outputs, and enforces
                    tone / format constraints.

Both are designed to be called from index.py's run_with_guardrails() helper.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from src.utils.logger import logger


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class GuardrailResult:
    """Outcome of a single guardrail check."""
    passed: bool
    reason: str = ""
    modified_content: str | None = None   # set when the content was sanitised


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# Credentials / secrets that must never appear in agent output
_SECRET_PATTERNS: list[re.Pattern] = [
    re.compile(r"(?i)(api[_\-]?key|secret|password|token|bearer)\s*[:=]\s*\S+"),
    re.compile(r"(?i)(Authorization:\s*Bearer\s+)[^\s]+"),
    re.compile(r"eyJ[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+"),  # JWT
    re.compile(r"(?i)aws_secret_access_key\s*[:=]\s*\S+"),
    re.compile(r"(?i)ghp_[A-Za-z0-9]{36}"),   # GitHub PAT
    re.compile(r"(?i)xox[bpoa]-[0-9A-Za-z\-]+"),  # Slack token
]

# Prompt injection / jailbreak signals
_INJECTION_PATTERNS: list[re.Pattern] = [
    re.compile(r"(?i)ignore (all |previous |prior )?(instructions?|prompts?|rules?)"),
    re.compile(r"(?i)you are now\b"),
    re.compile(r"(?i)pretend (you are|to be)\b"),
    re.compile(r"(?i)disregard (your|the) (system|instructions?|guidelines?)"),
    re.compile(r"(?i)DAN mode"),
    re.compile(r"(?i)(sudo|root|admin)\s+(mode|override|access|escalat)"),
    re.compile(r"(?i)bypass\s+(safety|guardrail|filter|restriction)"),
]

# Maximum allowed output length (characters) before we trim + warn
_MAX_OUTPUT_CHARS = 80_000

# Topics that are clearly out-of-scope for a systems-architect agent
_OFF_TOPIC_PATTERNS: list[re.Pattern] = [
    re.compile(r"(?i)\b(write a poem|generate (an? )?(image|picture|photo)|play\s+\w+\s+game)\b"),
    re.compile(r"(?i)\b(celebrity|gossip|sports score|lottery)\b"),
]


# ---------------------------------------------------------------------------
# Input Guardrail
# ---------------------------------------------------------------------------

class InputGuardrail:
    """
    Validates / sanitises the user message before it reaches the agent.

    Checks performed (in order):
      1. Prompt-injection / jailbreak detection → hard block.
      2. Off-topic detection                    → soft block (polite rejection).
      3. Maximum input length                   → trim with notice.
    """

    MAX_INPUT_CHARS: int = 40_000

    def run(self, user_message: str) -> GuardrailResult:
        # 1. Injection check
        for pattern in _INJECTION_PATTERNS:
            if pattern.search(user_message):
                logger.warning(f"[InputGuardrail] Prompt injection detected: {pattern.pattern!r}")
                return GuardrailResult(
                    passed=False,
                    reason=(
                        "Your message appears to attempt a prompt-injection or "
                        "jailbreak. The Architect agent cannot process this request."
                    ),
                )

        # 2. Off-topic check
        for pattern in _OFF_TOPIC_PATTERNS:
            if pattern.search(user_message):
                logger.info(f"[InputGuardrail] Off-topic request detected: {pattern.pattern!r}")
                return GuardrailResult(
                    passed=False,
                    reason=(
                        "The Architect agent is specialised in systems design, API contracts, "
                        "and technical blueprints. The detected request appears to be outside "
                        "that scope. Please rephrase or consult a more appropriate assistant."
                    ),
                )

        # 3. Length guard
        if len(user_message) > self.MAX_INPUT_CHARS:
            trimmed = user_message[: self.MAX_INPUT_CHARS]
            notice = (
                f"\n\n[SYSTEM] Input was trimmed from {len(user_message)} "
                f"to {self.MAX_INPUT_CHARS} characters."
            )
            logger.warning(
                f"[InputGuardrail] Input too long ({len(user_message)} chars), trimming."
            )
            return GuardrailResult(
                passed=True,
                reason="Input trimmed to maximum allowed length.",
                modified_content=trimmed + notice,
            )

        return GuardrailResult(passed=True)


# ---------------------------------------------------------------------------
# Output Guardrail
# ---------------------------------------------------------------------------

class OutputGuardrail:
    """
    Validates / sanitises the agent's response before it is returned to the caller.

    Checks performed (in order):
      1. Secret / credential redaction.
      2. Maximum output length trimming.
      3. Empty response detection.
    """

    def run(self, agent_response: str) -> GuardrailResult:
        content = agent_response

        # 1. Secret redaction
        redacted = False
        for pattern in _SECRET_PATTERNS:
            new_content, n = pattern.subn("[REDACTED]", content)
            if n:
                redacted = True
                content = new_content
                logger.warning(
                    f"[OutputGuardrail] Redacted {n} credential(s) matching {pattern.pattern!r}"
                )

        # 2. Length guard
        trimmed = False
        if len(content) > _MAX_OUTPUT_CHARS:
            content = (
                content[:_MAX_OUTPUT_CHARS]
                + f"\n\n... [OUTPUT TRUNCATED — original response was "
                f"{len(agent_response):,} characters]"
            )
            trimmed = True
            logger.warning(
                f"[OutputGuardrail] Response truncated from "
                f"{len(agent_response)} to {_MAX_OUTPUT_CHARS} chars."
            )

        # 3. Empty response
        if not content.strip():
            return GuardrailResult(
                passed=False,
                reason="The agent returned an empty response.",
                modified_content=content,
            )

        if redacted or trimmed:
            return GuardrailResult(
                passed=True,
                reason="Response was modified (secrets redacted and/or length trimmed).",
                modified_content=content,
            )

        return GuardrailResult(passed=True, modified_content=content)


# ---------------------------------------------------------------------------
# Convenience singleton instances
# ---------------------------------------------------------------------------

input_guardrail = InputGuardrail()
output_guardrail = OutputGuardrail()
