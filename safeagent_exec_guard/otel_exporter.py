"""OpenTelemetry exporter for SafeAgent execution events.

Emits one span per execution attempt over OTLP/HTTP. Spans follow this schema:

    span.name        = "safeagent.execute"
    trace_id         = execution_id        (the logical execution)
    span_id          = attempt_id          (this individual attempt)
    parent_span_id   = chain_execution_id  (the agent chain that called us)

Attributes attached to every span:
    action            (str)   - the SafeAgent-registered action name
    status            (str)   - "ok" | "duplicate" | "error" | ...
    side_effect_fired (bool)  - did the underlying side-effecting function run
    receipt_returned  (bool)  - was a cached receipt returned instead
    payload_hash      (str)   - hex digest of the payload, for correlation

Transport is OTLP/HTTP with JSON encoding (Content-Type: application/json),
which keeps the dependency footprint to just `requests`. Point it at any
OTLP/HTTP-compatible collector with the ``endpoint`` arg or the
``OTEL_EXPORTER_OTLP_ENDPOINT`` environment variable.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

import requests

logger = logging.getLogger(__name__)

DEFAULT_ENDPOINT = os.environ.get(
    "OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318"
)
DEFAULT_SERVICE_NAME = os.environ.get("OTEL_SERVICE_NAME", "safeagent")
SPAN_NAME = "safeagent.execute"
SPAN_KIND_INTERNAL = 1  # OTLP SpanKind.SPAN_KIND_INTERNAL
STATUS_CODE_OK = 1
STATUS_CODE_ERROR = 2


def _to_trace_id(value: Optional[str]) -> str:
    """Normalize an arbitrary string into a 32-char hex trace id (16 bytes)."""
    if value is None:
        return uuid.uuid4().hex
    s = str(value).strip()
    h = s.replace("-", "")
    if len(h) == 32:
        try:
            int(h, 16)
            return h.lower()
        except ValueError:
            pass
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:32]


def _to_span_id(value: Optional[str]) -> str:
    """Normalize an arbitrary string into a 16-char hex span id (8 bytes)."""
    if value is None:
        return uuid.uuid4().hex[:16]
    s = str(value).strip()
    h = s.replace("-", "")
    if len(h) == 16:
        try:
            int(h, 16)
            return h.lower()
        except ValueError:
            pass
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def _kv(key: str, value: Any) -> Dict[str, Any]:
    """Serialize an attribute key/value pair in OTLP/JSON AnyValue shape."""
    if isinstance(value, bool):
        return {"key": key, "value": {"boolValue": value}}
    if isinstance(value, int):
        return {"key": key, "value": {"intValue": str(value)}}
    if isinstance(value, float):
        return {"key": key, "value": {"doubleValue": value}}
    return {
        "key": key,
        "value": {"stringValue": "" if value is None else str(value)},
    }


@dataclass
class ExecutionSpan:
    """Logical representation of a single SafeAgent execution attempt."""

    execution_id: str
    attempt_id: str
    chain_execution_id: Optional[str]
    action: str
    status: str
    side_effect_fired: bool
    receipt_returned: bool
    payload_hash: str
    start_time_ns: int = field(default_factory=lambda: time.time_ns())
    end_time_ns: Optional[int] = None
    extra_attributes: Mapping[str, Any] = field(default_factory=dict)

    def finish(self) -> "ExecutionSpan":
        """Stamp ``end_time_ns`` if it has not been set yet."""
        if self.end_time_ns is None:
            self.end_time_ns = time.time_ns()
        return self

    def to_otlp_span(self) -> Dict[str, Any]:
        end_ns = (
            self.end_time_ns if self.end_time_ns is not None else time.time_ns()
        )
        attrs: List[Dict[str, Any]] = [
            _kv("action", self.action),
            _kv("status", self.status),
            _kv("side_effect_fired", bool(self.side_effect_fired)),
            _kv("receipt_returned", bool(self.receipt_returned)),
            _kv("payload_hash", self.payload_hash),
        ]
        for k, v in self.extra_attributes.items():
            attrs.append(_kv(k, v))

        status_code = (
            STATUS_CODE_ERROR
            if str(self.status).lower() == "error"
            else STATUS_CODE_OK
        )

        span: Dict[str, Any] = {
            "traceId": _to_trace_id(self.execution_id),
            "spanId": _to_span_id(self.attempt_id),
            "name": SPAN_NAME,
            "kind": SPAN_KIND_INTERNAL,
            "startTimeUnixNano": str(self.start_time_ns),
            "endTimeUnixNano": str(end_ns),
            "attributes": attrs,
            "status": {"code": status_code},
        }
        if self.chain_execution_id:
            span["parentSpanId"] = _to_span_id(self.chain_execution_id)
        return span


class OTLPHTTPExporter:
    """Minimal OTLP/HTTP+JSON exporter for SafeAgent execution spans.

    Parameters
    ----------
    endpoint:
        OTLP/HTTP endpoint. May be a base URL (e.g. ``http://collector:4318``)
        or the full traces URL (``.../v1/traces``). Defaults to the
        ``OTEL_EXPORTER_OTLP_ENDPOINT`` env var, then ``http://localhost:4318``.
    service_name:
        Resource ``service.name``. Defaults to ``OTEL_SERVICE_NAME`` or
        ``"safeagent"``.
    headers:
        Extra HTTP headers (e.g. auth tokens for hosted backends).
    timeout:
        Per-request timeout in seconds.
    """

    def __init__(
        self,
        endpoint: Optional[str] = None,
        service_name: str = DEFAULT_SERVICE_NAME,
        headers: Optional[Mapping[str, str]] = None,
        timeout: float = 5.0,
    ) -> None:
        base = (endpoint or DEFAULT_ENDPOINT).rstrip("/")
        self.url = base if base.endswith("/v1/traces") else f"{base}/v1/traces"
        self.service_name = service_name
        self.timeout = timeout
        self._headers: Dict[str, str] = {"Content-Type": "application/json"}
        if headers:
            self._headers.update(headers)

    def export(self, span: ExecutionSpan) -> bool:
        """Export a single span. Returns True on 2xx, False otherwise."""
        return self.export_batch([span])

    def export_batch(self, spans: List[ExecutionSpan]) -> bool:
        if not spans:
            return True
        body = {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [_kv("service.name", self.service_name)]
                    },
                    "scopeSpans": [
                        {
                            "scope": {
                                "name": "safeagent_exec_guard",
                                "version": "1",
                            },
                            "spans": [
                                s.finish().to_otlp_span() for s in spans
                            ],
                        }
                    ],
                }
            ]
        }
        try:
            resp = requests.post(
                self.url,
                json=body,
                headers=self._headers,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            logger.warning("OTLP export failed: %s", exc)
            return False
        if 200 <= resp.status_code < 300:
            return True
        logger.warning(
            "OTLP export returned %s: %s", resp.status_code, resp.text[:200]
        )
        return False


def record_execution(
    *,
    execution_id: str,
    attempt_id: str,
    chain_execution_id: Optional[str],
    action: str,
    status: str,
    side_effect_fired: bool,
    receipt_returned: bool,
    payload_hash: str,
    exporter: Optional[OTLPHTTPExporter] = None,
    start_time_ns: Optional[int] = None,
    end_time_ns: Optional[int] = None,
    extra_attributes: Optional[Mapping[str, Any]] = None,
) -> bool:
    """Build and export a single ``ExecutionSpan`` in one call.

    Returns True on a 2xx response from the OTLP endpoint, False otherwise.
    Exceptions are logged and swallowed so observability never breaks the
    caller's hot path.
    """
    span = ExecutionSpan(
        execution_id=execution_id,
        attempt_id=attempt_id,
        chain_execution_id=chain_execution_id,
        action=action,
        status=status,
        side_effect_fired=side_effect_fired,
        receipt_returned=receipt_returned,
        payload_hash=payload_hash,
        start_time_ns=start_time_ns
        if start_time_ns is not None
        else time.time_ns(),
        end_time_ns=end_time_ns,
        extra_attributes=extra_attributes or {},
    )
    return (exporter or OTLPHTTPExporter()).export(span)


__all__ = [
    "ExecutionSpan",
    "OTLPHTTPExporter",
    "SPAN_NAME",
    "record_execution",
]
