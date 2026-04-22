"""Market data and option-selection helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import requests

from tastytrade_autotrader.auth.tastytrade_auth import TastyTradeAuth
from tastytrade_autotrader.config import AppConfig
from tastytrade_autotrader.utils.exceptions import MarketDataError
from tastytrade_autotrader.utils.helpers import extract_data, format_occ_symbol, to_float
from tastytrade_autotrader.utils.logger import get_logger


@dataclass
class OptionCandidate:
    """Internal representation of an option candidate."""

    symbol: str
    expiration_date: str
    strike_price: float
    option_type: str
    delta: float | None
    bid: float
    ask: float
    last: float
    open_interest: int
    volume: int
    days_to_expiration: int


class MarketDataAPI:
    """Fetch equity quotes, option chains, and option quotes."""

    def __init__(self, auth: TastyTradeAuth, config: AppConfig) -> None:
        """Store shared dependencies."""
        self.auth = auth
        self.config = config
        self.logger = get_logger(self.__class__.__name__)

    def get_stock_quote(self, symbol: str) -> dict[str, float]:
        """Return bid, ask, and last prices for a stock."""
        try:
            payload = self._get_quote_payload(symbol)
            return self._normalize_quote(payload, symbol)
        except MarketDataError as exc:
            if self._should_use_quote_fallback():
                fallback = self._build_fallback_quote(
                    self.config.dry_run_fallback_stock_price
                )
                self.logger.warning(
                    "Using fallback stock quote for %s due to quote error: %s",
                    symbol,
                    exc,
                )
                return fallback
            raise

    def get_option_chain(
        self,
        symbol: str,
        expiration_date: str,
    ) -> list[dict[str, Any]]:
        """Return option contracts filtered by expiration date when supplied."""
        chain = self._fetch_chain(symbol)
        if expiration_date:
            return [
                contract
                for contract in chain
                if str(contract.get("expiration-date") or contract.get("expiration_date"))
                == expiration_date
            ]
        return chain

    def get_option_quote(self, option_symbol: str) -> dict[str, float]:
        """Return bid, ask, and last prices for an option contract."""
        try:
            payload = self._get_quote_payload(option_symbol)
            return self._normalize_quote(payload, option_symbol)
        except MarketDataError as exc:
            if self._should_use_quote_fallback():
                fallback = self._build_fallback_quote(
                    self.config.dry_run_fallback_option_price
                )
                self.logger.warning(
                    "Using fallback option quote for %s due to quote error: %s",
                    option_symbol,
                    exc,
                )
                return fallback
            raise

    def find_best_option_contract(
        self,
        symbol: str,
        direction: str,
        target_delta: float = 0.40,
        expiration_hint: str | None = None,
        strike_hint: float | None = None,
    ) -> dict[str, Any]:
        """Choose a liquid contract nearest to target delta on the nearest expiry."""
        normalized_direction = direction.strip().upper()
        option_type = "C" if normalized_direction.endswith("CALL") else "P"
        stock_quote = self.get_stock_quote(symbol)
        underlying_last = stock_quote["last"]

        chain = self.get_option_chain(symbol, expiration_hint or "")
        if not chain:
            raise MarketDataError(f"No option contracts found for {symbol}")

        candidates: list[OptionCandidate] = []
        for contract in chain:
            contract_type = str(
                contract.get("option-type") or contract.get("option_type") or ""
            ).strip().upper()
            if contract_type not in {"C", "P", "CALL", "PUT"}:
                continue
            compact_type = "C" if contract_type.startswith("C") else "P"
            if compact_type != option_type:
                continue
            if not contract.get("active", True):
                continue

            expiration = str(
                contract.get("expiration-date") or contract.get("expiration_date") or ""
            )
            if not expiration:
                continue

            strike_price = to_float(
                contract.get("strike-price") or contract.get("strike_price")
            )
            if strike_price is None:
                continue

            if strike_hint is not None and abs(strike_price - strike_hint) > 20:
                continue

            occ_symbol = (
                contract.get("symbol")
                or contract.get("occ-symbol")
                or contract.get("occ_symbol")
                or format_occ_symbol(symbol, expiration, strike_price, compact_type)
            )
            quote = self.get_option_quote(str(occ_symbol).strip())
            delta = to_float(contract.get("delta"))
            if delta is None:
                delta = to_float(contract.get("option-delta"))

            expiration_date = datetime.strptime(expiration, "%Y-%m-%d").date()
            days_to_expiration = max((expiration_date - datetime.utcnow().date()).days, 0)
            open_interest = int(contract.get("open-interest") or contract.get("open_interest") or 0)
            volume = int(contract.get("volume") or 0)

            candidates.append(
                OptionCandidate(
                    symbol=str(occ_symbol).strip(),
                    expiration_date=expiration,
                    strike_price=round(float(strike_price), 2),
                    option_type="CALL" if compact_type == "C" else "PUT",
                    delta=abs(delta) if delta is not None else None,
                    bid=quote["bid"],
                    ask=quote["ask"],
                    last=quote["last"],
                    open_interest=open_interest,
                    volume=volume,
                    days_to_expiration=days_to_expiration,
                )
            )

        if not candidates:
            raise MarketDataError(f"No matching option contracts found for {symbol}")

        liquid_candidates = [
            candidate
            for candidate in candidates
            if candidate.ask > 0
            and candidate.bid >= 0
            and (candidate.open_interest > 0 or candidate.volume > 0 or expiration_hint)
        ]
        candidates = liquid_candidates or [candidate for candidate in candidates if candidate.ask > 0]
        if not candidates:
            raise MarketDataError(f"No sufficiently liquid option contracts found for {symbol}")

        nearest_dte = min(candidate.days_to_expiration for candidate in candidates)
        nearest_expiry_candidates = [
            candidate
            for candidate in candidates
            if candidate.days_to_expiration == nearest_dte
        ]

        def rank(candidate: OptionCandidate) -> tuple[float, float, float]:
            delta_score = (
                abs((candidate.delta or target_delta) - target_delta)
                if candidate.delta is not None
                else 10.0
            )
            strike_score = abs(candidate.strike_price - underlying_last)
            return (delta_score, strike_score, candidate.ask)

        best = min(nearest_expiry_candidates, key=rank)
        return {
            "symbol": best.symbol,
            "expiration_date": best.expiration_date,
            "strike_price": round(best.strike_price, 2),
            "option_type": best.option_type,
            "delta": round(best.delta, 4) if best.delta is not None else None,
            "bid": round(best.bid, 2),
            "ask": round(best.ask, 2),
            "last": round(best.last, 2),
            "days_to_expiration": best.days_to_expiration,
        }

    def _fetch_chain(self, symbol: str) -> list[dict[str, Any]]:
        """Fetch raw option chain data for an underlying symbol."""
        try:
            response = self.auth.request("GET", f"/option-chains/{symbol.strip().upper()}")
            payload = extract_data(response.json())
            if isinstance(payload, dict) and isinstance(payload.get("items"), list):
                return payload["items"]
            if isinstance(payload, list):
                return payload
            raise MarketDataError("Option chain response did not include a contract list")
        except requests.RequestException as exc:
            raise MarketDataError(f"Failed to retrieve option chain for {symbol}: {exc}") from exc
        except ValueError as exc:
            raise MarketDataError(f"Invalid option chain response for {symbol}: {exc}") from exc

    def _get_quote_payload(self, symbol: str) -> dict[str, Any]:
        """Fetch a quote payload using a couple of likely TastyTrade shapes."""
        normalized = symbol.strip().upper()
        paths = (
            (f"/market-data/quotes/{normalized}", None),
            ("/market-data/quotes", {"symbol": normalized}),
            ("/market-data/quotes", {"symbols[]": normalized}),
        )
        last_error: Exception | None = None

        for path, params in paths:
            try:
                response = self.auth.request("GET", path, params=params)
                payload = extract_data(response.json())
                quote_payload = self._extract_quote_from_payload(payload, normalized)
                if quote_payload:
                    return quote_payload
            except (requests.RequestException, ValueError, MarketDataError) as exc:
                last_error = exc

        if last_error is not None:
            raise MarketDataError(f"Unable to retrieve quote for {normalized}: {last_error}") from last_error
        raise MarketDataError(f"Unable to retrieve quote for {normalized}")

    @staticmethod
    def _extract_quote_from_payload(payload: Any, symbol: str) -> dict[str, Any] | None:
        """Extract a single quote object from a variety of response shapes."""
        if isinstance(payload, dict):
            if {"bid", "ask", "last"} & set(payload.keys()):
                return payload
            if {"bid-price", "ask-price", "last-price"} & set(payload.keys()):
                return payload
            if isinstance(payload.get("quote"), dict):
                return payload["quote"]
            if isinstance(payload.get("items"), list):
                for item in payload["items"]:
                    item_symbol = str(
                        item.get("symbol") or item.get("eventSymbol") or ""
                    ).replace(" ", "").upper()
                    if item_symbol == symbol.replace(" ", "").upper():
                        return item
                if len(payload["items"]) == 1 and isinstance(payload["items"][0], dict):
                    return payload["items"][0]
        return None

    @staticmethod
    def _normalize_quote(payload: dict[str, Any], symbol: str) -> dict[str, float]:
        """Normalize mixed quote field names to bid, ask, and last."""
        bid = to_float(payload.get("bid"))
        if bid is None:
            bid = to_float(payload.get("bid-price"))
        ask = to_float(payload.get("ask"))
        if ask is None:
            ask = to_float(payload.get("ask-price"))
        last = to_float(payload.get("last"))
        if last is None:
            last = to_float(payload.get("last-price"))
        if last is None:
            last = to_float(payload.get("price"))

        if bid is None or ask is None or last is None:
            raise MarketDataError(
                f"Quote payload for {symbol} is missing bid, ask, or last price"
            )

        return {
            "bid": round(bid, 2),
            "ask": round(ask, 2),
            "last": round(last, 2),
        }

    @staticmethod
    def _build_fallback_quote(price: float) -> dict[str, float]:
        """Build a predictable quote for DRY_RUN testing when sandbox quotes fail."""
        return {
            "bid": round(price, 2),
            "ask": round(price, 2),
            "last": round(price, 2),
        }

    def _should_use_quote_fallback(self) -> bool:
        """Return whether quote fallback is allowed for the current environment."""
        return bool(
            self.config.dry_run
            or (
                self.config.allow_sandbox_quote_fallback
                and self.config.tastytrade_base_url == "https://api.cert.tastyworks.com"
            )
        )
