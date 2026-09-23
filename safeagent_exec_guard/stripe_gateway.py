"""Permit-gated Stripe PaymentIntents with durable provider correlation.

This module is intentionally provider-specific.  The Stripe secret belongs at
the effect boundary, never in the agent sandbox.  A SafeAgent permit is
consumed before the Stripe request and its permit ID becomes Stripe's native
idempotency key.  Lost responses remain reconcilable without minting fresh
authority or creating a second logical payment.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from .boundary import (
    ActionRequest,
    BoundaryGateway,
    DispatchReceipt,
    PermitDenied,
    canonical_bytes,
)


STRIPE_PAYMENT_INTENT_CREATE = "stripe.payment_intent.create"
_ALLOWED_CREATE_FIELDS = {
    "amount",
    "currency",
    "customer",
    "description",
    "payment_method",
    "payment_method_types",
    "confirm",
    "capture_method",
    "confirmation_method",
    "setup_future_usage",
    "statement_descriptor",
    "statement_descriptor_suffix",
    "receipt_email",
    "shipping",
    "metadata",
}


class StripePaymentIntents(Protocol):
    def create(self, **params: Any) -> Any: ...

    def retrieve(self, payment_intent_id: str) -> Any: ...


@dataclass(frozen=True)
class StripeObservation:
    permit_id: str
    state: str
    source: str
    payment_intent_id: str | None
    payment_intent_status: str | None
    last_error: str | None = None


def _plain_object(value: Any) -> dict[str, Any]:
    """Convert StripeObject-like values to a JSON-compatible dictionary."""
    if isinstance(value, Mapping):
        raw = dict(value)
    elif hasattr(value, "to_dict"):
        raw = value.to_dict()
    elif hasattr(value, "to_dict_recursive"):
        raw = value.to_dict_recursive()
    else:
        raw = {
            key: getattr(value, key)
            for key in ("id", "object", "status", "amount", "currency", "customer", "metadata")
            if hasattr(value, key)
        }
    return json.loads(json.dumps(raw, default=str))


def _classify(status: str | None) -> str:
    if status == "succeeded":
        return "CONFIRMED"
    if status == "canceled":
        return "REJECTED"
    if status in {
        "processing",
        "requires_action",
        "requires_capture",
        "requires_confirmation",
        "requires_payment_method",
    }:
        return "OPEN"
    return "UNCERTAIN"


class SQLiteStripeStore:
    """Single-host durable correlation and deduplicated webhook journal."""

    def __init__(self, path: str | os.PathLike[str]):
        self.path = str(Path(path))
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS stripe_operations (
                    permit_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    request_json TEXT NOT NULL,
                    payment_intent_id TEXT UNIQUE,
                    payment_intent_status TEXT,
                    reconciliation_state TEXT NOT NULL,
                    boundary_decision TEXT,
                    last_source TEXT,
                    last_error TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS stripe_webhook_events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    payment_intent_id TEXT,
                    received_at INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                );
                """
            )

    def prepare(
        self,
        permit_id: str,
        idempotency_key: str,
        params: Mapping[str, Any],
        *,
        now: int,
    ) -> None:
        request_json = canonical_bytes(dict(params)).decode("utf-8")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT idempotency_key, request_json FROM stripe_operations WHERE permit_id=?",
                (permit_id,),
            ).fetchone()
            if row is None:
                conn.execute(
                    """INSERT INTO stripe_operations
                    (permit_id,idempotency_key,request_json,reconciliation_state,created_at,updated_at)
                    VALUES (?,?,?,'PREPARED',?,?)""",
                    (permit_id, idempotency_key, request_json, now, now),
                )
            elif row["idempotency_key"] != idempotency_key or row["request_json"] != request_json:
                conn.execute("ROLLBACK")
                raise PermitDenied("stripe_operation_collision")
            conn.execute("COMMIT")

    def record_observation(
        self,
        permit_id: str,
        payment_intent: Mapping[str, Any],
        *,
        source: str,
        now: int,
    ) -> StripeObservation:
        payment_intent_id = payment_intent.get("id")
        status = payment_intent.get("status")
        if not isinstance(payment_intent_id, str) or not payment_intent_id:
            raise ValueError("Stripe response has no PaymentIntent id")
        if status is not None and not isinstance(status, str):
            raise ValueError("Stripe PaymentIntent status must be a string")
        state = _classify(status)
        with self._connect() as conn:
            updated = conn.execute(
                """UPDATE stripe_operations
                SET payment_intent_id=?, payment_intent_status=?, reconciliation_state=?,
                    last_source=?, last_error=NULL, updated_at=?
                WHERE permit_id=?""",
                (payment_intent_id, status, state, source, now, permit_id),
            ).rowcount
        if updated != 1:
            raise ValueError("unknown SafeAgent permit correlation")
        return StripeObservation(permit_id, state, source, payment_intent_id, status)

    def record_boundary_decision(self, permit_id: str, decision: str, *, now: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE stripe_operations SET boundary_decision=?, updated_at=? WHERE permit_id=?",
                (decision, now, permit_id),
            )

    def record_error(self, permit_id: str, error: str, *, source: str, now: int) -> None:
        with self._connect() as conn:
            conn.execute(
                """UPDATE stripe_operations SET reconciliation_state='UNCERTAIN',
                last_source=?, last_error=?, updated_at=? WHERE permit_id=?""",
                (source, error[:1000], now, permit_id),
            )

    def find_by_payment_intent(self, payment_intent_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM stripe_operations WHERE payment_intent_id=?", (payment_intent_id,)
            ).fetchone()
        return dict(row) if row else None

    def get(self, permit_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM stripe_operations WHERE permit_id=?", (permit_id,)
            ).fetchone()
        return dict(row) if row else None

    def ingest_webhook(self, event: Mapping[str, Any], *, now: int) -> StripeObservation | None:
        event_id = event.get("id")
        event_type = event.get("type")
        data = event.get("data")
        obj = data.get("object") if isinstance(data, Mapping) else None
        if not isinstance(event_id, str) or not isinstance(event_type, str) or not isinstance(obj, Mapping):
            raise ValueError("invalid Stripe webhook event")
        payment_intent = _plain_object(obj)
        payment_intent_id = payment_intent.get("id")
        payload_json = canonical_bytes(_plain_object(event)).decode("utf-8")
        with self._connect() as conn:
            inserted = conn.execute(
                """INSERT OR IGNORE INTO stripe_webhook_events
                (event_id,event_type,payment_intent_id,received_at,payload_json) VALUES (?,?,?,?,?)""",
                (event_id, event_type, payment_intent_id, now, payload_json),
            ).rowcount
        if inserted == 0 or not isinstance(payment_intent_id, str):
            return None
        operation = self.find_by_payment_intent(payment_intent_id)
        if operation is None:
            return None
        return self.record_observation(
            operation["permit_id"], payment_intent, source=f"webhook:{event_type}", now=now
        )


