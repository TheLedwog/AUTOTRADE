"""Unit tests for the trade decision engine."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tastytrade_autotrader.logic.trade_decision import TradeDecisionEngine
from tastytrade_autotrader.signal.signal_parser import TradeSignal
from tastytrade_autotrader.utils.exceptions import (
    InsufficientFundsError,
    TradeConfirmationError,
)


def build_engine(
    threshold: float = 210.0,
    trade_confirmer: Mock | None = None,
    allocation_base: str = "net_liquidation_value",
) -> tuple[TradeDecisionEngine, Mock, Mock, Mock, Mock | None]:
    """Create a decision engine with mocked collaborators."""
    account_api = Mock()
    market_data_api = Mock()
    orders_api = Mock()
    config = SimpleNamespace(
        options_cost_threshold=threshold,
        allocation_base=allocation_base,
    )
    return (
        TradeDecisionEngine(
            account_api,
            market_data_api,
            orders_api,
            config,
            trade_confirmer=trade_confirmer,
        ),
        account_api,
        market_data_api,
        orders_api,
        trade_confirmer,
    )


def make_signal(signal_type: str, direction: str = "BUY") -> TradeSignal:
    """Create a test TradeSignal instance."""
    return TradeSignal(
        symbol="AAPL",
        direction=direction,
        signal_type=signal_type,
        allocation=0.10,
        confidence=None,
        expiration_hint=None,
        strike_hint=None,
        raw={},
    )


def test_option_cost_below_threshold_places_option_order():
    """Affordable options should execute as option orders."""
    confirmer = Mock()
    confirmer.confirm_trade.return_value = {"message_id": 1}
    engine, account_api, market_data_api, orders_api, _ = build_engine(
        trade_confirmer=confirmer
    )
    signal = make_signal("OPTION", "BUY")

    account_api.get_balances.return_value = {"net_liquidation_value": 10000.0}
    market_data_api.find_best_option_contract.return_value = {
        "symbol": "AAPL260619C00200000"
    }
    market_data_api.get_option_quote.return_value = {"bid": 1.9, "ask": 2.0, "last": 2.0}
    orders_api.place_option_order.return_value = {
        "order_id": "1",
        "status": "RECEIVED",
        "filled_price": None,
        "timestamp": "2026-04-21T10:00:00Z",
    }

    result = engine.decide_and_execute(signal)

    assert result["trade_type"] == "OPTION"
    assert result["quantity"] == 5
    assert result["estimated_cost"] == 1000.0
    confirmer.confirm_trade.assert_called_once()
    confirmer.notify_order_result.assert_called_once()
    orders_api.place_option_order.assert_called_once_with(
        option_symbol="AAPL260619C00200000",
        quantity=5,
        side="BUY",
    )
    orders_api.place_stock_order.assert_not_called()


def test_option_cost_above_threshold_falls_back_to_stock():
    """Expensive option contracts should fall back to stock."""
    confirmer = Mock()
    confirmer.confirm_trade.return_value = {"message_id": 1}
    engine, account_api, market_data_api, orders_api, _ = build_engine(
        trade_confirmer=confirmer
    )
    signal = make_signal("OPTION", "BUY")

    account_api.get_balances.return_value = {"net_liquidation_value": 10000.0}
    market_data_api.find_best_option_contract.return_value = {
        "symbol": "AAPL260619C00200000"
    }
    market_data_api.get_option_quote.return_value = {"bid": 2.9, "ask": 3.0, "last": 3.0}
    market_data_api.get_stock_quote.return_value = {"bid": 99.5, "ask": 100.0, "last": 100.0}
    orders_api.place_stock_order.return_value = {
        "order_id": "2",
        "status": "RECEIVED",
        "filled_price": None,
        "timestamp": "2026-04-21T10:00:00Z",
    }

    result = engine.decide_and_execute(signal)

    assert result["trade_type"] == "STOCK"
    assert result["quantity"] == 10
    confirmer.confirm_trade.assert_called_once()
    confirmer.notify_order_result.assert_called_once()
    orders_api.place_stock_order.assert_called_once_with(
        symbol="AAPL",
        quantity=10,
        side="BUY",
    )
    orders_api.place_option_order.assert_not_called()


def test_stock_signal_places_stock_order_directly():
    """Stock signals should skip option selection."""
    confirmer = Mock()
    confirmer.confirm_trade.return_value = {"message_id": 1}
    engine, account_api, market_data_api, orders_api, _ = build_engine(
        trade_confirmer=confirmer
    )
    signal = make_signal("STOCK", "BUY")

    account_api.get_balances.return_value = {"net_liquidation_value": 5000.0}
    market_data_api.get_stock_quote.return_value = {"bid": 49.5, "ask": 50.0, "last": 50.0}
    orders_api.place_stock_order.return_value = {
        "order_id": "3",
        "status": "RECEIVED",
        "filled_price": None,
        "timestamp": "2026-04-21T10:00:00Z",
    }

    result = engine.decide_and_execute(signal)

    assert result["trade_type"] == "STOCK"
    assert result["quantity"] == 10
    market_data_api.find_best_option_contract.assert_not_called()
    confirmer.confirm_trade.assert_called_once()
    confirmer.notify_order_result.assert_called_once()
    orders_api.place_stock_order.assert_called_once()


def test_insufficient_funds_raises_insufficient_funds_error():
    """Too little capital should stop execution before ordering."""
    engine, account_api, market_data_api, orders_api, _ = build_engine()
    signal = make_signal("STOCK", "BUY")

    account_api.get_balances.return_value = {"net_liquidation_value": 10.0}
    market_data_api.get_stock_quote.return_value = {"bid": 99.5, "ask": 100.0, "last": 100.0}

    with pytest.raises(InsufficientFundsError):
        engine.decide_and_execute(signal)

    orders_api.place_stock_order.assert_not_called()


def test_trade_confirmation_rejection_blocks_order_submission():
    """A rejected Telegram confirmation should stop the order."""
    confirmer = Mock()
    confirmer.confirm_trade.side_effect = TradeConfirmationError("rejected")
    engine, account_api, market_data_api, orders_api, _ = build_engine(
        trade_confirmer=confirmer
    )
    signal = make_signal("STOCK", "BUY")

    account_api.get_balances.return_value = {"net_liquidation_value": 5000.0}
    market_data_api.get_stock_quote.return_value = {
        "bid": 49.5,
        "ask": 50.0,
        "last": 50.0,
    }

    with pytest.raises(TradeConfirmationError):
        engine.decide_and_execute(signal)

    orders_api.place_stock_order.assert_not_called()
    confirmer.notify_order_result.assert_not_called()


def test_order_failure_notifies_telegram_with_failure_reason():
    """A broker order failure should be reported back through Telegram."""
    confirmer = Mock()
    confirmer.confirm_trade.return_value = {"message_id": 1}
    engine, account_api, market_data_api, orders_api, _ = build_engine(
        trade_confirmer=confirmer
    )
    signal = make_signal("STOCK", "BUY")

    account_api.get_balances.return_value = {"net_liquidation_value": 5000.0}
    market_data_api.get_stock_quote.return_value = {
        "bid": 49.5,
        "ask": 50.0,
        "last": 50.0,
    }
    orders_api.place_stock_order.side_effect = RuntimeError("not enough funds")

    with pytest.raises(RuntimeError, match="not enough funds"):
        engine.decide_and_execute(signal)

    confirmer.notify_order_result.assert_called_once()


def test_buying_power_allocation_base_uses_buying_power_when_configured():
    """Allocation sizing should follow the configured capital base explicitly."""
    engine, account_api, market_data_api, orders_api, _ = build_engine(
        allocation_base="buying_power"
    )
    signal = make_signal("STOCK", "BUY")

    account_api.get_balances.return_value = {
        "net_liquidation_value": 1000000.0,
        "buying_power": 2000000.0,
    }
    market_data_api.get_stock_quote.return_value = {
        "bid": 99.5,
        "ask": 100.0,
        "last": 100.0,
    }
    orders_api.place_stock_order.return_value = {
        "order_id": "7",
        "status": "RECEIVED",
        "filled_price": None,
        "timestamp": "2026-04-22T15:00:00Z",
    }

    result = engine.decide_and_execute(signal)

    assert result["estimated_cost"] == 200000.0
    assert result["quantity"] == 2000
