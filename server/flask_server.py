"""Flask API server for receiving signals and exposing service status."""

from __future__ import annotations

import hmac
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request

from tastytrade_autotrader.api.account import AccountAPI
from tastytrade_autotrader.api.orders import OrdersAPI
from tastytrade_autotrader.api.market_data import MarketDataAPI
from tastytrade_autotrader.auth.tastytrade_auth import TastyTradeAuth
from tastytrade_autotrader.config import AppConfig, get_config
from tastytrade_autotrader.logic.trade_decision import TradeDecisionEngine
from tastytrade_autotrader.notifications.telegram_confirmation import (
    TelegramTradeConfirmer,
)
from tastytrade_autotrader.signal.signal_parser import SignalParser
from tastytrade_autotrader.utils.exceptions import (
    AuthenticationError,
    InsufficientFundsError,
    MarketDataError,
    OrderPlacementError,
    SignalParseError,
    TradeConfirmationError,
)
from tastytrade_autotrader.utils.logger import get_logger


def create_app(
    config: AppConfig | None = None,
    *,
    auth: TastyTradeAuth | None = None,
    account_api: AccountAPI | None = None,
    market_data_api: MarketDataAPI | None = None,
    orders_api: OrdersAPI | None = None,
    signal_parser: SignalParser | None = None,
    decision_engine: TradeDecisionEngine | None = None,
    trade_confirmer: TelegramTradeConfirmer | None = None,
) -> Flask:
    """Create the Flask application and wire up dependencies."""
    config = config or get_config()
    auth = auth or TastyTradeAuth(config)
    account_api = account_api or AccountAPI(auth, config)
    market_data_api = market_data_api or MarketDataAPI(auth, config)
    orders_api = orders_api or OrdersAPI(auth, config)
    signal_parser = signal_parser or SignalParser()
    trade_confirmer = trade_confirmer or (
        TelegramTradeConfirmer(config)
        if config.telegram_confirmation_enabled
        else None
    )
    if trade_confirmer is not None:
        trade_confirmer.start_listener()
    decision_engine = decision_engine or TradeDecisionEngine(
        account_api=account_api,
        market_data_api=market_data_api,
        orders_api=orders_api,
        config=config,
        trade_confirmer=trade_confirmer,
    )

    app = Flask(__name__)
    logger = get_logger(__name__)
    started_at = datetime.now(timezone.utc)
    app.config["AUTOTRADER_STARTED_AT"] = started_at
    app.config["LAST_SIGNAL_RECEIVED_AT"] = None

    @app.before_request
    def verify_api_key() -> Any:
        """Require a static API key for every request."""
        provided = request.headers.get("X-API-Key", "")
        if not hmac.compare_digest(provided, config.api_key):
            return _json_error("Unauthorized", 401)
        return None

    @app.post("/signal")
    def post_signal() -> Any:
        """Parse and execute a trade signal."""
        payload = request.get_json(silent=True)
        if payload is None:
            return _json_error("Request body must be valid JSON", 400)

        try:
            signal = signal_parser.parse(payload)
            result = decision_engine.decide_and_execute(signal)
            app.config["LAST_SIGNAL_RECEIVED_AT"] = datetime.now(timezone.utc).isoformat()
            return jsonify(result), 200
        except (
            SignalParseError,
            AuthenticationError,
            MarketDataError,
            OrderPlacementError,
            InsufficientFundsError,
            TradeConfirmationError,
            ValueError,
        ) as exc:
            logger.warning("Signal processing failed: %s", exc)
            return _json_error(str(exc), 400)

    @app.get("/status")
    def get_status() -> Any:
        """Return application health information."""
        auth_status = False
        auth_error = auth.last_auth_error
        balance_snapshot: dict[str, Any] = {}
        try:
            auth.authenticate()
            auth_status = True
            balance_snapshot = account_api.get_balances()
        except Exception as exc:  # pragma: no cover - defensive endpoint handling
            auth_error = str(exc)

        uptime_seconds = int(
            (datetime.now(timezone.utc) - app.config["AUTOTRADER_STARTED_AT"]).total_seconds()
        )
        response = {
            "auth_status": auth_status,
            "account_balance": balance_snapshot,
            "last_signal_received_at": app.config["LAST_SIGNAL_RECEIVED_AT"],
            "service_uptime_seconds": uptime_seconds,
            "auth_error": auth_error,
            "telegram_confirmation_enabled": config.telegram_confirmation_enabled,
            "dry_run": config.dry_run,
            "allow_sandbox_quote_fallback": config.allow_sandbox_quote_fallback,
            "allocation_base": config.allocation_base,
            "tastytrade_base_url": config.tastytrade_base_url,
        }
        return jsonify(response), 200

    @app.get("/positions")
    def get_positions() -> Any:
        """Return open account positions."""
        try:
            return jsonify({"positions": account_api.get_positions()}), 200
        except (AuthenticationError, MarketDataError, ValueError) as exc:
            return _json_error(str(exc), 400)

    @app.get("/logs")
    def get_logs() -> Any:
        """Return the last 100 log lines as JSON."""
        log_path = Path(__file__).resolve().parent.parent / "logs" / "autotrader.log"
        if not log_path.exists():
            return jsonify({"lines": []}), 200

        lines = log_path.read_text(encoding="utf-8").splitlines()[-100:]
        return jsonify({"lines": lines}), 200

    @app.errorhandler(Exception)
    def handle_uncaught_error(exc: Exception) -> Any:
        """Return a structured JSON error instead of a raw traceback."""
        logger.exception("Unhandled server error")
        return _json_error(f"Internal server error: {exc}", 500)

    return app


def _json_error(message: str, status_code: int) -> tuple[Any, int]:
    """Build a structured JSON error response."""
    return jsonify({"error": message, "status": status_code}), status_code
