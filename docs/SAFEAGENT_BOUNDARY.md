# SafeAgent Boundary — one-use authority for consequential agent actions

SafeAgent Boundary is an external capability-control primitive for autonomous
agents. It does not ask the model whether an action is safe. An operator-owned
authority signs permission for one exact action, and an external gateway
atomically consumes that permission before dispatch.

This is an **MVP security primitive**, not a complete sandbox and not a global
exactly-once guarantee.

## The contract

1. The permit signer and durable store remain outside the agent sandbox.
2. A permit binds the principal, run, action, target, canonical payload digest,
   expiry and one-use limit.
3. Any mutation, tool substitution, target substitution, run replay, expiry,
   signature failure or second use is denied before the executor is called.
4. Consumption is an atomic SQLite transaction in the single-host reference
   store.
5. Consumption happens before dispatch.
6. An exception after consumption becomes `PENDING_RECONCILIATION`; time does
   not restore authority.
7. A normal executor return becomes `SETTLED`, not `CONFIRMED`. Only an
   authoritative provider read-back may establish external confirmation.

## Quick example

```python
from safeagent_exec_guard.boundary import (
    ActionRequest,
    BoundaryGateway,
    PermitAuthority,
    SQLitePermitStore,
)

# CONTROL PLANE — never put the private authority in the agent sandbox.
authority = PermitAuthority.generate(issuer="payments-control")

request = ActionRequest(
    principal="invoice-agent",
    run_id="run-2026-09-20-001",
    action="stripe.payment_intent.create",
    target="customer:cus_123",
    payload={"amount": 2500, "currency": "USD", "customer": "cus_123"},
)
permit = authority.issue(request, ttl_seconds=120)

# EFFECT BOUNDARY — owns provider credentials and durable permit state.
gateway = BoundaryGateway(
    public_key_hex=authority.public_key_hex(),
    store=SQLitePermitStore("safeagent-boundary.db"),
)

receipt = gateway.dispatch(
    permit,
    request,
    executor=lambda approved: provider_call(approved.payload),
)
```

The example keeps issuance and dispatch together only for brevity. Real
deployments must separate them so the agent cannot mint its own authority.

## Stripe Test Mode adapter

The optional Stripe adapter turns the boundary primitive into an operational
PaymentIntent control. It is deliberately narrow: v1 permits only
`stripe.payment_intent.create` and rejects unknown request fields, including
credentials supplied by an agent.

```bash
pip install "safeagent-exec-guard[stripe]"
export STRIPE_SECRET_KEY=sk_test_...
export STRIPE_WEBHOOK_SECRET=whsec_...
```

```python
import os

from safeagent_exec_guard import (
    ActionRequest,
    BoundaryGateway,
    PermitAuthority,
    SQLitePermitStore,
    SQLiteStripeStore,
    StripePaymentIntentGateway,
)

authority = PermitAuthority.generate(issuer="payments-control")
permit_store = SQLitePermitStore("safeagent-boundary.db")
boundary = BoundaryGateway(authority.public_key_hex(), permit_store)
stripe_store = SQLiteStripeStore("safeagent-stripe.db")
stripe_gateway = StripePaymentIntentGateway.from_api_key(
    boundary,
    os.environ["STRIPE_SECRET_KEY"],
    stripe_store,
)

request = ActionRequest(
    principal="invoice-agent",
    run_id="run-2026-09-20-001",
    action="stripe.payment_intent.create",
    target="customer:cus_123",
    payload={
        "amount": 2500,
        "currency": "usd",
        "customer": "cus_123",
        "metadata": {"invoice_id": "inv_456"},
    },
)
permit = authority.issue(request, ttl_seconds=120)
receipt = stripe_gateway.dispatch(permit, request)
```

The adapter:

- keeps the Stripe secret at the external effect boundary;
- uses `safeagent:{permit_id}` as Stripe's idempotency key;
- adds the permit and run IDs to Stripe metadata;
- durably correlates the permit and PaymentIntent;
- refuses live secret keys unless `allow_live=True` is explicitly set;
- replays a lost create request only with the original parameters and original
  idempotency key;
- retrieves a known PaymentIntent for authoritative read-back; and
- verifies Stripe webhook signatures before applying webhook observations.

Use `StripeWebhookVerifier.process(raw_body, stripe_signature)` with the exact
raw request bytes and the `Stripe-Signature` header. Duplicate Stripe event IDs
are journaled once. A successful create response remains `SETTLED` at the
boundary; only a retrieved PaymentIntent or verified webhook with status
`succeeded` produces a `CONFIRMED` Stripe observation.

This is a Test Mode reference adapter. Before live use, move both SQLite stores
to a shared transactional backend, enforce default-deny agent egress, configure
restricted Stripe keys, and require explicit operator policy for amount,
currency, customer and velocity limits.

## Current decisions

| Decision | Meaning |
| --- | --- |
| `SETTLED` | The executor returned and SafeAgent stored its result. This is not proof of provider-final outcome. |
| `PENDING_RECONCILIATION` | Dispatch may have reached the provider, but SafeAgent lacks a durable result. The permit remains consumed. |
| `DENY` / `PermitDenied` | Signature, binding, time, collision or consumption rules rejected the action before dispatch. |

Provider reconciliation may later classify external evidence as confirmed,
rejected, conflicting or still uncertain. That belongs to SafeAgent Control;
it is deliberately not inferred by this module.

## Threats covered by the MVP

- Replay of a consumed permit
- Concurrent consumption of one permit
- Payload mutation after approval
- Tool/action substitution
- Target/recipient substitution
- Cross-run and cross-principal replay
- Expired authority
- Forged or corrupted tokens
- Lost/ambiguous executor response reopening an action
- Unserializable local result being mistaken for provider confirmation

## Threats not covered yet

- Kernel, hypervisor or container escape
- Direct network paths that bypass the gateway
- Credentials available inside the sandbox
- A compromised signing authority
- Incorrect operator policy
- Distributed consumption without a shared atomic store
- DNS rebinding, SSRF and network-protocol enforcement
- Provider-specific finality or automatic reconciliation beyond the Stripe
  PaymentIntent reference adapter
- Budget, velocity and human-approval policy

Those are integration requirements, not claims this module makes.

## Deployment requirements for a real sandbox control layer

- Default-deny sandbox egress
- No reusable provider credentials in the sandbox
- All consequential calls forced through an external gateway
- Signer and gateway administered separately from the agent workload
- PostgreSQL or another shared atomic store for multi-host deployments
- Provider-native idempotency keys derived from the same logical action ID
- SafeAgent Control adapter for authoritative outcome reconciliation
- Central audit export and an operator kill switch

## Verification

`tests/test_boundary.py` covers valid dispatch, exact binding, tool/target/
payload/run/principal mutation, expiry, signature forgery, wrong issuer key,
ambiguous outcomes, unserializable receipts, replay, one-use enforcement and a
24-way concurrent-consumption race. `tests/test_stripe_gateway.py` covers
native idempotency propagation, lost-response recovery, provider retrieval,
payload mutation, credential-field rejection, live-key fail-closed behavior,
webhook event deduplication and provider-state classification without making
network calls.

An end-to-end Stripe sandbox run is recorded in
[`STRIPE_VERIFICATION.md`](STRIPE_VERIFICATION.md). The executable test-mode
example is [`../examples/stripe_payment_intent_guard.py`](../examples/stripe_payment_intent_guard.py).
