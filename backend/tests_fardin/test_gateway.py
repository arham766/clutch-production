"""
Tests for src/gateway.py — routing, fallback, and safe-instruction guardrail.

Covers:
- Route table: init, resolution, unknown task
- GatewayClient: primary success, retry on transient error
- GatewayClient: fallback to direct provider when gateway is down
- Safe-instruction guardrail: flagging unsafe patterns, prepending warnings
- Embeddings: primary and fallback
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch, PropertyMock

import httpx
import pytest


# ---------------------------------------------------------------------------
# Route table
# ---------------------------------------------------------------------------

class TestRouteTable:
    def test_route_before_init_raises(self) -> None:
        from src.gateway import route, _ROUTE_TABLE
        import src.gateway as gw

        # Save and clear
        saved = dict(gw._ROUTE_TABLE)
        gw._ROUTE_TABLE.clear()

        try:
            with pytest.raises(RuntimeError, match="not initialised"):
                route("vision.identify")
        finally:
            gw._ROUTE_TABLE.update(saved)

    def test_route_unknown_task_raises(self) -> None:
        from src.gateway import route
        import src.gateway as gw

        # Ensure the table is populated
        gw._ROUTE_TABLE = {"vision.identify": "qwen-vl"}
        with pytest.raises(KeyError, match="Unknown gateway task"):
            route("nonexistent.task")

    @patch("src.gateway._build_route_table")
    def test_init_gateway_builds_table(self, mock_build: MagicMock) -> None:
        from src.gateway import init_gateway
        import src.gateway as gw

        mock_build.return_value = {
            "vision.identify": "test-vision",
            "reason.compose": "test-reason",
            "embed": "test-embed",
        }

        mock_cfg = MagicMock()
        mock_cfg.tf_base_url = "https://test-gw.example.com"
        mock_cfg.tf_api_key = "test-key"

        client = init_gateway(mock_cfg)
        assert gw._ROUTE_TABLE["vision.identify"] == "test-vision"
        assert gw._ROUTE_TABLE["reason.compose"] == "test-reason"
        assert client is not None
        client.close()


# ---------------------------------------------------------------------------
# Safe-instruction guardrail
# ---------------------------------------------------------------------------

class TestSafeInstructionGuardrail:
    def test_safe_text_not_flagged(self) -> None:
        from src.gateway import check_safe_instruction

        flagged, text = check_safe_instruction(
            "Open the paper tray and remove any jammed paper."
        )
        assert not flagged
        assert "WARNING" not in text

    def test_unsafe_live_wire(self) -> None:
        from src.gateway import check_safe_instruction

        flagged, text = check_safe_instruction(
            "You can touch the live wire to test connectivity."
        )
        assert flagged
        assert text.startswith("WARNING")
        assert "powered off and unplugged" in text

    def test_unsafe_bypass_ground(self) -> None:
        from src.gateway import check_safe_instruction

        flagged, text = check_safe_instruction(
            "Simply bypass the ground wire to speed up the repair."
        )
        assert flagged
        assert "WARNING" in text

    def test_unsafe_disable_safety_switch(self) -> None:
        from src.gateway import check_safe_instruction

        flagged, text = check_safe_instruction(
            "You should disable the safety switch before opening the panel."
        )
        assert flagged

    def test_safe_normal_advice(self) -> None:
        from src.gateway import check_safe_instruction

        flagged, _ = check_safe_instruction(
            "1. Unplug the device. 2. Wait 30 seconds. 3. Press the reset button."
        )
        assert not flagged

    def test_unsafe_submerge_electric(self) -> None:
        from src.gateway import check_safe_instruction

        flagged, text = check_safe_instruction(
            "Pour water over the electric motor to cool it down while plugged in."
        )
        assert flagged

    def test_guardrail_preserves_original_content(self) -> None:
        from src.gateway import check_safe_instruction

        original = "Go ahead and touch the live wire terminal."
        flagged, text = check_safe_instruction(original)
        assert flagged
        # Original text is still present after the warning
        assert original in text


# ---------------------------------------------------------------------------
# GatewayClient — primary, retry, fallback
# ---------------------------------------------------------------------------

class TestGatewayClient:
    def _make_client(self, **kwargs: object) -> "GatewayClient":
        from src.gateway import GatewayClient
        import src.gateway as gw
        gw._ROUTE_TABLE = {"reason.compose": "test-model", "embed": "test-embed"}
        return GatewayClient(
            base_url="https://gw.test.com",
            api_key="gw-key",
            **kwargs,
        )

    @patch("httpx.Client.post")
    def test_primary_success(self, mock_post: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "Hello"}, "finish_reason": "stop"}],
        }
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        client = self._make_client()
        result = client.chat_completion(
            task="reason.compose",
            messages=[{"role": "user", "content": "hi"}],
        )
        assert result["choices"][0]["message"]["content"] == "Hello"

    @patch("httpx.Client.post")
    def test_retry_on_timeout(self, mock_post: MagicMock) -> None:
        # First call times out, second succeeds
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"choices": [{"message": {"content": "OK"}}]}
        mock_resp.raise_for_status = MagicMock()

        mock_post.side_effect = [
            httpx.TimeoutException("timeout"),
            mock_resp,
        ]

        client = self._make_client(max_retries=1)
        result = client.chat_completion(
            task="reason.compose",
            messages=[{"role": "user", "content": "hi"}],
        )
        assert result["choices"][0]["message"]["content"] == "OK"
        assert mock_post.call_count == 2

    @patch("httpx.Client.post")
    def test_fallback_when_gateway_exhausted(self, mock_post: MagicMock) -> None:
        """When the gateway is fully down, use the fallback provider."""
        # All gateway calls fail
        gateway_error = httpx.ConnectError("gateway down")

        # Fallback returns a response
        fallback_resp = MagicMock()
        fallback_resp.status_code = 200
        fallback_resp.json.return_value = {
            "choices": [{"message": {"role": "assistant", "content": "Fallback OK"}}],
        }
        fallback_resp.raise_for_status = MagicMock()

        # First 3 calls (gateway retries) fail, 4th (fallback) succeeds
        mock_post.side_effect = [
            gateway_error,
            gateway_error,
            gateway_error,
            fallback_resp,
        ]

        client = self._make_client(
            fallback_base_url="https://fallback.test.com",
            fallback_api_key="fallback-key",
            max_retries=2,
        )
        result = client.chat_completion(
            task="reason.compose",
            messages=[{"role": "user", "content": "help"}],
        )
        assert result["choices"][0]["message"]["content"] == "Fallback OK"

    @patch("httpx.Client.post")
    def test_fallback_applies_guardrail(self, mock_post: MagicMock) -> None:
        """When falling back, the local guardrail should be applied."""
        gateway_error = httpx.ConnectError("down")

        fallback_resp = MagicMock()
        fallback_resp.status_code = 200
        fallback_resp.json.return_value = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "Just touch the live wire terminal to test.",
                },
            }],
        }
        fallback_resp.raise_for_status = MagicMock()

        mock_post.side_effect = [gateway_error, gateway_error, fallback_resp]

        client = self._make_client(
            fallback_base_url="https://fallback.test.com",
            fallback_api_key="fb-key",
            max_retries=1,
        )
        result = client.chat_completion(
            task="reason.compose",
            messages=[{"role": "user", "content": "help"}],
        )
        content = result["choices"][0]["message"]["content"]
        assert content.startswith("WARNING")
        assert result.get("_guardrail_applied") is True

    @patch("httpx.Client.post")
    def test_no_fallback_raises(self, mock_post: MagicMock) -> None:
        """Without a fallback, exhausted retries raise the last error."""
        mock_post.side_effect = httpx.ConnectError("nope")

        client = self._make_client(max_retries=1)
        with pytest.raises(httpx.ConnectError):
            client.chat_completion(
                task="reason.compose",
                messages=[{"role": "user", "content": "hi"}],
            )
