"""Unit tests for Telegram confirmation helpers."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import requests

from tastytrade_autotrader.notifications.telegram_confirmation import (
    PendingConfirmation,
    TelegramTradeConfirmer,
)
from tastytrade_autotrader.utils.order_history import OrderHistoryStore


def build_confirmer(history_path: Path) -> TelegramTradeConfirmer:
    """Create a confirmer with a lightweight test config."""
    config = SimpleNamespace(
        telegram_confirmation_enabled=True,
        telegram_bot_token="token",
        telegram_chat_id="123",
        telegram_confirmation_timeout_seconds=300,
        telegram_request_timeout_seconds=60,
    )
    return TelegramTradeConfirmer(
        config=config,
        session=Mock(),
        order_history_store=OrderHistoryStore(history_path),
    )


def make_history_path() -> Path:
    """Return a unique writable test history path inside the workspace."""
    path = (
        Path(__file__).resolve().parents[2]
        / ".test_artifacts"
        / f"telegram-history-{uuid4().hex}.jsonl"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def test_callback_yes_still_counts_if_callback_ack_times_out():
    """A valid Telegram button press should still approve even if ack is slow."""
    history_path = make_history_path()
    try:
        confirmer = build_confirmer(history_path)
        confirmer._answer_callback_query = Mock(side_effect=requests.Timeout("slow"))
        pending = PendingConfirmation()
        confirmer._pending_confirmations["ABC123"] = pending

        confirmer._handle_confirmation_response(
            {
                "callback_query": {
                    "id": "cb1",
                    "data": "TRADE_CONFIRM:YES:ABC123",
                    "message": {"chat": {"id": "123"}},
                }
            }
        )

        assert pending.decision == "YES"
        assert pending.event.is_set()
    finally:
        history_path.unlink(missing_ok=True)


def test_callback_no_still_counts_if_callback_ack_times_out():
    """A valid Telegram rejection button should still reject even if ack is slow."""
    history_path = make_history_path()
    try:
        confirmer = build_confirmer(history_path)
        confirmer._answer_callback_query = Mock(side_effect=requests.Timeout("slow"))
        pending = PendingConfirmation()
        confirmer._pending_confirmations["ABC123"] = pending

        confirmer._handle_confirmation_response(
            {
                "callback_query": {
                    "id": "cb2",
                    "data": "TRADE_CONFIRM:NO:ABC123",
                    "message": {"chat": {"id": "123"}},
                }
            }
        )

        assert pending.decision == "NO"
        assert pending.event.is_set()
    finally:
        history_path.unlink(missing_ok=True)


def test_orders_command_returns_recent_history():
    """The /orders Telegram command should send a readable recent history message."""
    history_path = make_history_path()
    try:
        confirmer = build_confirmer(history_path)
        confirmer.order_history_store.append(
            {
                "timestamp": "2026-04-22T16:00:00Z",
                "trade_type": "STOCK",
                "underlying_symbol": "AAPL",
                "order_symbol": "AAPL",
                "side": "BUY",
                "quantity": 10,
                "estimated_cost": 1000.0,
                "unit_price": 100.0,
                "result": "SUCCESS",
                "broker_status": "RECEIVED",
                "order_id": "12345",
                "filled_price": None,
                "failure_reason": None,
                "decision_reasoning": "Test entry",
            }
        )

        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"ok": True, "result": {"message_id": 1}}
        confirmer.session.post.return_value = response

        handled = confirmer._handle_command(
            {
                "message": {
                    "chat": {"id": "123"},
                    "text": "/orders",
                }
            }
        )

        assert handled is True
        payload = confirmer.session.post.call_args.kwargs["json"]
        assert "Recent Orders" in payload["text"]
        assert "12345" in payload["text"]
        assert "AAPL" in payload["text"]
    finally:
        history_path.unlink(missing_ok=True)
