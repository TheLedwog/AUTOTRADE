"""Order placement helpers for stock and option trades."""

from __future__ import annotations

import time
from typing import Any

import requests

from tastytrade_autotrader.auth.tastytrade_auth import TastyTradeAuth
from tastytrade_autotrader.config import AppConfig
from tastytrade_autotrader.utils.exceptions import OrderPlacementError
from tastytrade_autotrader.utils.helpers import extract_data, now_iso, to_float
from tastytrade_autotrader.utils.logger import get_logger


class OrdersAPI:
    """Place and manage TastyTrade orders."""

    VALID_SIDES = {"BUY", "SELL"}
    VALID_ORDER_TYPES = {"MARKET", "LIMIT"}

    def __init__(self, auth: TastyTradeAuth, config: AppConfig) -> None:
        """Store shared dependencies."""
        self.auth = auth
        self.config = config
        self.logger = get_logger(self.__class__.__name__)

    def place_stock_order(
        self,
        symbol: str,
        quantity: int,
        side: str,
        order_type: str = "Market",
    ) -> dict[str, Any]:
        """Place a stock order."""
        payload = self._build_payload(
            symbol=symbol,
            quantity=quantity,
            side=side,
            order_type=order_type,
            instrument_type="Equity",
        )
        return self._submit_order(payload)

    def place_option_order(
        self,
        option_symbol: str,
        quantity: int,
        side: str,
        order_type: str = "Market",
    ) -> dict[str, Any]:
        """Place an option order."""
        payload = self._build_payload(
            symbol=option_symbol,
            quantity=quantity,
            side=side,
            order_type=order_type,
            instrument_type="Equity Option",
        )
        return self._submit_order(payload)

    def get_order_status(self, order_id: str) -> dict[str, Any]:
        """Return a structured order status response."""
        if not str(order_id).strip():
            raise OrderPlacementError("order_id must be a non-empty string")

        path = f"/accounts/{self.config.tastytrade_account_number}/orders/{order_id}"
        try:
            response = self.auth.request("GET", path)
            payload = extract_data(response.json())
            order_payload = payload.get("order", payload)
            return self._normalize_order_response(order_payload)
        except requests.HTTPError as exc:
            raise OrderPlacementError(
                f"Failed to fetch order {order_id}: HTTP {exc.response.status_code}"
            ) from exc
        except requests.RequestException as exc:
            raise OrderPlacementError(f"Failed to fetch order {order_id}: {exc}") from exc
        except ValueError as exc:
            raise OrderPlacementError(
                f"Invalid order status response for {order_id}: {exc}"
            ) from exc

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        """Cancel an existing order."""
        if not str(order_id).strip():
            raise OrderPlacementError("order_id must be a non-empty string")

        path = f"/accounts/{self.config.tastytrade_account_number}/orders/{order_id}"
        try:
            response = self.auth.request("DELETE", path)
            payload = extract_data(response.json())
            order_payload = payload.get("order", payload)
            return self._normalize_order_response(order_payload, default_status="CANCELLED")
        except requests.HTTPError as exc:
            raise OrderPlacementError(
                f"Failed to cancel order {order_id}: HTTP {exc.response.status_code}"
            ) from exc
        except requests.RequestException as exc:
            raise OrderPlacementError(f"Failed to cancel order {order_id}: {exc}") from exc
        except ValueError as exc:
            raise OrderPlacementError(
                f"Invalid cancel response for {order_id}: {exc}"
            ) from exc

    def _submit_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Submit an order or simulate it when DRY_RUN is enabled."""
        if self.config.dry_run:
            self.logger.info("DRY_RUN enabled; simulated order payload: %s", payload)
            return {
                "order_id": f"SIM-{int(time.time())}",
                "status": "SIMULATED",
                "filled_price": None,
                "timestamp": now_iso(),
            }

        path = f"/accounts/{self.config.tastytrade_account_number}/orders"
        try:
            response = self.auth.request("POST", path, json=payload)
            body = extract_data(response.json())
            order_payload = body.get("order", body)
            return self._normalize_order_response(order_payload)
        except requests.HTTPError as exc:
            detail = self._extract_error_detail(exc.response)
            raise OrderPlacementError(
                f"Order submission failed with status "
                f"{exc.response.status_code}: {detail}"
            ) from exc
        except requests.RequestException as exc:
            raise OrderPlacementError(f"Order submission failed: {exc}") from exc
        except ValueError as exc:
            raise OrderPlacementError(f"Invalid order response payload: {exc}") from exc

    def _build_payload(
        self,
        *,
        symbol: str,
        quantity: int,
        side: str,
        order_type: str,
        instrument_type: str,
    ) -> dict[str, Any]:
        """Validate inputs and construct a TastyTrade order payload."""
        normalized_symbol = str(symbol).strip().upper()
        if not normalized_symbol:
            raise OrderPlacementError("symbol must be a non-empty string")
        if not isinstance(quantity, int) or quantity <= 0:
            raise OrderPlacementError("quantity must be a positive integer")

        normalized_side = side.strip().upper()
        if normalized_side not in self.VALID_SIDES:
            raise OrderPlacementError("side must be BUY or SELL")

        normalized_order_type = order_type.strip().upper()
        if normalized_order_type not in self.VALID_ORDER_TYPES:
            raise OrderPlacementError("order_type must be Market or Limit")

        action = "Buy to Open" if normalized_side == "BUY" else "Sell to Open"
        return {
            "time-in-force": "Day",
            "order-type": normalized_order_type.title(),
            "legs": [
                {
                    "instrument-type": instrument_type,
                    "symbol": normalized_symbol,
                    "quantity": quantity,
                    "action": action,
                }
            ],
        }

    @staticmethod
    def _normalize_order_response(
        order_payload: dict[str, Any],
        default_status: str | None = None,
    ) -> dict[str, Any]:
        """Return a compact, consistent order response."""
        if not isinstance(order_payload, dict):
            raise ValueError("Expected order payload dictionary")

        fills = order_payload.get("fills") or []
        filled_price = None
        if fills and isinstance(fills, list) and isinstance(fills[0], dict):
            filled_price = to_float(fills[0].get("price"))

        return {
            "order_id": str(
                order_payload.get("id")
                or order_payload.get("order-id")
                or order_payload.get("external-order-id")
                or ""
            ),
            "status": str(
                order_payload.get("status")
                or order_payload.get("confirmation-status")
                or default_status
                or "UNKNOWN"
            ),
            "filled_price": round(filled_price, 2) if filled_price is not None else None,
            "timestamp": str(
                order_payload.get("updated-at")
                or order_payload.get("received-at")
                or now_iso()
            ),
        }

    @staticmethod
    def _extract_error_detail(response: requests.Response | None) -> str:
        """Extract a readable error message from an HTTP response."""
        if response is None:
            return "No response body"
        try:
            payload = response.json()
        except ValueError:
            return response.text.strip() or "Unknown error"
        data = extract_data(payload)
        if isinstance(data, dict):
            for key in ("error", "message"):
                if data.get(key):
                    return str(data[key])
        if isinstance(payload, dict):
            for key in ("error", "message"):
                if payload.get(key):
                    return str(payload[key])
        return str(payload)
