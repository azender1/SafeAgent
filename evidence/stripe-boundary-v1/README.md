# SafeAgent Stripe boundary fixture v1

This bounded, offline fixture demonstrates the payment execution boundary
discussed in [`stripe/ai#402`](https://github.com/stripe/ai/issues/402).
It performs no network calls and needs no Stripe credential.

The fixture proves that:

1. a signed permit is durably consumed before provider dispatch;
2. the permit ID becomes Stripe's native idempotency key;
3. replay of the consumed permit is blocked before a second provider call;
4. a lost create response remains `PENDING_RECONCILIATION`;
5. reconciliation reuses the original request and idempotency key; and
6. authoritative provider readback can move the observation to `CONFIRMED`.

It also checks the RFC 8785/JCS identity boundary: object-key order and the
equivalent JSON number representations `100` and `100.0` produce the same
digest, while the string `"100"` does not.

## Run

From the repository root:

```powershell
python -m pip install -e ".[stripe,test]"
python evidence/stripe-boundary-v1/run_fixture.py
```

Expected final line:

```text
RESULT: 10/10 invariants passed
```

The fake provider implements Stripe's idempotent-create behavior and makes the
fixture deterministic. A separate test-mode Stripe run observed a succeeded
PaymentIntent, blocked permit replay, provider retrieval, and `CONFIRMED`
reconciliation. No secret key, customer data, full PaymentIntent identifier,
or claim database is included here.

## Scope

This fixture is implementation evidence, not a Stripe endorsement and not a
claim of fuzzy semantic equivalence. Canonicalization removes representational
drift; it must not merge materially different payment intent.

See [`PROVENANCE.json`](PROVENANCE.json) for the contribution lineage and
source discussion.
