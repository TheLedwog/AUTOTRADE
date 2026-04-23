"""Environment-backed configuration for the autotrader service."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_BASE_URL = "https://api.cert.tastyworks.com"
PRODUCTION_BASE_URL = "https://api.tastyworks.com"
ALLOWED_BASE_URLS = {DEFAULT_BASE_URL, PRODUCTION_BASE_URL}
ALLOWED_ALLOCATION_BASES = {
    "net_liquidation_value",
    "buying_power",
    "cash_balance",
}


def _parse_bool(value: str | None, default: bool = False) -> bool:
    """Parse a boolean-like environment variable."""
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _require_env(name: str) -> str:
    """Return a required environment variable or raise a helpful error."""
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


@dataclass(frozen=True)
class AppConfig:
    """Immutable application configuration."""

    tastytrade_username: str
    tastytrade_password: str
    tastytrade_account_number: str
    tastytrade_base_url: str
    allocation_base: str
    options_cost_threshold: float
    flask_port: int
    flask_host: str
    dry_run: bool
    allow_sandbox_quote_fallback: bool
    log_level: str
    api_key: str
    telegram_confirmation_enabled: bool
    telegram_bot_token: str | None
    telegram_chat_id: str | None
    telegram_confirmation_timeout_seconds: int
    telegram_request_timeout_seconds: float
    telegram_poll_timeout_seconds: int
    dry_run_fallback_stock_price: float
    dry_run_fallback_option_price: float
    request_timeout: float = 15.0
    user_agent: str = "tastytrade_autotrader/1.0.0"

    @classmethod
    def from_env(cls) -> "AppConfig":
        """Build configuration from environment variables."""
        load_dotenv(PROJECT_ROOT / ".env")

        base_url = os.getenv("TASTYTRADE_BASE_URL", DEFAULT_BASE_URL).strip().rstrip("/")
        if base_url not in ALLOWED_BASE_URLS:
            raise ValueError(
                "TASTYTRADE_BASE_URL must be one of "
                f"{sorted(ALLOWED_BASE_URLS)}; got {base_url!r}"
            )

        allocation_base = (
            os.getenv("ALLOCATION_BASE", "net_liquidation_value").strip().lower()
        )
        if allocation_base not in ALLOWED_ALLOCATION_BASES:
            raise ValueError(
                "ALLOCATION_BASE must be one of "
                f"{sorted(ALLOWED_ALLOCATION_BASES)}; got {allocation_base!r}"
            )

        options_cost_threshold = round(
            float(os.getenv("OPTIONS_COST_THRESHOLD", "210.0")),
            2,
        )
        if options_cost_threshold <= 0:
            raise ValueError("OPTIONS_COST_THRESHOLD must be greater than zero")

        flask_port = int(os.getenv("FLASK_PORT", "5000"))
        if flask_port <= 0 or flask_port > 65535:
            raise ValueError("FLASK_PORT must be between 1 and 65535")

        telegram_confirmation_enabled = _parse_bool(
            os.getenv("TELEGRAM_CONFIRMATION_ENABLED"),
            default=False,
        )
        telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip() or None
        telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip() or None
        telegram_confirmation_timeout_seconds = int(
            os.getenv("TELEGRAM_CONFIRMATION_TIMEOUT_SECONDS", "300")
        )
        if telegram_confirmation_timeout_seconds <= 0:
            raise ValueError(
                "TELEGRAM_CONFIRMATION_TIMEOUT_SECONDS must be greater than zero"
            )
        telegram_request_timeout_seconds = float(
            os.getenv("TELEGRAM_REQUEST_TIMEOUT_SECONDS", "60")
        )
        if telegram_request_timeout_seconds <= 0:
            raise ValueError(
                "TELEGRAM_REQUEST_TIMEOUT_SECONDS must be greater than zero"
            )
        telegram_poll_timeout_seconds = int(
            os.getenv("TELEGRAM_POLL_TIMEOUT_SECONDS", "1")
        )
        if telegram_poll_timeout_seconds <= 0:
            raise ValueError(
                "TELEGRAM_POLL_TIMEOUT_SECONDS must be greater than zero"
            )

        if telegram_confirmation_enabled:
            if not telegram_bot_token:
                raise ValueError(
                    "TELEGRAM_BOT_TOKEN is required when "
                    "TELEGRAM_CONFIRMATION_ENABLED=True"
                )
            if not telegram_chat_id:
                raise ValueError(
                    "TELEGRAM_CHAT_ID is required when "
                    "TELEGRAM_CONFIRMATION_ENABLED=True"
                )

        dry_run_fallback_stock_price = round(
            float(os.getenv("DRY_RUN_FALLBACK_STOCK_PRICE", "100.0")),
            2,
        )
        if dry_run_fallback_stock_price <= 0:
            raise ValueError("DRY_RUN_FALLBACK_STOCK_PRICE must be greater than zero")

        dry_run_fallback_option_price = round(
            float(os.getenv("DRY_RUN_FALLBACK_OPTION_PRICE", "2.0")),
            2,
        )
        if dry_run_fallback_option_price <= 0:
            raise ValueError("DRY_RUN_FALLBACK_OPTION_PRICE must be greater than zero")

        return cls(
            tastytrade_username=_require_env("TASTYTRADE_USERNAME"),
            tastytrade_password=_require_env("TASTYTRADE_PASSWORD"),
            tastytrade_account_number=_require_env("TASTYTRADE_ACCOUNT_NUMBER"),
            tastytrade_base_url=base_url,
            allocation_base=allocation_base,
            options_cost_threshold=options_cost_threshold,
            flask_port=flask_port,
            flask_host=os.getenv("FLASK_HOST", "0.0.0.0").strip() or "0.0.0.0",
            dry_run=_parse_bool(os.getenv("DRY_RUN"), default=False),
            allow_sandbox_quote_fallback=_parse_bool(
                os.getenv("ALLOW_SANDBOX_QUOTE_FALLBACK"),
                default=False,
            ),
            log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
            api_key=_require_env("API_KEY"),
            telegram_confirmation_enabled=telegram_confirmation_enabled,
            telegram_bot_token=telegram_bot_token,
            telegram_chat_id=telegram_chat_id,
            telegram_confirmation_timeout_seconds=telegram_confirmation_timeout_seconds,
            telegram_request_timeout_seconds=telegram_request_timeout_seconds,
            telegram_poll_timeout_seconds=telegram_poll_timeout_seconds,
            dry_run_fallback_stock_price=dry_run_fallback_stock_price,
            dry_run_fallback_option_price=dry_run_fallback_option_price,
        )


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    """Return a cached config instance."""
    return AppConfig.from_env()
