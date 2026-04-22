"""Signal parsing and normalization."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional, Union

from tastytrade_autotrader.utils.exceptions import SignalParseError


@dataclass
class TradeSignal:
    """Normalized trade signal consumed by the decision engine."""

    symbol: str
    direction: str
    signal_type: str
    allocation: float
    confidence: Optional[float]
    expiration_hint: Optional[str]
    strike_hint: Optional[float]
    raw: dict[str, Any]


class SignalParser:
    """Parse incoming JSON payloads into a validated TradeSignal."""

    def parse(self, raw_input: Union[str, dict[str, Any]]) -> TradeSignal:
        """Parse a raw signal payload."""
        payload = self._load_payload(raw_input)

        symbol = self._required_string(payload, "symbol")
        direction = self._normalize_direction(self._required_string(payload, "direction"))
        signal_type = self._normalize_signal_type(self._required_string(payload, "signal_type"))
        allocation = self._parse_allocation(payload.get("allocation"))
        confidence = self._parse_optional_float(payload.get("confidence"), "confidence")
        expiration_hint = self._parse_optional_date(payload.get("expiration_hint"))
        strike_hint = self._parse_optional_float(payload.get("strike_hint"), "strike_hint")

        return TradeSignal(
            symbol=symbol.upper(),
            direction=direction,
            signal_type=signal_type,
            allocation=allocation,
            confidence=confidence,
            expiration_hint=expiration_hint,
            strike_hint=strike_hint,
            raw=payload,
        )

    @staticmethod
    def _load_payload(raw_input: Union[str, dict[str, Any]]) -> dict[str, Any]:
        """Load a raw dictionary or JSON string into a dictionary."""
        if isinstance(raw_input, dict):
            return raw_input
        if not isinstance(raw_input, str):
            raise SignalParseError("Signal payload must be a dict or JSON string")
        try:
            payload = json.loads(raw_input)
        except json.JSONDecodeError as exc:
            raise SignalParseError(f"Malformed JSON string: {exc.msg}") from exc
        if not isinstance(payload, dict):
            raise SignalParseError("Signal payload must decode to a JSON object")
        return payload

    @staticmethod
    def _required_string(payload: dict[str, Any], field_name: str) -> str:
        """Return a required string field or raise a field-specific error."""
        value = payload.get(field_name)
        if not isinstance(value, str) or not value.strip():
            raise SignalParseError(f"Missing or invalid required field: {field_name}")
        return value.strip()

    @staticmethod
    def _normalize_direction(raw_direction: str) -> str:
        """Normalize direction to BUY or SELL."""
        normalized = raw_direction.strip().upper()
        if normalized not in {"BUY", "SELL"}:
            raise SignalParseError("direction must be either BUY or SELL")
        return normalized

    @staticmethod
    def _normalize_signal_type(raw_signal_type: str) -> str:
        """Normalize signal type to STOCK or OPTION."""
        normalized = raw_signal_type.strip().upper()
        if normalized not in {"STOCK", "OPTION"}:
            raise SignalParseError("signal_type must be either STOCK or OPTION")
        return normalized

    @staticmethod
    def _parse_allocation(value: Any) -> float:
        """Parse allocation as a decimal fraction from 0 to 1 inclusive."""
        if value is None:
            raise SignalParseError("Missing required field: allocation")
        try:
            allocation = float(value)
        except (TypeError, ValueError) as exc:
            raise SignalParseError("allocation must be a numeric value") from exc

        if allocation <= 0 or allocation > 1:
            raise SignalParseError("allocation must be greater than 0 and at most 1")
        return round(allocation, 4)

    @staticmethod
    def _parse_optional_float(value: Any, field_name: str) -> Optional[float]:
        """Parse an optional float field."""
        if value in {None, ""}:
            return None
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise SignalParseError(f"{field_name} must be numeric when provided") from exc

    @staticmethod
    def _parse_optional_date(value: Any) -> Optional[str]:
        """Validate an optional expiration hint."""
        if value in {None, ""}:
            return None
        try:
            return datetime.strptime(str(value), "%Y-%m-%d").date().isoformat()
        except ValueError as exc:
            raise SignalParseError(
                "expiration_hint must use YYYY-MM-DD format"
            ) from exc