class StripePaymentIntentGateway:
    """Consume a permit and create exactly one logical Stripe PaymentIntent."""

    def __init__(
        self,
        boundary: BoundaryGateway,
        stripe_payment_intents: StripePaymentIntents,
        store: SQLiteStripeStore,
    ):
        self.boundary = boundary
        self.stripe = stripe_payment_intents
        self.store = store

    @classmethod
    def from_api_key(
        cls,
        boundary: BoundaryGateway,
        api_key: str,
        store: SQLiteStripeStore,
        *,
        allow_live: bool = False,
    ) -> "StripePaymentIntentGateway":
        if not isinstance(api_key, str) or not api_key:
            raise ValueError("Stripe API key is required")
        if api_key.startswith("sk_live_") and not allow_live:
            raise ValueError("live Stripe keys require allow_live=True")
        if not api_key.startswith(("sk_test_", "sk_live_")):
            raise ValueError("expected a Stripe secret key")
        try:
            import stripe
        except ImportError as exc:  # pragma: no cover - exercised by packaging users
            raise RuntimeError("install safeagent-exec-guard[stripe]") from exc
        stripe.api_key = api_key
        return cls(boundary, stripe.PaymentIntent, store)

    @staticmethod
    def _validate(request: ActionRequest) -> dict[str, Any]:
        if request.action != STRIPE_PAYMENT_INTENT_CREATE:
            raise PermitDenied("unsupported_stripe_action")
        if not isinstance(request.payload, Mapping):
            raise PermitDenied("invalid_stripe_payload")
        params = dict(request.payload)
        unknown = set(params) - _ALLOWED_CREATE_FIELDS
        if unknown:
            raise PermitDenied("unsupported_stripe_parameter")
        amount = params.get("amount")
        currency = params.get("currency")
        if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
            raise PermitDenied("invalid_stripe_amount")
        if not isinstance(currency, str) or len(currency.strip()) != 3:
            raise PermitDenied("invalid_stripe_currency")
        if "metadata" in params and not isinstance(params["metadata"], Mapping):
            raise PermitDenied("invalid_stripe_metadata")
        if "confirm" in params and not isinstance(params["confirm"], bool):
            raise PermitDenied("invalid_stripe_confirm")
        if "payment_method_types" in params:
            payment_method_types = params["payment_method_types"]
            if (
                not isinstance(payment_method_types, list)
                or not payment_method_types
                or any(not isinstance(value, str) or not value for value in payment_method_types)
            ):
                raise PermitDenied("invalid_stripe_payment_method_types")
        params["currency"] = currency.lower()
        return params

    def dispatch(
        self, token: str, request: ActionRequest, *, now: int | None = None,
        pre_dispatch: Callable[[], None] | None = None,
    ) -> DispatchReceipt:
        now = int(time.time()) if now is None else int(now)
        params = self._validate(request)
        if pre_dispatch is not None:
            pre_dispatch()
        claims = self.boundary._authorize(token, request, now)
        idempotency_key = f"safeagent:{claims.permit_id}"
        provider_params = dict(params)
        metadata = dict(provider_params.get("metadata") or {})
        metadata.update(
            {
                "safeagent_permit_id": claims.permit_id,
                "safeagent_run_id": request.run_id,
            }
        )
        provider_params["metadata"] = metadata
        self.store.prepare(claims.permit_id, idempotency_key, provider_params, now=now)

        def create(_: ActionRequest) -> dict[str, Any]:
            try:
                # Recheck after durable consumption, immediately before crossing
                # the provider boundary. An intervening expiry/revocation stops
                # the call; the consumed permit remains unresolved, never reusable.
                if pre_dispatch is not None:
                    pre_dispatch()
                payment_intent = _plain_object(
                    self.stripe.create(**provider_params, idempotency_key=idempotency_key)
                )
            except Exception as exc:
                self.store.record_error(
                    claims.permit_id, str(exc), source="create", now=now
                )
                raise
            self.store.record_observation(
                claims.permit_id, payment_intent, source="create_response", now=now
            )
            return payment_intent

        receipt = self.boundary.dispatch(token, request, create, now=now)
        self.store.record_boundary_decision(claims.permit_id, receipt.decision, now=now)
        return receipt

    def reconcile(self, permit_id: str, *, now: int | None = None) -> StripeObservation:
        """Read back a known PaymentIntent or replay create with the same key.

        Replaying create is provider reconciliation, not fresh authority: the
        original permit remains consumed and the same Stripe idempotency key
        and byte-equivalent request parameters are reused.
        """
        now = int(time.time()) if now is None else int(now)
        operation = self.store.get(permit_id)
        if operation is None:
            raise ValueError("unknown SafeAgent permit")
        try:
            if operation["payment_intent_id"]:
                value = self.stripe.retrieve(operation["payment_intent_id"])
                source = "retrieve"
            else:
                params = json.loads(operation["request_json"])
                value = self.stripe.create(
                    **params, idempotency_key=operation["idempotency_key"]
                )
                source = "idempotent_replay"
            return self.store.record_observation(
                permit_id, _plain_object(value), source=source, now=now
            )
        except Exception as exc:
            self.store.record_error(permit_id, str(exc), source="reconcile", now=now)
            return StripeObservation(permit_id, "UNCERTAIN", "reconcile", None, None, str(exc))


class StripeWebhookVerifier:
    """Verify Stripe signatures before journaling and applying observations."""

    def __init__(self, endpoint_secret: str, store: SQLiteStripeStore):
        if not endpoint_secret:
            raise ValueError("Stripe webhook endpoint secret is required")
        try:
            import stripe
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("install safeagent-exec-guard[stripe]") from exc
        self._construct_event = stripe.Webhook.construct_event
        self.endpoint_secret = endpoint_secret
        self.store = store

    def process(self, payload: bytes, signature: str, *, now: int | None = None) -> StripeObservation | None:
        now = int(time.time()) if now is None else int(now)
        event = self._construct_event(payload, signature, self.endpoint_secret)
        return self.store.ingest_webhook(_plain_object(event), now=now)


__all__ = [
    "STRIPE_PAYMENT_INTENT_CREATE",
    "SQLiteStripeStore",
    "StripeObservation",
    "StripePaymentIntentGateway",
    "StripeWebhookVerifier",
]
