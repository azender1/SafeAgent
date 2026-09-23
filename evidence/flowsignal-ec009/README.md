# SafeAgent × FlowSignal EC-009: corrected offline fixture

This fixture uses Graham's corrected frozen FlowSignal revision. Its default path runs a **simulated** provider. A separate gated Stripe Test Mode path is included for Graham's review; it has not been run against Stripe.

## Pinned external action

- FlowSignal commit: `7fe99e13456a492a644a8760f126eee6503fc868`
- Canonical SHA-256: `bb7dbb55025471a21a216f5e08eea74f72a5a6e1bd0513e127d2f84abcc8de93`
- Action: `payment.collect`, USD 100 minor units, `pm_card_visa`, account `acct_1U06JYL6P3JlguFB`.
- Target: `stripe:test:acct_1U06JYL6P3JlguFB:payment_intent.create`.
- The SafeAgent commit is the exact commit printed by the runner and recorded in its bundle manifest; pin it when reviewing.

## Reproduce from clean checkouts

```bash
git clone https://github.com/grahamb-ai/flowsignal-agentic-payments.git FlowSignal
git -C FlowSignal checkout 7fe99e13456a492a644a8760f126eee6503fc868
git clone https://github.com/azender1/SafeAgent.git SafeAgent
git -C SafeAgent checkout <SafeAgent-commit-from-the-delivery>
python -m pip install -r SafeAgent/evidence/flowsignal-ec009/requirements.txt
python SafeAgent/evidence/flowsignal-ec009/run_fixture.py --flowsignal-root FlowSignal
python SafeAgent/evidence/flowsignal-ec009/verify_bundle.py <bundle-path-printed-by-runner> --flowsignal-root FlowSignal
```

Run commands from the directory containing both checkouts. The runner prints an immutable run path. The verifier must print `"passed": true` and `"problems": []`. Each invocation writes a new directory under `evidence/flowsignal-ec009/evidence/runs/`, never replacing another run.

Windows PowerShell uses the same Python commands and `git -C` commands. Substitute Windows checkout paths for `FlowSignal` and `SafeAgent` and pass the printed bundle path to the verifier.

## Mechanism and scope

`BoundEC009Gateway` requires an upstream check both before SafeAgent consumes its permit and immediately before the provider call. The check independently recomputes the frozen action, verifies the FlowSignal receipt HMAC and signed permit against the pinned harness, requires ALLOW and the current authority state version, checks expiry, and compares the SafeAgent request to FlowSignal's deterministic Stripe projection. The SafeAgent permit expires no later than FlowSignal's permit. The gateway durably consumes it once; replay is blocked. Retrieval of the simulated PaymentIntent is recorded separately from executor SETTLED.

The bundle contains primary records, durable SQLite snapshots without WAL/SHM sidecars, negative cases, and a SHA-256 manifest. The verifier independently recomputes every `summary.json` field and the manifest's mode and claim scope from the primary records. `test_claim_integrity.py` preserves Graham's coordinated manifest and summary mutation: the original bundle passes and the false Stripe execution claim fails.

**This is a local reference harness.** FlowSignal's reference HMAC key and SafeAgent's offline private key are visible in code. The simulated account identity is declared by the fake provider and is not independently proven by Stripe. This does not establish production secret isolation, alternate-route closure, distributed atomicity, actual Stripe execution, or commercial adoption. The generic Stripe gateway also permits clients to omit the optional pre-dispatch hook; the bounded claim applies to the `BoundEC009Gateway` route only. Never give the agent a direct Stripe key.

## Proposed Stripe Test Mode execution path: review gate

**Do not run this path until Graham has reviewed and cleared the exact candidate. No Stripe request has been made for this candidate.** The operator supplies `STRIPE_SECRET_KEY` to the process environment, never to the agent or CLI arguments. The runner refuses anything except `sk_test_` and requires `--review-cleared`.

After clearance, from the parent directory of the pinned checkouts:

```bash
export STRIPE_SECRET_KEY='sk_test_<operator-supplied-secret>'
python SafeAgent/evidence/flowsignal-ec009/run_fixture.py \
  --flowsignal-root FlowSignal --mode stripe-test --review-cleared
python SafeAgent/evidence/flowsignal-ec009/verify_bundle.py \
  <new-bundle-path> --flowsignal-root FlowSignal
unset STRIPE_SECRET_KEY
```

The runner verifies the frozen FlowSignal action and SHA before touching Stripe. A key-bound `StripeClient` with automatic network retries disabled calls `GET /v1/account` to read the **authenticated** account ID; it refuses a mismatch with `acct_1U06JYL6P3JlguFB` before creating anything. The same client submits the unchanged FlowSignal Stripe projection with the SafeAgent permit ID as the idempotency key, under the mandatory upstream and one-use boundary checks. It calls `GET /v1/payment_intents/{id}` only after an ID is durably known; a lost create response remains uncertain and never causes a second create. On dispatch failure, it preserves the SQLite journals and an `uncertain.json` record for manual reconciliation, not a passing bundle. On success, both response and retrieval are recorded with selected non-secret fields, alongside the account readback, operation journal, first dispatch, replay denial, and distinct `stripe-test` manifest and summary. The verifier checks amount, amount received, currency, payment method, metadata, status, ID, and `livemode: false`. The provider response is local evidence; an offline verifier cannot cryptographically prove that the recorded Stripe API responses originated at Stripe. Independent reviewer readback in the Stripe Dashboard or API is still needed to establish that external fact.

Before clearance, review can run `python SafeAgent/evidence/flowsignal-ec009/test_stripe_test_path.py` to exercise the key/account gate and request shape with an in-memory fake. This test makes no network calls. `python SafeAgent/evidence/flowsignal-ec009/test_claim_integrity.py <simulated-bundle> --flowsignal-root FlowSignal` repeats Graham's hostile mutation.
