"""HTTP transport for the Front Systems OData API.

No paging: $top is applied before $filter on this API, so $skip cannot produce
consistent pages. One request per query, with $top pinned high.
"""
from __future__ import annotations

import asyncio
import ssl
from collections.abc import Sequence

import httpx
import truststore

from .config import Config
from .odata import build_params

MAX_ATTEMPTS = 4
BACKOFF_BASE = 0.5
MAX_RETRY_AFTER = 30.0
#: Retry-After is upstream-controlled input. This client backs an interactive MCP
#: tool call, so an honest-but-large value is as unhelpful as a hostile one:
#: better to give up and report rate limiting than to stall the caller.


class ApiError(Exception):
    """Base class for API failures."""


class AuthError(ApiError):
    """Credentials rejected."""


class RateLimitedError(ApiError):
    """Rate limited after exhausting retries."""


class FrontSystemsClient:
    def __init__(self, config: Config, timeout: float = 600.0) -> None:
        self._config = config
        # System trust store: some hosts run a TLS-intercepting proxy under
        # which Python's bundled CA bundle fails on a genuinely valid chain.
        # Disabling verification would expose the keys to the interceptor.
        ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        self._http = httpx.AsyncClient(
            timeout=timeout,
            verify=ctx,
            headers={
                "Ocp-Apim-Subscription-Key": config.subscription_key,
                "x-api-key": config.api_key,
                "Accept": "application/json",
            },
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def fetch(
        self,
        entity: str,
        filters: Sequence[str],
        select: Sequence[str],
    ) -> list[dict]:
        params = build_params(filters, select)
        url = f"{self._config.base_url}/odata/{entity}"
        last: Exception | None = None

        for attempt in range(MAX_ATTEMPTS):
            try:
                response = await self._http.get(url, params=params)
            except httpx.TimeoutException:
                last = ApiError(f"{entity}: request timed out.")
                if attempt < MAX_ATTEMPTS - 1:
                    await self._sleep(attempt)
                continue
            except httpx.TransportError as exc:
                last = ApiError(f"{entity}: network error ({type(exc).__name__}).")
                if attempt < MAX_ATTEMPTS - 1:
                    await self._sleep(attempt)
                continue

            if response.status_code in (401, 403):
                raise AuthError(
                    f"{entity}: authentication rejected (HTTP "
                    f"{response.status_code}). Check the subscription key and "
                    "x-api-key; keys are not shown here."
                )
            if response.status_code == 429:
                last = RateLimitedError(f"{entity}: rate limited.")
                if attempt < MAX_ATTEMPTS - 1:
                    await self._sleep(attempt, response.headers.get("Retry-After"))
                continue
            if response.status_code >= 500:
                last = ApiError(f"{entity}: server error {response.status_code}.")
                if attempt < MAX_ATTEMPTS - 1:
                    await self._sleep(attempt)
                continue
            if response.status_code != 200:
                raise ApiError(f"{entity}: unexpected HTTP {response.status_code}.")

            return self._parse(entity, response)

        raise last or ApiError(f"{entity}: request failed.")

    async def fetch_raw(self, entity: str, params: dict[str, str]) -> list[dict]:
        """Escape hatch for endpoints whose parameters are not OData filters.

        Stockstatus takes snapshotDateTime as an ordinary query parameter, so it
        cannot go through build_params.
        """
        url = f"{self._config.base_url}/odata/{entity}"
        response = await self._http.get(url, params=params)
        if response.status_code in (401, 403):
            raise AuthError(f"{entity}: authentication rejected.")
        if response.status_code != 200:
            raise ApiError(f"{entity}: HTTP {response.status_code}.")
        return self._parse(entity, response)

    @staticmethod
    def _parse(entity: str, response: httpx.Response) -> list[dict]:
        try:
            payload = response.json()
        except ValueError:
            raise ApiError(f"{entity}: response was not JSON.") from None
        if "odata.error" in payload:
            message = payload["odata.error"].get("message", {})
            detail = message.get("value") if isinstance(message, dict) else message
            raise ApiError(f"{entity}: OData error — {detail}")
        if "value" not in payload:
            raise ApiError(
                f"{entity}: response had no 'value' array. Treating this as an "
                "error rather than an empty result, since empty is a legitimate "
                "answer and must not be faked by a malformed body."
            )
        return payload["value"]

    @staticmethod
    async def _sleep(attempt: int, retry_after: str | None = None) -> None:
        if retry_after is not None:
            try:
                seconds = float(retry_after)
                await asyncio.sleep(max(0.0, min(seconds, MAX_RETRY_AFTER)))
                return
            except ValueError:
                pass
        backoff = BACKOFF_BASE * (2 ** attempt)
        await asyncio.sleep(min(backoff, MAX_RETRY_AFTER))
