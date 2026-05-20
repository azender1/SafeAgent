"""SafeAgent hosted cache backend for CrewAI idempotent tool deduplication.

Implements the CacheBackend protocol from crewAI PR #5822 (e094bad),
routing all cache operations to the SafeAgent Railway endpoint instead
of local SQLite. This gives CrewAI tools cross-process, cross-machine
exactly-once semantics gated by x402 micropayments.

Usage
-----
    from safeagent_backend import SafeAgentCacheBackend
    from crewai import Crew
    from crewai.agents.cache import CacheHandler

    backend = SafeAgentCacheBackend(
        endpoint="https://safeagent-production.up.railway.app",
        payment_header="<x402-payment-token>",   # omit for free local dev
    )

    crew = Crew(
        agents=[...],
        tasks=[...],
        cache_backend=backend,
    )

Protocol compatibility
----------------------
Implements: get / set / claim_if_absent
Matches: crewai.agents.cache.cache_backend.CacheBackend (Protocol)

Revenue path
------------
Every claim_if_absent call hits POST /claim → x402 micropayment.
get and set hit GET /audit and POST /settle respectively — currently
free endpoints. When GET /audit is gated (June 10 after Coinbase
unlock), pass payment_header for those calls too.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import requests

_log = logging.getLogger(__name__)

_SENTINEL_MARKER = "__safeagent_sentinel__"
_DEFAULT_TIMEOUT = 10  # seconds per request
_DEFAULT_RETRIES = 3


class SafeAgentCacheBackend:
    """CacheBackend implementation backed by the SafeAgent hosted endpoint.

    Cross-process safe: state lives on Railway, not in the worker.
    x402 gated: every claim_if_absent costs one micropayment.
    Crash-safe: pre-claim survives worker restart because it's server-side.

    Args:
        endpoint: Base URL of the SafeAgent service.
                  Default: https://safeagent-production.up.railway.app
        payment_header: x402 payment token for POST /claim.
                        When None, requests are sent without payment
                        (works against a local dev server with no
                        SAFEAGENT_PAYMENT_ADDRESS set).
        agent_id: Identifier for this CrewAI crew/agent.
                  Recorded on every claim row in the audit log.
        timeout: Per-request timeout in seconds.
        retries: Number of retry attempts on transient network errors.
    """

    def __init__(
        self,
        endpoint: str = "https://safeagent-production.up.railway.app",
        payment_header: str | None = None,
        agent_id: str = "crewai-agent",
        timeout: int = _DEFAULT_TIMEOUT,
        retries: int = _DEFAULT_RETRIES,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._payment_header = payment_header
        self._agent_id = agent_id
        self._timeout = timeout
        self._retries = retries

    # ------------------------------------------------------------------
    # CacheBackend protocol
    # ------------------------------------------------------------------

    def get(self, key: str) -> Any | None:
        """Retrieve a cached value by key via GET /audit.

        Returns the cached result or None if the key is not committed.
        PENDING rows are treated as absent — the caller will re-attempt.
        """
        try:
            resp = self._request(
                "GET",
                "/audit",
                params={"action": key, "status": "COMMITTED", "limit": 1},
            )
            rows = resp.get("rows", [])
            if not rows:
                return None
            raw = rows[0].get("result")
            if raw is None:
                return None
            value = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(value, dict) and value.get(_SENTINEL_MARKER):
                return None
            return value
        except Exception as exc:
            _log.warning("SafeAgent get(%s) failed: %s", key, exc)
            return None

    def set(self, key: str, value: Any) -> None:
        """Persist a committed result via POST /settle/{request_id}.

        First resolves the request_id for this key by checking the audit
        log, then settles it. If no PENDING row exists (e.g. non-idempotent
        tool path), this is a no-op — consistent with InMemoryCacheBackend.
        """
        request_id = self._resolve_request_id(key)
        if request_id is None:
            _log.debug("SafeAgent set(%s): no PENDING row found, skipping settle", key)
            return
        try:
            self._request(
                "POST",
                f"/settle/{request_id}",
                json_body={"result": value},
            )
            _log.debug("SafeAgent set(%s): settled as %s", key, request_id)
        except Exception as exc:
            _log.warning("SafeAgent set(%s) failed: %s", key, exc)

    def claim_if_absent(self, key: str, sentinel: Any) -> tuple[bool, Any | None]:
        """Atomically claim a key via POST /claim (x402 gated).

        Maps SafeAgent semantics to CacheBackend protocol:
            PROCEED  → (True,  None)          — caller owns the claim
            SKIP     → (False, cached_result) — duplicate, reuse result
            PENDING  → (False, sentinel)      — in-flight, treat as taken

        The sentinel written server-side marks the row as a pre-claim.
        On success the caller must call set() to overwrite with the real
        result (CrewAI's CacheHandler does this automatically).
        """
        try:
            resp = self._request(
                "POST",
                "/claim",
                json_body={
                    "agent_id": self._agent_id,
                    "action_type": "tool_execution",
                    "scope": key,
                },
                payment=True,
            )
            status = resp.get("status")

            if status == "PROCEED":
                _log.debug("SafeAgent claim_if_absent(%s): PROCEED", key)
                return True, None

            if status == "SKIP":
                cached = resp.get("existing")
                if cached is None:
                    cached = self.get(key)
                _log.debug("SafeAgent claim_if_absent(%s): SKIP", key)
                return False, cached

            if status == "PENDING":
                _log.debug("SafeAgent claim_if_absent(%s): PENDING (in-flight)", key)
                return False, sentinel

            _log.warning("SafeAgent claim_if_absent(%s): unexpected status %s", key, status)
            return True, None

        except Exception as exc:
            _log.warning(
                "SafeAgent claim_if_absent(%s) failed (%s), falling back to PROCEED",
                key, exc,
            )
            return True, None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_request_id(self, key: str) -> str | None:
        """Find the PENDING request_id for a given scope key."""
        try:
            resp = self._request(
                "GET",
                "/audit",
                params={"action": key, "status": "PENDING", "limit": 1},
            )
            rows = resp.get("rows", [])
            if rows:
                return rows[0].get("request_id")
        except Exception as exc:
            _log.warning("SafeAgent _resolve_request_id(%s) failed: %s", key, exc)
        return None

    def _request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        json_body: dict | None = None,
        payment: bool = False,
    ) -> dict:
        """Execute an HTTP request with retry logic."""
        url = f"{self._endpoint}{path}"
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if payment and self._payment_header:
            headers["x-payment"] = self._payment_header

        last_exc: Exception | None = None
        for attempt in range(self._retries):
            try:
                resp = requests.request(
                    method,
                    url,
                    headers=headers,
                    params=params,
                    json=json_body,
                    timeout=self._timeout,
                )
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as exc:
                last_exc = exc
                if attempt < self._retries - 1:
                    wait = 0.5 * (2 ** attempt)
                    _log.debug("SafeAgent request retry %d/%d in %.1fs", attempt + 1, self._retries, wait)
                    time.sleep(wait)

        raise RuntimeError(f"SafeAgent request failed after {self._retries} attempts: {last_exc}")
