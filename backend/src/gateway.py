"""
Clutch gateway — TrueFoundry AI Gateway adapter.

One OpenAI-compatible endpoint, one API key, for all model calls
(vision + reasoning). This is the LLM/vision swap point (HLD 00 §7):
callers ask for logical task names; the gateway resolves to models.

STT/TTS are NOT gateway-routed (they're LiveKit plugins — swap via
speech adapters in src/speech/).

Features (HLD 06 §3a, §6):
- Logical task->model routing via ``route()``
- Auto-fallback: if TrueFoundry is down, falls back to direct provider
- Local safe-instruction guardrail: when gateway guardrails are bypassed,
  applies a local check on hardware safety advice

Reference: HLD 06 §3a (AI Gateway), §4 (route table), §6 (failure modes).
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from src.config import Config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Route table (logical task -> model name)
# ---------------------------------------------------------------------------

_ROUTE_TABLE: dict[str, str] = {}


def _build_route_table(cfg: Config) -> dict[str, str]:
    """Build the logical-task -> model mapping from config."""
    return {
        "vision.identify": cfg.vision_model,
        "reason.compose": cfg.reason_model,
        "embed": cfg.embed_model,
    }


def route(task: str) -> str:
    """Resolve a logical task name to a model name.

    Args:
        task: Logical name (e.g. ``"vision.identify"``, ``"reason.compose"``).

    Returns:
        The model name configured for that task.

    Raises:
        KeyError: If the task is not in the route table.
    """
    if not _ROUTE_TABLE:
        raise RuntimeError("Gateway not initialised. Call init_gateway() first.")

    if task not in _ROUTE_TABLE:
        raise KeyError(
            f"Unknown gateway task: {task!r}. "
            f"Known tasks: {list(_ROUTE_TABLE.keys())}"
        )
    return _ROUTE_TABLE[task]


# ---------------------------------------------------------------------------
# Local safe-instruction guardrail (HLD 06 §6)
# ---------------------------------------------------------------------------

# Patterns that indicate potentially unsafe hardware advice
_UNSAFE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(touch|grab|hold)\b.*\b(live\s+wire|exposed\s+wire|mains|terminal)", re.I),
    re.compile(r"\b(do\s+not\s+unplug|leave\s+(it\s+)?plugged\s+in)\b.*\b(while|during|when)\b.*\b(open|repair|disassembl)", re.I),
    re.compile(r"\breplace\b.*\b(capacitor|fuse|transformer)\b.*\b(yourself|without\s+training)", re.I),
    re.compile(r"\b(immerse|submerge|pour\s+water)\b.*\b(electric|power|plugged)", re.I),
    re.compile(r"\b(bypass|disable|remove)\b.*\b(ground|earth|safety\s+switch|circuit\s+breaker)", re.I),
    re.compile(r"\b(hot\s+surface|burn|scalding)\b.*\b(touch|reach\s+in)", re.I),
]

_SAFETY_WARNING = (
    "WARNING: For your safety, ensure the device is fully powered off and "
    "unplugged before performing any physical maintenance or repair. If you "
    "are unsure, please contact a certified technician."
)


def check_safe_instruction(text: str) -> tuple[bool, str]:
    """Run a local safe-instruction check on generated text.

    If the text contains patterns suggesting unsafe hardware advice,
    prepends a safety warning. This is the local fallback for when
    TrueFoundry's built-in guardrails are bypassed (direct-provider mode).

    Args:
        text: The generated text to check.

    Returns:
        Tuple of (was_flagged, possibly_modified_text).
    """
    for pattern in _UNSAFE_PATTERNS:
        if pattern.search(text):
            logger.warning(
                "Safe-instruction guardrail triggered: pattern=%s",
                pattern.pattern[:60],
            )
            return True, f"{_SAFETY_WARNING}\n\n{text}"

    return False, text


# ---------------------------------------------------------------------------
# Gateway client (OpenAI-compatible) with fallback
# ---------------------------------------------------------------------------

class GatewayClient:
    """OpenAI-compatible client pointing at the TrueFoundry AI Gateway.

    All model calls (LLM + vision) route through here. Provides:
    - ``chat_completion``: text/vision reasoning
    - ``embeddings``: vector embeddings
    - **Automatic fallback**: if the gateway is down, falls back to a
      direct provider client (same OpenAI shape) + local guardrail.

    The gateway handles fallback, cost tracking, and guardrails.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        fallback_base_url: str | None = None,
        fallback_api_key: str | None = None,
        max_retries: int = 2,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._max_retries = max_retries

        # Primary gateway client
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=60.0,
        )

        # Fallback client (direct provider, if gateway is fully down)
        self._fallback_client: httpx.Client | None = None
        self._using_fallback = False
        if fallback_base_url and fallback_api_key:
            self._fallback_client = httpx.Client(
                base_url=fallback_base_url.rstrip("/"),
                headers={
                    "Authorization": f"Bearer {fallback_api_key}",
                    "Content-Type": "application/json",
                },
                timeout=60.0,
            )

    def chat_completion(
        self,
        *,
        task: str,
        messages: list[dict[str, Any]],
        temperature: float = 0.3,
        max_tokens: int = 2048,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """OpenAI-compatible chat completion via the gateway.

        If the gateway is unreachable, falls back to the direct provider
        and applies a local safe-instruction guardrail on the response.

        Args:
            task: Logical task name (resolved via ``route()``).
            messages: Chat messages.
            temperature: Sampling temperature.
            max_tokens: Max output tokens.

        Returns:
            The full API response dict.
        """
        model = route(task)
        # OpenRouter does not expect 'openrouter/' prefix in the model ID.
        if "openrouter.ai" in self.base_url and model.startswith("openrouter/"):
            model = model.replace("openrouter/", "", 1)
            
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            **kwargs,
        }

        # Try primary gateway with retries
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                resp = self._client.post("/v1/chat/completions", json=payload)
                resp.raise_for_status()
                return resp.json()
            except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError) as e:
                last_error = e
                status = getattr(getattr(e, "response", None), "status_code", None)
                if status and 400 <= status < 500 and status != 429:
                    # Client error (not rate limit) — don't retry
                    raise
                logger.warning(
                    "Gateway attempt %d/%d failed: %s",
                    attempt + 1, self._max_retries + 1, e,
                )

        # Gateway exhausted — try fallback
        if self._fallback_client is not None:
            logger.warning("Gateway fully down — falling back to direct provider")
            self._using_fallback = True
            try:
                fallback_model = route(task)
                # If fallback is OpenRouter, strip prefix. If TrueFoundry, keep it.
                if "openrouter.ai" in str(self._fallback_client.base_url) and fallback_model.startswith("openrouter/"):
                    fallback_model = fallback_model.replace("openrouter/", "", 1)
                
                fallback_payload = {**payload, "model": fallback_model}
                resp = self._fallback_client.post("/v1/chat/completions", json=fallback_payload)
                resp.raise_for_status()
                result = resp.json()

                # Apply local safe-instruction guardrail (since we lost
                # TrueFoundry's built-in guardrails)
                result = self._apply_local_guardrail(result)
                return result
            except Exception as fallback_err:
                logger.exception("Fallback provider also failed")
                raise fallback_err from last_error

        # No fallback configured — raise the last gateway error
        raise last_error  # type: ignore[misc]

    def embeddings(
        self,
        *,
        texts: list[str],
        task: str = "embed",
    ) -> list[list[float]]:
        """Generate embeddings via the gateway."""
        model = route(task)
        payload = {"model": model, "input": texts}

        try:
            resp = self._client.post("/v1/embeddings", json=payload)
            resp.raise_for_status()
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            if self._fallback_client is not None:
                logger.warning("Embeddings: gateway down, using fallback")
                resp = self._fallback_client.post("/v1/embeddings", json=payload)
                resp.raise_for_status()
            else:
                raise

        data = resp.json()
        return [item["embedding"] for item in data.get("data", [])]

    def _apply_local_guardrail(self, result: dict[str, Any]) -> dict[str, Any]:
        """Apply the local safe-instruction check on a direct-provider response.

        Only called when the gateway is bypassed (fallback mode), meaning
        we've lost TrueFoundry's built-in guardrails. Fail toward caution:
        prepend a safety warning rather than going silent.
        """
        choices = result.get("choices", [])
        for choice in choices:
            msg = choice.get("message", {})
            content = msg.get("content", "")
            if content:
                flagged, safe_content = check_safe_instruction(content)
                if flagged:
                    msg["content"] = safe_content
                    # Tag the response so callers know guardrail fired
                    result["_guardrail_applied"] = True
        return result

    def close(self) -> None:
        """Close the underlying HTTP client(s)."""
        self._client.close()
        if self._fallback_client:
            self._fallback_client.close()


# ---------------------------------------------------------------------------
# Init + factory
# ---------------------------------------------------------------------------

def init_gateway(cfg: Config) -> GatewayClient:
    """Initialise the gateway route table and return a client.

    Called once at startup.
    """
    global _ROUTE_TABLE
    _ROUTE_TABLE = _build_route_table(cfg)
    logger.info(
        "Gateway initialised: %s",
        {k: v for k, v in _ROUTE_TABLE.items()},
    )
    if cfg.openrouter_api_key:
        return GatewayClient(
            base_url=cfg.openrouter_base_url,
            api_key=cfg.openrouter_api_key,
            fallback_base_url=cfg.tf_base_url,
            fallback_api_key=cfg.tf_api_key,
        )
    return GatewayClient(base_url=cfg.tf_base_url, api_key=cfg.tf_api_key)


def gateway_client(cfg: Config) -> GatewayClient:
    """Convenience: init + return a gateway client."""
    return init_gateway(cfg)
