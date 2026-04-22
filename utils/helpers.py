"""General-purpose helper utilities used across the project."""

from __future__ import annotations

from datetime import date, datetime, timezone
from math import floor
from typing import Any


def round_to_lot_size(quantity: float, lot_size: int = 1) -> int:
    """Round quantity down to the nearest allowed lot size."""
    if lot_size <= 0:
        raise ValueError("lot_size must be greater than zero")
    if quantity <= 0:
        return 0
    return int(floor(quantity / lot_size) * lot_size)


def format_occ_symbol(
    symbol: str,
    expiry: str | date | datetime,
    strike: float,
    option_type: str,
) -> str:
    """Return an OCC-formatted option symbol."""
    if not symbol or not symbol.strip():
        raise ValueError("symbol is required")
    if strike <= 0:
        raise ValueError("strike must be greater than zero")

    if isinstance(expiry, datetime):
        expiry_date = expiry.date()
    elif isinstance(expiry, date):
        expiry_date = expiry
    else:
        expiry_date = datetime.strptime(str(expiry), "%Y-%m-%d").date()

    normalized_type = option_type.strip().upper()
    if normalized_type not in {"C", "P", "CALL", "PUT"}:
        raise ValueError("option_type must be C, P, CALL, or PUT")

    contract_type = "C" if normalized_type.startswith("C") else "P"
    strike_component = int(round(strike * 1000))
    root_symbol = symbol.strip().upper().ljust(6)
    return (
        f"{root_symbol}"
        f"{expiry_date.strftime('%y%m%d')}"
        f"{contract_type}"
        f"{strike_component:08d}"
    )


def safe_get(data: dict[str, Any], *keys: str) -> Any:
    """Safely retrieve a nested value from a dictionary."""
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
        if current is None:
            return None
    return current


def extract_data(payload: Any) -> Any:
    """Extract the TastyTrade `data` wrapper when present."""
    if not isinstance(payload, dict):
        raise ValueError("Expected a JSON object response")
    return payload.get("data", payload)


def to_float(value: Any, default: float | None = None) -> float | None:
    """Convert a value to float while tolerating missing values."""
    if value in {None, ""}:
        return default
    return float(value)


def now_utc() -> datetime:
    """Return the current UTC datetime."""
    return datetime.now(timezone.utc)


def now_iso() -> str:
    """Return the current UTC timestamp in ISO 8601 format."""
    return now_utc().replace(microsecond=0).isoformat().replace("+00:00", "Z")
