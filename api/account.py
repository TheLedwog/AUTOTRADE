"""Account-focused TastyTrade API helpers."""

from __future__ import annotations

from typing import Any

import requests

from tastytrade_autotrader.auth.tastytrade_auth import TastyTradeAuth
from tastytrade_autotrader.config import AppConfig
from tastytrade_autotrader.utils.exceptions import MarketDataError
from tastytrade_autotrader.utils.helpers import extract_data, to_float


class AccountAPI:
    """Read account metadata, balances, and positions."""

    def __init__(self, auth: TastyTradeAuth, config: AppConfig) -> None:
        """Store shared dependencies."""
        self.auth = auth
        self.config = config

    def get_account_info(self) -> dict[str, Any]:
        """Return full account details for the configured account."""
        return self._get_json(f"/accounts/{self.config.tastytrade_account_number}")

    def get_balances(self) -> dict[str, Any]:
        """Return normalized buying power and net liquidation metrics."""
        payload = self._get_json(
            f"/accounts/{self.config.tastytrade_account_number}/balances"
        )
        buying_power = (
            to_float(payload.get("equity-buying-power"))
            or to_float(payload.get("derivative-buying-power"))
            or to_float(payload.get("cash-balance"))
            or 0.0
        )
        net_liquidation_value = (
            to_float(payload.get("net-liquidating-value"))
            or to_float(payload.get("liquid-net-worth"))
            or buying_power
        )
        return {
            "buying_power": round(buying_power, 2),
            "net_liquidation_value": round(net_liquidation_value, 2),
            "cash_balance": round(to_float(payload.get("cash-balance"), 0.0) or 0.0, 2),
            "raw": payload,
        }

    def get_positions(self) -> list[dict[str, Any]]:
        """Return all currently open positions."""
        payload = self._get_json(
            f"/accounts/{self.config.tastytrade_account_number}/positions"
        )
        items = payload.get("items")
        if isinstance(items, list):
            return items
        if isinstance(payload, list):
            return payload
        raise MarketDataError("Positions response did not include a position list")

    def _get_json(self, path: str) -> dict[str, Any]:
        """Execute a GET request and return the decoded payload."""
        try:
            response = self.auth.request("GET", path)
            payload = extract_data(response.json())
            if not isinstance(payload, dict):
                raise MarketDataError(f"Unexpected response type for {path}")
            return payload
        except requests.RequestException as exc:
            raise MarketDataError(f"Failed to retrieve account data from {path}: {exc}") from exc
        except ValueError as exc:
            raise MarketDataError(f"Invalid JSON payload returned from {path}: {exc}") from exc
