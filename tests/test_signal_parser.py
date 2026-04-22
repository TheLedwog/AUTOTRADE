"""Unit tests for signal parsing."""

import pytest

from tastytrade_autotrader.signal.signal_parser import SignalParser
from tastytrade_autotrader.utils.exceptions import SignalParseError


def test_valid_stock_signal_parses_correctly():
    """A valid stock signal should be normalized without modification."""
    parser = SignalParser()

    signal = parser.parse(
        {
            "symbol": "aapl",
            "direction": "buy",
            "signal_type": "stock",
            "allocation": 0.10,
            "confidence": 0.75,
        }
    )

    assert signal.symbol == "AAPL"
    assert signal.direction == "BUY"
    assert signal.signal_type == "STOCK"
    assert signal.allocation == 0.10
    assert signal.confidence == 0.75
    assert signal.expiration_hint is None
    assert signal.strike_hint is None


def test_valid_option_signal_parses_correctly():
    """A valid option signal should preserve optional option fields."""
    parser = SignalParser()

    signal = parser.parse(
        {
            "symbol": "tsla",
            "direction": "sell",
            "signal_type": "option",
            "allocation": 0.25,
            "confidence": 0.9,
            "expiration_hint": "2026-06-19",
            "strike_hint": 250,
        }
    )

    assert signal.symbol == "TSLA"
    assert signal.direction == "SELL"
    assert signal.signal_type == "OPTION"
    assert signal.allocation == 0.25
    assert signal.expiration_hint == "2026-06-19"
    assert signal.strike_hint == 250.0


def test_missing_required_field_raises_signal_parse_error():
    """A missing required field should raise a descriptive parser error."""
    parser = SignalParser()

    with pytest.raises(SignalParseError, match="symbol"):
        parser.parse(
            {
                "direction": "BUY",
                "signal_type": "STOCK",
                "allocation": 0.10,
            }
        )


def test_malformed_json_string_raises_signal_parse_error():
    """Malformed JSON should be rejected cleanly."""
    parser = SignalParser()

    with pytest.raises(SignalParseError, match="Malformed JSON"):
        parser.parse('{"symbol": "AAPL",')
