# SafeAgent 0.1.25 — permit-gated Stripe PaymentIntents

## Stripe execution boundary

Version 0.1.25 adds a deliberately narrow Stripe PaymentIntent adapter on top
of SafeAgent's signed, one-use execution permits.

- consumes the permit before provider dispatch;
- derives Stripe's native idempotency key from the SafeAgent permit ID;
- binds amount, currency, target and the complete canonical payload;
- stores the Stripe PaymentIntent correlation durably;
- blocks exact replay after the permit is consumed;
- preserves ambiguous create failures for reconciliation and operator diagnosis;
- reconciles through authoritative Stripe retrieval or verified webhooks;
- rejects credentials and unsupported parameters supplied by the agent; and
- refuses live secret keys unless the operator explicitly enables them.

The adapter now accepts an explicit `payment_method_types` list, enabling the
bounded confirmed-card sandbox example without enabling redirect-based payment
methods. Provider create errors are retained in the operation record instead of
being lost behind a generic `PENDING_RECONCILIATION` receipt.

## Verified sandbox result

On 2026-09-20, the public example was exercised against Stripe Test Mode:

- first dispatch: `SETTLED`;
- Stripe status: `succeeded`;
- exact replay: blocked with `permit_already_consumed`;
- reconciliation: `CONFIRMED` from Stripe `retrieve`; and
- Stripe dashboard: one successful USD 1.00 test payment using test card 4242.

This verifies the bounded claim: SafeAgent can authorize one exact
PaymentIntent, prevent reuse of that authority, and independently read back the
provider outcome. It does not claim global exactly-once behavior or protect a
provider path that bypasses the gateway.

## Packaging

The runtime `safeagent_exec_guard.__version__` now matches the project version.
