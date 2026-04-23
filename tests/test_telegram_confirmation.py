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
        telegram_poll_timeout_seconds=5,
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
        assert "Order History" in payload["text"]
        assert "12345" in payload["text"]
        assert "AAPL" in payload["text"]
        assert "Status: Successful | Broker: RECEIVED" in payload["text"]
        assert "Est. Cost: $1,000.00" in payload["text"]
    finally:
        history_path.unlink(missing_ok=True)


def test_orders_command_tolerates_legacy_string_values():
    """The /orders command should still render if older rows stored numbers as strings."""
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
                "quantity": "1000.0",
                "estimated_cost": "100000.00",
                "unit_price": "100.0",
                "result": "SUCCESS",
                "broker_status": "Routed",
                "order_id": "940344",
                "filled_price": None,
                "failure_reason": None,
                "decision_reasoning": "Legacy entry",
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
        assert "AAPL" in payload["text"]
        assert "Action: Stock Buy AAPL x1,000" in payload["text"]
        assert "Est. Cost: $100,000.00" in payload["text"]
    finally:
        history_path.unlink(missing_ok=True)


def test_orders_command_adds_next_button_when_multiple_pages_exist():
    """The initial /orders response should include a Next button when needed."""
    history_path = make_history_path()
    try:
        confirmer = build_confirmer(history_path)
        for index in range(6):
            confirmer.order_history_store.append(
                {
                    "timestamp": f"2026-04-22T16:00:0{index}Z",
                    "trade_type": "STOCK",
                    "underlying_symbol": f"SYM{index}",
                    "order_symbol": f"SYM{index}",
                    "side": "BUY",
                    "quantity": 10,
                    "estimated_cost": 1000.0,
                    "unit_price": 100.0,
                    "result": "SUCCESS",
                    "broker_status": "Routed",
                    "order_id": str(index),
                    "filled_price": None,
                    "failure_reason": None,
                    "decision_reasoning": "Paged entry",
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
        assert payload["reply_markup"]["inline_keyboard"][0][0]["text"] == "Next"
    finally:
        history_path.unlink(missing_ok=True)


def test_order_history_callback_edits_message_to_next_page():
    """Pressing the Next button should edit the same Telegram message to the next page."""
    history_path = make_history_path()
    try:
        confirmer = build_confirmer(history_path)
        for index in range(6):
            confirmer.order_history_store.append(
                {
                    "timestamp": f"2026-04-22T16:00:0{index}Z",
                    "trade_type": "STOCK",
                    "underlying_symbol": f"SYM{index}",
                    "order_symbol": f"SYM{index}",
                    "side": "BUY",
                    "quantity": 10,
                    "estimated_cost": 1000.0,
                    "unit_price": 100.0,
                    "result": "SUCCESS",
                    "broker_status": "Routed",
                    "order_id": str(index),
                    "filled_price": None,
                    "failure_reason": None,
                    "decision_reasoning": "Paged entry",
                }
            )

        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"ok": True, "result": True}
        confirmer.session.post.return_value = response
        confirmer._answer_callback_query = Mock()

        handled = confirmer._handle_order_history_callback(
            {
                "callback_query": {
                    "id": "cb1",
                    "data": "ORDER_HISTORY:PAGE:1:5",
                    "message": {
                        "chat": {"id": "123"},
                        "message_id": 42,
                    },
                }
            }
        )

        assert handled is True
        payload = confirmer.session.post.call_args.kwargs["json"]
        assert payload["message_id"] == 42
        assert "Order History (Page 2 of 2)" in payload["text"]
        assert "6. SYM0" in payload["text"]
    finally:
        history_path.unlink(missing_ok=True)
