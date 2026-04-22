"""Session-based authentication for the TastyTrade API."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import requests

from tastytrade_autotrader.config import AppConfig
from tastytrade_autotrader.utils.exceptions import AuthenticationError
from tastytrade_autotrader.utils.helpers import extract_data
from tastytrade_autotrader.utils.logger import get_logger


class TastyTradeAuth:
    """Manage TastyTrade API authentication and authenticated requests."""

    def __init__(
        self,
        config: AppConfig,
        session: requests.Session | None = None,
    ) -> None:
        """Initialize the auth helper with a reusable HTTP session."""
        self.config = config
        self.session = session or requests.Session()
        self.logger = get_logger(self.__class__.__name__)
        self.session_token: str | None = None
        self.last_authenticated_at: str | None = None
        self.last_auth_error: str | None = None
        self.session.headers.update(
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": self.config.user_agent,
            }
        )

    @property
    def is_authenticated(self) -> bool:
        """Return whether a session token is currently cached."""
        return bool(self.session_token)

    def authenticate(self, force: bool = False) -> str:
        """Authenticate against POST /sessions and cache the session token."""
        if self.session_token and not force:
            return self.session_token

        payload = {
            "login": self.config.tastytrade_username,
            "password": self.config.tastytrade_password,
            "remember-me": True,
        }
        url = f"{self.config.tastytrade_base_url}/sessions"

        try:
            response = self.session.post(
                url,
                json=payload,
                timeout=self.config.request_timeout,
            )
            response.raise_for_status()
            body = response.json()
            data = extract_data(body)
            token = data.get("session-token")
            if not token:
                raise AuthenticationError(
                    "Authentication response did not include a session token"
                )

            self.session_token = str(token)
            self.last_authenticated_at = datetime.now(timezone.utc).isoformat()
            self.last_auth_error = None
            self._warn_on_unexpected_timestamp(data)
            self.logger.info("Authenticated with TastyTrade API")
            return self.session_token
        except requests.HTTPError as exc:
            detail = self._extract_error_detail(exc.response)
            self.session_token = None
            self.last_auth_error = detail
            raise AuthenticationError(
                f"Authentication failed with status "
                f"{exc.response.status_code}: {detail}"
            ) from exc
        except requests.RequestException as exc:
            self.session_token = None
            self.last_auth_error = str(exc)
            raise AuthenticationError(f"Authentication request failed: {exc}") from exc
        except ValueError as exc:
            self.session_token = None
            self.last_auth_error = str(exc)
            raise AuthenticationError(
                f"Authentication response was invalid: {exc}"
            ) from exc

    def invalidate(self) -> None:
        """Clear the cached session token."""
        self.session_token = None

    def get_headers(self) -> dict[str, str]:
        """Return the authentication headers for API calls."""
        token = self.authenticate()
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": self.config.user_agent,
            "Authorization": token,
        }

    def request(
        self,
        method: str,
        path: str,
        *,
        retry_on_401: bool = True,
        **kwargs: Any,
    ) -> requests.Response:
        """Make an authenticated request and retry once on session expiry."""
        url = f"{self.config.tastytrade_base_url.rstrip('/')}/{path.lstrip('/')}"
        timeout = kwargs.pop("timeout", self.config.request_timeout)
        headers = {**self.get_headers(), **kwargs.pop("headers", {})}

        response = self.session.request(
            method=method.upper(),
            url=url,
            headers=headers,
            timeout=timeout,
            **kwargs,
        )
        if response.status_code == 401 and retry_on_401:
            self.logger.warning("Session expired; re-authenticating and retrying once")
            self.invalidate()
            self.authenticate(force=True)
            return self.request(
                method,
                path,
                retry_on_401=False,
                timeout=timeout,
                **kwargs,
            )

        response.raise_for_status()
        return response

    def _warn_on_unexpected_timestamp(self, data: dict[str, Any]) -> None:
        """Log a warning when token timestamps imply clock skew on the Pi."""
        timestamp_value = None
        for key in ("issued-at", "created-at", "timestamp", "expires-at"):
            if data.get(key):
                timestamp_value = str(data[key])
                break

        if not timestamp_value:
            return

        normalized = timestamp_value.replace("Z", "+00:00")
        try:
            remote_timestamp = datetime.fromisoformat(normalized)
        except ValueError:
            self.logger.warning(
                "Received an unexpected timestamp format from TastyTrade auth: %s",
                timestamp_value,
            )
            return

        if remote_timestamp.tzinfo is None:
            remote_timestamp = remote_timestamp.replace(tzinfo=timezone.utc)

        delta_seconds = abs(
            (datetime.now(timezone.utc) - remote_timestamp).total_seconds()
        )
        if delta_seconds > 300:
            self.logger.warning(
                "Auth timestamp differs from local UTC time by %.0f seconds. "
                "If this Raspberry Pi clock is wrong, check NTP sync.",
                delta_seconds,
            )

    @staticmethod
    def _extract_error_detail(response: requests.Response | None) -> str:
        """Best-effort extraction of a human-readable error message."""
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
