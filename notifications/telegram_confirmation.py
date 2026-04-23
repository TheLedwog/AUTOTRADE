"""Telegram-based trade confirmation workflow and order-history commands."""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from tastytrade_autotrader.config import AppConfig
from tastytrade_autotrader.utils.exceptions import TradeConfirmationError
from tastytrade_autotrader.utils.logger import get_logger
from tastytrade_autotrader.utils.order_history import OrderHistoryStore
from tastytrade_autotrader.utils.helpers import now_iso


@dataclass(frozen=True)
class TelegramConfirmationContext:
    """Metadata for a Telegram confirmation message that can be updated later."""

    confirmation_code: str
    message_id: int
    trade_details: dict[str, Any]


@dataclass
class PendingConfirmation:
    """In-memory state for a trade waiting on a Telegram decision."""

    event: threading.Event = field(default_factory=threading.Event)
    decision: str | None = None


class TelegramTradeConfirmer:
    """Request yes/no trade approval and expose order history via Telegram."""

    def __init__(
        self,
        config: AppConfig,
        session: requests.Session | None = None,
        order_history_store: OrderHistoryStore | None = None,
    ) -> None:
        """Store Telegram settings, HTTP session, and history storage."""
        self.config = config
        self.session = session or requests.Session()
        self.logger = get_logger(self.__class__.__name__)
        self.base_url = f"https://api.telegram.org/bot{self.config.telegram_bot_token}"
        self.order_history_store = order_history_store or OrderHistoryStore(
            Path(__file__).resolve().parent.parent / "logs" / "order_history.jsonl"
        )
        self._pending_confirmations: dict[str, PendingConfirmation] = {}
        self._pending_lock = threading.Lock()
        self._listener_started = False
        self._listener_lock = threading.Lock()
        self._listener_stop = threading.Event()
        self._next_update_offset: int | None = None

    @property
    def enabled(self) -> bool:
        """Return whether Telegram confirmation is enabled."""
        return self.config.telegram_confirmation_enabled

    def start_listener(self) -> None:
        """Start the Telegram long-poll listener once."""
        if not self.enabled:
            return

        with self._listener_lock:
            if self._listener_started:
                return
            try:
                self._next_update_offset = self._get_next_update_offset()
            except (requests.RequestException, TradeConfirmationError, ValueError) as exc:
                self.logger.warning(
                    "Telegram listener could not read initial update offset: %s",
                    exc,
                )
                self._next_update_offset = 0
            thread = threading.Thread(
                target=self._run_listener_loop,
                name="telegram-update-listener",
                daemon=True,
            )
            thread.start()
            self._listener_started = True

    def confirm_trade(self, details: dict[str, Any]) -> TelegramConfirmationContext | None:
        """Block until the user approves or rejects a pending trade."""
        if not self.enabled:
            return None

        try:
            self.start_listener()
            confirmation_code = secrets.token_hex(3).upper()
            message_id = self._send_message(
                self._build_confirmation_message(details),
                confirmation_code=confirmation_code,
            )
            context = TelegramConfirmationContext(
                confirmation_code=confirmation_code,
                message_id=message_id,
                trade_details=details,
            )
            pending = PendingConfirmation()
            with self._pending_lock:
                self._pending_confirmations[confirmation_code] = pending

            approved = pending.event.wait(
                timeout=self.config.telegram_confirmation_timeout_seconds
            )
            with self._pending_lock:
                self._pending_confirmations.pop(confirmation_code, None)

            if not approved:
                self._record_history(
                    details=details,
                    result="CANCELLED",
                    broker_status=None,
                    order_id=None,
                    filled_price=None,
                    failure_reason="Confirmation timed out before approval was received",
                )
                self._safe_edit_message(
                    message_id=context.message_id,
                    text=self._build_status_message(
                        header="ORDER CANCELLED",
                        details=details,
                        summary_lines=[
                            "Confirmation timed out before approval was received.",
                            "No order was submitted.",
                        ],
                    ),
                )
                raise TradeConfirmationError(
                    "Trade confirmation timed out before an approval was received"
                )

            if pending.decision == "YES":
                self._safe_edit_message(
                    message_id=context.message_id,
                    text=self._build_status_message(
                        header="TRADE APPROVED",
                        details=details,
                        summary_lines=[
                            "User approved the trade in Telegram.",
                            "Placing order now...",
                        ],
                    ),
                )
                return context

            self._record_history(
                details=details,
                result="CANCELLED",
                broker_status=None,
                order_id=None,
                filled_price=None,
                failure_reason="Trade was rejected through Telegram confirmation",
            )
            self._safe_edit_message(
                message_id=context.message_id,
                text=self._build_status_message(
                    header="ORDER CANCELLED",
                    details=details,
                    summary_lines=[
                        "User rejected the trade in Telegram.",
                        "No order was submitted.",
                    ],
                ),
            )
            raise TradeConfirmationError(
                "Trade was rejected through Telegram confirmation"
            )
        except requests.RequestException as exc:
            raise TradeConfirmationError(
                f"Telegram confirmation request failed: {exc}"
            ) from exc
        except ValueError as exc:
            raise TradeConfirmationError(
                f"Telegram confirmation returned invalid data: {exc}"
            ) from exc

    def notify_order_result(
        self,
        context: TelegramConfirmationContext | None,
        *,
        success: bool,
        order_result: dict[str, Any] | None = None,
        failure_reason: str | None = None,
    ) -> None:
        """Update the original Telegram message with the final order outcome."""
        if not self.enabled or context is None:
            return

        details = context.trade_details
        if success:
            order_result = order_result or {}
            status = str(order_result.get("status", "UNKNOWN"))
            order_id = str(order_result.get("order_id", ""))
            timestamp = str(order_result.get("timestamp", ""))
            filled_price = order_result.get("filled_price")
            summary_lines = [
                "ORDER HAS BEEN SUCCESSFULLY PLACED",
                f"Broker status: {status}",
            ]
            if order_id:
                summary_lines.append(f"Order ID: {order_id}")
            if filled_price is not None:
                summary_lines.append(f"Filled price: ${float(filled_price):.2f}")
            if timestamp:
                summary_lines.append(f"Timestamp: {timestamp}")
            self._record_history(
                details=details,
                result="SUCCESS",
                broker_status=status,
                order_id=order_id or None,
                filled_price=filled_price,
                failure_reason=None,
            )
        else:
            summary_lines = [
                "ORDER FAILED",
                f"Cause: {failure_reason or 'Unknown error'}",
            ]
            self._record_history(
                details=details,
                result="FAILED",
                broker_status=None,
                order_id=None,
                filled_price=None,
                failure_reason=failure_reason or "Unknown error",
            )

        self._edit_message(
            message_id=context.message_id,
            text=self._build_status_message(
                header=summary_lines[0],
                details=details,
                summary_lines=summary_lines[1:],
            ),
        )

    def _run_listener_loop(self) -> None:
        """Continuously poll Telegram for commands and confirmation responses."""
        while not self._listener_stop.is_set():
            try:
                updates = self._get_updates(
                    self._next_update_offset,
                    timeout=self.config.telegram_poll_timeout_seconds,
                )
                for update in updates:
                    self._next_update_offset = max(
                        self._next_update_offset or 0,
                        int(update["update_id"]) + 1,
                    )
                    self._handle_update(update)
            except (requests.RequestException, TradeConfirmationError, ValueError) as exc:
                self.logger.warning("Telegram listener polling failed: %s", exc)
                time.sleep(0.5)

    def _handle_update(self, update: dict[str, Any]) -> None:
        """Handle slash commands and pending confirmation responses."""
        if self._handle_command(update):
            return
        if self._handle_order_history_callback(update):
            return
        self._handle_confirmation_response(update)

    def _handle_command(self, update: dict[str, Any]) -> bool:
        """Respond to supported Telegram slash commands."""
        message = update.get("message")
        if not isinstance(message, dict):
            return False

        chat = message.get("chat")
        if not isinstance(chat, dict):
            return False

        chat_id = str(chat.get("id", "")).strip()
        if chat_id != str(self.config.telegram_chat_id):
            return False

        text = str(message.get("text", "")).strip()
        if not text.startswith("/orders"):
            return False

        parts = text.split()
        page_size = 5
        if len(parts) > 1:
            try:
                page_size = max(1, min(int(parts[1]), 20))
            except ValueError:
                page_size = 5

        try:
            message_text, reply_markup = self._build_order_history_message(
                page=0,
                page_size=page_size,
            )
        except Exception as exc:  # pragma: no cover - defensive Telegram command path
            self.logger.warning("Failed to build Telegram order history: %s", exc)
            message_text = (
                "I couldn't render the order history cleanly right now. "
                "Please try again in a moment."
            )
            reply_markup = None

        try:
            self._send_message(message_text, None, reply_markup=reply_markup)
        except (requests.RequestException, TradeConfirmationError, ValueError) as exc:
            self.logger.warning("Failed to send Telegram /orders response: %s", exc)
        return True

    def _handle_order_history_callback(self, update: dict[str, Any]) -> bool:
        """Handle Previous/Next pagination callbacks for Telegram order history."""
        callback_query = update.get("callback_query")
        if not isinstance(callback_query, dict):
            return False

        message = callback_query.get("message")
        if not isinstance(message, dict):
            return False

        chat = message.get("chat")
        if not isinstance(chat, dict):
            return False

        chat_id = str(chat.get("id", "")).strip()
        if chat_id != str(self.config.telegram_chat_id):
            return False

        callback_data = str(callback_query.get("data", "")).strip()
        if not callback_data.startswith("ORDER_HISTORY:"):
            return False

        parts = callback_data.split(":")
        if len(parts) != 4:
            return False

        try:
            page = max(0, int(parts[2]))
            page_size = max(1, min(int(parts[3]), 20))
        except ValueError:
            return False

        try:
            message_text, reply_markup = self._build_order_history_message(
                page=page,
                page_size=page_size,
            )
            self._edit_message(
                message_id=int(message.get("message_id")),
                text=message_text,
                reply_markup=reply_markup,
            )
            self._safe_answer_callback_query(
                callback_query.get("id"),
                f"Showing page {page + 1}",
            )
        except (requests.RequestException, TradeConfirmationError, ValueError) as exc:
            self.logger.warning("Failed to paginate Telegram order history: %s", exc)
            self._safe_answer_callback_query(
                callback_query.get("id"),
                "Could not load that order-history page.",
            )
        return True

    def _handle_confirmation_response(self, update: dict[str, Any]) -> None:
        """Apply a Telegram yes/no response to the matching pending trade."""
        response = self._extract_confirmation_response(update)
        if response is None:
            return

        confirmation_code, decision, callback_query_id = response
        with self._pending_lock:
            pending = self._pending_confirmations.get(confirmation_code)
        if pending is None:
            return

        pending.decision = decision
        pending.event.set()

        if callback_query_id is not None:
            callback_text = "Trade approved" if decision == "YES" else "Trade rejected"
            self._safe_answer_callback_query(callback_query_id, callback_text)

    def _build_confirmation_message(self, details: dict[str, Any]) -> str:
        """Render the initial Telegram confirmation request."""
        return self._build_status_message(
            header="TRADE CONFIRMATION REQUIRED",
            details=details,
            summary_lines=[
                "Press Yes to approve or No to cancel.",
                "Use /orders at any time to view recent order history.",
            ],
        )

    def _build_status_message(
        self,
        *,
        header: str,
        details: dict[str, Any],
        summary_lines: list[str],
    ) -> str:
        """Render a Telegram message body describing the trade and current status."""
        lines = [
            header,
            "",
            f"Trade type: {details['trade_type']}",
            f"Underlying symbol: {details['underlying_symbol']}",
            f"Order symbol: {details['order_symbol']}",
            f"Side: {details['side']}",
            f"Quantity: {details['quantity']}",
            f"Estimated unit price: ${details['unit_price']:.2f}",
            f"Estimated total cost: ${details['estimated_cost']:.2f}",
            "",
            "Reasoning:",
            details["decision_reasoning"],
            "",
        ]
        lines.extend(summary_lines)
        return "\n".join(lines)

    def _build_order_history_message(
        self,
        *,
        page: int,
        page_size: int,
    ) -> tuple[str, dict[str, Any] | None]:
        """Render paginated recent order history for the /orders Telegram command."""
        all_entries = list(reversed(self.order_history_store.recent(
            limit=self.order_history_store.max_entries
        )))
        total_entries = len(all_entries)
        total_pages = max((total_entries + page_size - 1) // page_size, 1)
        current_page = min(max(page, 0), total_pages - 1)
        start = current_page * page_size
        end = start + page_size
        entries = all_entries[start:end]
        if not entries:
            return "No order history has been recorded yet.", None

        lines = [f"Order History (Page {current_page + 1} of {total_pages})", ""]
        for index, entry in enumerate(entries, start=1):
            lines.append(f"{start + index}. {self._format_history_symbol(entry)}")
            lines.append(f"   Time: {self._format_history_timestamp(entry.get('timestamp'))}")
            lines.append(f"   Status: {self._format_history_status(entry)}")
            lines.append(f"   Action: {self._format_history_action(entry)}")
            lines.append(f"   Est. Cost: {self._format_history_money(entry.get('estimated_cost'))}")
            order_id = str(entry.get("order_id") or "").strip()
            if order_id:
                lines.append(f"   Order ID: {order_id}")
            failure_reason = str(entry.get("failure_reason") or "").strip()
            if failure_reason:
                lines.append(f"   Note: {failure_reason}")
            lines.append("")

        buttons: list[dict[str, str]] = []
        if current_page > 0:
            buttons.append(
                {
                    "text": "Previous",
                    "callback_data": (
                        f"ORDER_HISTORY:PAGE:{current_page - 1}:{page_size}"
                    ),
                }
            )
        if current_page < total_pages - 1:
            buttons.append(
                {
                    "text": "Next",
                    "callback_data": (
                        f"ORDER_HISTORY:PAGE:{current_page + 1}:{page_size}"
                    ),
                }
            )

        reply_markup = {"inline_keyboard": [buttons]} if buttons else None
        return "\n".join(lines).rstrip(), reply_markup

    @staticmethod
    def _format_history_symbol(entry: dict[str, Any]) -> str:
        """Return a readable symbol label for an order-history entry."""
        order_symbol = str(entry.get("order_symbol") or "").strip()
        underlying_symbol = str(entry.get("underlying_symbol") or "").strip()
        if order_symbol and underlying_symbol and order_symbol != underlying_symbol:
            return f"{underlying_symbol} ({order_symbol})"
        return order_symbol or underlying_symbol or "Unknown symbol"

    @staticmethod
    def _format_history_timestamp(value: Any) -> str:
        """Return a human-readable UTC timestamp for Telegram history output."""
        raw_value = str(value or "").strip()
        if not raw_value:
            return "Unknown"

        try:
            parsed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
        except ValueError:
            return raw_value

        utc_value = parsed.astimezone(timezone.utc)
        return utc_value.strftime("%d %b %Y %H:%M UTC")

    @staticmethod
    def _format_history_status(entry: dict[str, Any]) -> str:
        """Return a friendlier status label for Telegram history."""
        status_map = {
            "SUCCESS": "Successful",
            "FAILED": "Failed",
            "CANCELLED": "Cancelled",
        }
        result = status_map.get(str(entry.get("result", "")).upper(), "Unknown")
        broker_status = str(entry.get("broker_status") or "").strip()
        if broker_status:
            return f"{result} | Broker: {broker_status}"
        return result

    @staticmethod
    def _format_history_action(entry: dict[str, Any]) -> str:
        """Return a concise action summary for Telegram history."""
        trade_type = str(entry.get("trade_type") or "").strip().title() or "Trade"
        side = str(entry.get("side") or "").strip().title() or "Unknown"
        order_symbol = str(entry.get("order_symbol") or "").strip() or "Unknown symbol"
        quantity = TelegramTradeConfirmer._safe_int(entry.get("quantity"))
        return f"{trade_type} {side} {order_symbol} x{quantity:,}"

    @staticmethod
    def _format_history_money(value: Any) -> str:
        """Return a safe human-readable currency string."""
        amount = TelegramTradeConfirmer._safe_float(value)
        return f"${amount:,.2f}"

    @staticmethod
    def _safe_float(value: Any) -> float:
        """Best-effort float conversion for legacy history rows."""
        if value in {None, ""}:
            return 0.0
        try:
            return float(str(value).replace(",", "").strip())
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _safe_int(value: Any) -> int:
        """Best-effort integer conversion for legacy history rows."""
        if value in {None, ""}:
            return 0
        try:
            return int(float(str(value).replace(",", "").strip()))
        except (TypeError, ValueError):
            return 0

    def _record_history(
        self,
        *,
        details: dict[str, Any],
        result: str,
        broker_status: str | None,
        order_id: str | None,
        filled_price: Any,
        failure_reason: str | None,
    ) -> None:
        """Persist a compact summary of a completed or cancelled trade flow."""
        entry = {
            "timestamp": now_iso(),
            "trade_type": details["trade_type"],
            "underlying_symbol": details["underlying_symbol"],
            "order_symbol": details["order_symbol"],
            "side": details["side"],
            "quantity": int(details["quantity"]),
            "estimated_cost": round(float(details["estimated_cost"]), 2),
            "unit_price": round(float(details["unit_price"]), 2),
            "result": result,
            "broker_status": broker_status,
            "order_id": order_id,
            "filled_price": (
                round(float(filled_price), 2) if filled_price is not None else None
            ),
            "failure_reason": failure_reason,
            "decision_reasoning": details["decision_reasoning"],
        }
        self.order_history_store.append(entry)

    def _send_message(
        self,
        text: str,
        confirmation_code: str | None,
        *,
        reply_markup: dict[str, Any] | None = None,
    ) -> int:
        """Send a Telegram message and return its message ID."""
        payload: dict[str, Any] = {
            "chat_id": self.config.telegram_chat_id,
            "text": text,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        elif confirmation_code is not None:
            payload["reply_markup"] = {
                "inline_keyboard": [
                    [
                        {
                            "text": "Yes",
                            "callback_data": f"TRADE_CONFIRM:YES:{confirmation_code}",
                        },
                        {
                            "text": "No",
                            "callback_data": f"TRADE_CONFIRM:NO:{confirmation_code}",
                        },
                    ]
                ]
            }

        response = self.session.post(
            f"{self.base_url}/sendMessage",
            json=payload,
            timeout=self.config.telegram_request_timeout_seconds,
        )
        response.raise_for_status()
        body = response.json()
        if not body.get("ok", False):
            raise TradeConfirmationError(
                f"Telegram sendMessage failed: {body.get('description', body)}"
            )
        result = body.get("result")
        if not isinstance(result, dict) or "message_id" not in result:
            raise TradeConfirmationError("Telegram sendMessage returned no message_id")
        return int(result["message_id"])

    def _edit_message(
        self,
        *,
        message_id: int,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> None:
        """Edit a previously sent Telegram message and clear inline buttons."""
        payload: dict[str, Any] = {
            "chat_id": self.config.telegram_chat_id,
            "message_id": message_id,
            "text": text,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup

        response = self.session.post(
            f"{self.base_url}/editMessageText",
            json=payload,
            timeout=self.config.telegram_request_timeout_seconds,
        )
        response.raise_for_status()
        body = response.json()
        if not body.get("ok", False):
            raise TradeConfirmationError(
                f"Telegram editMessageText failed: {body.get('description', body)}"
            )

    def _safe_edit_message(self, *, message_id: int, text: str) -> None:
        """Try to edit a Telegram message without blocking the trade flow on failure."""
        try:
            self._edit_message(message_id=message_id, text=text)
        except (requests.RequestException, TradeConfirmationError, ValueError) as exc:
            self.logger.warning("Telegram message edit failed: %s", exc)

    def _get_next_update_offset(self) -> int:
        """Return the update offset after the latest visible Telegram update."""
        updates = self._get_updates(offset=None, timeout=1)
        if not updates:
            return 0
        return max(int(update["update_id"]) for update in updates) + 1

    def _get_updates(
        self,
        offset: int | None,
        timeout: int,
    ) -> list[dict[str, Any]]:
        """Fetch Telegram updates using long polling."""
        payload: dict[str, Any] = {
            "timeout": timeout,
            "allowed_updates": ["message", "callback_query"],
        }
        if offset is not None:
            payload["offset"] = offset

        response = self.session.post(
            f"{self.base_url}/getUpdates",
            json=payload,
            timeout=timeout + 5,
        )
        response.raise_for_status()
        body = response.json()
        if not body.get("ok", False):
            raise TradeConfirmationError(
                f"Telegram getUpdates failed: {body.get('description', body)}"
            )
        result = body.get("result")
        if not isinstance(result, list):
            raise TradeConfirmationError("Telegram getUpdates returned an invalid payload")
        return result

    def _extract_confirmation_response(
        self,
        update: dict[str, Any],
    ) -> tuple[str, str, str | None] | None:
        """Parse inline button or text confirmation replies from Telegram."""
        callback_query = update.get("callback_query")
        if isinstance(callback_query, dict):
            callback_message = callback_query.get("message")
            if not isinstance(callback_message, dict):
                return None

            chat = callback_message.get("chat")
            if not isinstance(chat, dict):
                return None

            chat_id = str(chat.get("id", "")).strip()
            if chat_id != str(self.config.telegram_chat_id):
                return None

            callback_data = str(callback_query.get("data", "")).strip().upper()
            if callback_data.startswith("TRADE_CONFIRM:YES:"):
                return (
                    callback_data.rsplit(":", maxsplit=1)[-1],
                    "YES",
                    str(callback_query.get("id") or ""),
                )
            if callback_data.startswith("TRADE_CONFIRM:NO:"):
                return (
                    callback_data.rsplit(":", maxsplit=1)[-1],
                    "NO",
                    str(callback_query.get("id") or ""),
                )
            return None

        message = update.get("message")
        if not isinstance(message, dict):
            return None

        chat = message.get("chat")
        if not isinstance(chat, dict):
            return None

        chat_id = str(chat.get("id", "")).strip()
        if chat_id != str(self.config.telegram_chat_id):
            return None

        text = str(message.get("text", "")).strip().upper()
        if text.startswith("YES "):
            return (text.split(maxsplit=1)[1], "YES", None)
        if text.startswith("NO "):
            return (text.split(maxsplit=1)[1], "NO", None)
        return None

    def _answer_callback_query(self, callback_query_id: str | None, text: str) -> None:
        """Acknowledge a Telegram button click so the client stops spinning."""
        if not callback_query_id:
            return

        response = self.session.post(
            f"{self.base_url}/answerCallbackQuery",
            json={
                "callback_query_id": callback_query_id,
                "text": text,
                "show_alert": False,
            },
            timeout=self.config.telegram_request_timeout_seconds,
        )
        response.raise_for_status()
        body = response.json()
        if not body.get("ok", False):
            raise TradeConfirmationError(
                f"Telegram answerCallbackQuery failed: {body.get('description', body)}"
            )

    def _safe_answer_callback_query(
        self,
        callback_query_id: str | None,
        text: str,
    ) -> None:
        """Best-effort callback acknowledgement that does not block valid decisions."""
        try:
            self._answer_callback_query(callback_query_id, text)
        except (requests.RequestException, TradeConfirmationError, ValueError) as exc:
            self.logger.warning("Telegram callback acknowledgement failed: %s", exc)
