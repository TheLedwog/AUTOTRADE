"""Unit tests for market data fallbacks."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from tastytrade_autotrader.api.market_data import MarketDataAPI
from tastytrade_autotrader.utils.exceptions import MarketDataError


def test_dry_run_stock_quote_falls_back_when_quote_endpoint_fails():
    """Dry-run mode should use a fallback stock quote when sandbox quotes fail."""
    auth = Mock()
    auth.request.side_effect = requests.HTTPError("bad gateway")
    config = SimpleNamespace(
        dry_run=True,
        allow_sandbox_quote_fallback=False,
        tastytrade_base_url="https://api.cert.tastyworks.com",
        dry_run_fallback_stock_price=123.45,
        dry_run_fallback_option_price=2.0,
    )
    api = MarketDataAPI(auth, config)

    quote = api.get_stock_quote("AAPL")

    assert quote == {"bid": 123.45, "ask": 123.45, "last": 123.45}


def test_non_dry_run_stock_quote_still_raises_on_quote_failure():
    """Live mode should not hide quote errors behind fallback prices."""
    auth = Mock()
    auth.request.side_effect = requests.HTTPError("bad gateway")
    config = SimpleNamespace(
        dry_run=False,
        allow_sandbox_quote_fallback=False,
        tastytrade_base_url="https://api.cert.tastyworks.com",
        dry_run_fallback_stock_price=123.45,
        dry_run_fallback_option_price=2.0,
    )
    api = MarketDataAPI(auth, config)

    with pytest.raises(MarketDataError):
        api.get_stock_quote("AAPL")


def test_sandbox_quote_fallback_can_be_enabled_without_dry_run():
    """Sandbox quote fallback should work for real cert-order testing when enabled."""
    auth = Mock()
    auth.request.side_effect = requests.HTTPError("bad gateway")
    config = SimpleNamespace(
        dry_run=False,
        allow_sandbox_quote_fallback=True,
        tastytrade_base_url="https://api.cert.tastyworks.com",
        dry_run_fallback_stock_price=123.45,
        dry_run_fallback_option_price=2.0,
    )
    api = MarketDataAPI(auth, config)

    quote = api.get_stock_quote("AAPL")

    assert quote == {"bid": 123.45, "ask": 123.45, "last": 123.45}


def test_live_base_url_does_not_allow_sandbox_quote_fallback():
    """Fallback pricing must not activate for live trading."""
    auth = Mock()
    auth.request.side_effect = requests.HTTPError("bad gateway")
    config = SimpleNamespace(
        dry_run=False,
        allow_sandbox_quote_fallback=True,
        tastytrade_base_url="https://api.tastyworks.com",
        dry_run_fallback_stock_price=123.45,
        dry_run_fallback_option_price=2.0,
    )
    api = MarketDataAPI(auth, config)

    with pytest.raises(MarketDataError):
        api.get_stock_quote("AAPL")
