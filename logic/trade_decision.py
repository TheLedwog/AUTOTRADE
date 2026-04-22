"""Core trade decision engine."""

from __future__ import annotations

from typing import Any

from tastytrade_autotrader.api.account import AccountAPI
from tastytrade_autotrader.api.market_data import MarketDataAPI
from tastytrade_autotrader.api.orders import OrdersAPI
from tastytrade_autotrader.config import AppConfig
from tastytrade_autotrader.notifications.telegram_confirmation import (
    TelegramTradeConfirmer,
)
from tastytrade_autotrader.signal.signal_parser import TradeSignal
from tastytrade_autotrader.utils.exceptions import InsufficientFundsError
from tastytrade_autotrader.utils.helpers import round_to_lot_size


class TradeDecisionEngine:
    """Evaluate a signal and execute the appropriate trade."""

    def __init__(
        self,
        account_api: AccountAPI,
        market_data_api: MarketDataAPI,
        orders_api: OrdersAPI,
        config: AppConfig,
        trade_confirmer: TelegramTradeConfirmer | None = None,
    ) -> None:
        """Store collaborators required for decisions and execution."""
        self.account_api = account_api
        self.market_data_api = market_data_api
        self.orders_api = orders_api
        self.config = config
        self.trade_confirmer = trade_confirmer
        self._confirmation_context: Any = None

    def decide_and_execute(self, signal: TradeSignal) -> dict[str, Any]:
        """Decide between option and stock execution, then place the order."""
        balances = self.account_api.get_balances()
        available_capital = self._determine_available_capital(balances)
        if available_capital <= 0:
            raise InsufficientFundsError(
                f"No available capital was found for allocation base "
                f"{self.config.allocation_base!r}"
            )

        dollar_allocation = round(available_capital * signal.allocation, 2)
        if dollar_allocation <= 0:
            raise InsufficientFundsError("Signal allocation results in zero tradable dollars")

        if signal.signal_type == "OPTION":
            return self._handle_option_signal(signal, dollar_allocation)

        return self._execute_stock_trade(
            signal=signal,
            dollar_allocation=dollar_allocation,
            decision_reasoning=(
                "Signal type was STOCK, so the trade was executed directly as an equity order."
            ),
        )

    def _handle_option_signal(
        self,
        signal: TradeSignal,
        dollar_allocation: float,
    ) -> dict[str, Any]:
        """Evaluate an option trade and fall back to stock when needed."""
        option_direction = "BUY_CALL" if signal.direction == "BUY" else "BUY_PUT"
        option_contract = self.market_data_api.find_best_option_contract(
            symbol=signal.symbol,
            direction=option_direction,
            expiration_hint=signal.expiration_hint,
            strike_hint=signal.strike_hint,
        )
        option_quote = self.market_data_api.get_option_quote(option_contract["symbol"])
        ask_price = round(option_quote["ask"], 2)
        total_contract_cost = round(ask_price * 100, 2)

        if total_contract_cost <= self.config.options_cost_threshold:
            quantity = round_to_lot_size(dollar_allocation / total_contract_cost)
            if quantity >= 1:
                estimated_cost = round(quantity * total_contract_cost, 2)
                decision_reasoning = (
                    f"Selected an option contract because the total contract cost "
                    f"(${total_contract_cost:.2f}) was within the configured threshold "
                    f"(${self.config.options_cost_threshold:.2f})."
                )
                self._confirm_trade(
                    trade_type="OPTION",
                    underlying_symbol=signal.symbol,
                    order_symbol=option_contract["symbol"],
                    side="BUY",
                    quantity=quantity,
                    unit_price=total_contract_cost,
                    estimated_cost=estimated_cost,
                    decision_reasoning=decision_reasoning,
                )
                try:
                    order_result = self.orders_api.place_option_order(
                        option_symbol=option_contract["symbol"],
                        quantity=quantity,
                        side="BUY",
                    )
                except Exception as exc:
                    self._notify_trade_result(
                        success=False,
                        order_result=None,
                        failure_reason=str(exc),
                    )
                    raise

                self._notify_trade_result(
                    success=True,
                    order_result=order_result,
                    failure_reason=None,
                )
                return {
                    "trade_type": "OPTION",
                    "symbol": option_contract["symbol"],
                    "quantity": quantity,
                    "estimated_cost": estimated_cost,
                    "order_result": order_result,
                    "decision_reasoning": decision_reasoning,
                }

        fallback_reason = (
            f"Option contract cost (${total_contract_cost:.2f}) exceeded the configured "
            f"threshold (${self.config.options_cost_threshold:.2f}), so the engine "
            "fell back to stock."
            if total_contract_cost > self.config.options_cost_threshold
            else (
                "The option contract was within threshold, but the allocated capital "
                "was not enough to buy a full contract, so the engine fell back to stock."
            )
        )
        return self._execute_stock_trade(
            signal=signal,
            dollar_allocation=dollar_allocation,
            decision_reasoning=fallback_reason,
        )

    def _execute_stock_trade(
        self,
        *,
        signal: TradeSignal,
        dollar_allocation: float,
        decision_reasoning: str,
    ) -> dict[str, Any]:
        """Place a stock order for the signal."""
        stock_quote = self.market_data_api.get_stock_quote(signal.symbol)
        ask_price = round(stock_quote["ask"], 2)
        quantity = round_to_lot_size(dollar_allocation / ask_price)
        if quantity < 1:
            raise InsufficientFundsError(
                f"Allocation of ${dollar_allocation:.2f} is insufficient to purchase "
                f"one share of {signal.symbol} at ${ask_price:.2f}"
            )

        estimated_cost = round(quantity * ask_price, 2)
        self._confirm_trade(
            trade_type="STOCK",
            underlying_symbol=signal.symbol,
            order_symbol=signal.symbol,
            side=signal.direction,
            quantity=quantity,
            unit_price=ask_price,
            estimated_cost=estimated_cost,
            decision_reasoning=decision_reasoning,
        )
        try:
            order_result = self.orders_api.place_stock_order(
                symbol=signal.symbol,
                quantity=quantity,
                side=signal.direction,
            )
        except Exception as exc:
            self._notify_trade_result(
                success=False,
                order_result=None,
                failure_reason=str(exc),
            )
            raise

        self._notify_trade_result(
            success=True,
            order_result=order_result,
            failure_reason=None,
        )
        return {
            "trade_type": "STOCK",
            "symbol": signal.symbol,
            "quantity": quantity,
            "estimated_cost": estimated_cost,
            "order_result": order_result,
            "decision_reasoning": decision_reasoning,
        }

    def _confirm_trade(
        self,
        *,
        trade_type: str,
        underlying_symbol: str,
        order_symbol: str,
        side: str,
        quantity: int,
        unit_price: float,
        estimated_cost: float,
        decision_reasoning: str,
    ) -> None:
        """Require Telegram approval before submitting an order when enabled."""
        self._confirmation_context = None
        if self.trade_confirmer is None:
            return

        self._confirmation_context = self.trade_confirmer.confirm_trade(
            {
                "trade_type": trade_type,
                "underlying_symbol": underlying_symbol,
                "order_symbol": order_symbol,
                "side": side,
                "quantity": quantity,
                "unit_price": round(unit_price, 2),
                "estimated_cost": round(estimated_cost, 2),
                "decision_reasoning": decision_reasoning,
            }
        )

    def _notify_trade_result(
        self,
        *,
        success: bool,
        order_result: dict[str, Any] | None,
        failure_reason: str | None,
    ) -> None:
        """Update Telegram with the final order outcome when available."""
        if self.trade_confirmer is None:
            return

        self.trade_confirmer.notify_order_result(
            self._confirmation_context,
            success=success,
            order_result=order_result,
            failure_reason=failure_reason,
        )

    def _determine_available_capital(self, balances: dict[str, Any]) -> float:
        """Return the configured capital base used for signal allocation sizing."""
        allocation_base = str(self.config.allocation_base).strip().lower()
        if allocation_base == "net_liquidation_value":
            value = balances.get("net_liquidation_value")
        elif allocation_base == "buying_power":
            value = balances.get("buying_power")
        else:
            value = balances.get("cash_balance")
        return round(float(value or 0.0), 2)
