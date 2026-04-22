"""Custom exceptions for the autotrader application."""


class TastytradeAutotraderError(Exception):
    """Base exception for the application."""


class AuthenticationError(TastytradeAutotraderError):
    """Raised when TastyTrade authentication fails."""


class SignalParseError(TastytradeAutotraderError):
    """Raised when an incoming signal payload is invalid."""


class OrderPlacementError(TastytradeAutotraderError):
    """Raised when an order cannot be submitted or managed."""


class InsufficientFundsError(TastytradeAutotraderError):
    """Raised when a trade cannot be funded."""


class MarketDataError(TastytradeAutotraderError):
    """Raised when market data cannot be fetched or interpreted."""


class TradeConfirmationError(TastytradeAutotraderError):
    """Raised when a Telegram trade confirmation is rejected or times out."""
