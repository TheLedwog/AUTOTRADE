"""Unit tests for order submission helpers."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from tastytrade_autotrader.api.orders import OrdersAPI
from tastytrade_autotrader.utils.exceptions import OrderPlacementError


def test_dry_run_mode_returns_simulated_response_without_calling_api():
    """Dry-run mode should never submit HTTP requests."""
    auth = Mock()
    config = SimpleNamespace(
        dry_run=True,
        tastytrade_account_number="TEST123",
        request_timeout=5,
    )
    api = OrdersAPI(auth, config)

    result = api.place_stock_order("AAPL", 2, "BUY")

    assert result["status"] == "SIMULATED"
    assert result["order_id"].startswith("SIM-")
    auth.request.assert_not_called()


def test_valid_order_returns_structured_response_dict():
    """A successful order response should be normalized consistently."""
    auth = Mock()
    config = SimpleNamespace(
        dry_run=False,
        tastytrade_account_number="TEST123",
        request_timeout=5,
    )
    api = OrdersAPI(auth, config)
    response = Mock()
    response.json.return_value = {
        "data": {
            "order": {
                "id": 1234,
                "status": "Received",
                "updated-at": "2026-04-21T10:00:00Z",
                "fills": [],
            }
        }
    }
    auth.request.return_value = response

    result = api.place_stock_order("AAPL", 1, "BUY")

    assert result == {
        "order_id": "1234",
        "status": "Received",
        "filled_price": None,
        "timestamp": "2026-04-21T10:00:00Z",
    }


def test_api_4xx_error_raises_order_placement_error():
    """HTTP 4xx responses should surface as OrderPlacementError."""
    auth = Mock()
    config = SimpleNamespace(
        dry_run=False,
        tastytrade_account_number="TEST123",
        request_timeout=5,
    )
    api = OrdersAPI(auth, config)

    response = Mock()
    response.status_code = 422
    response.json.return_value = {"error": "bad order"}
    http_error = requests.HTTPError(response=response)
    auth.request.side_effect = http_error

    with pytest.raises(OrderPlacementError, match="422"):
        api.place_stock_order("AAPL", 1, "BUY")
