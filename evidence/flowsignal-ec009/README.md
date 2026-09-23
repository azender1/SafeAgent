# SafeAgent × FlowSignal EC-009: corrected offline fixture

This fixture uses Graham's corrected frozen FlowSignal revision. It runs a **simulated** provider only. No Stripe secret is needed or accepted. Graham should inspect this structure before either side executes Stripe Test Mode.

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
python -m pip install rfc8785 cryptography
python SafeAgent/evidence/flowsignal-ec009/run_fixture.py --flowsignal-root FlowSignal
python SafeAgent/evidence/flowsignal-ec009/verify_bundle.py <bundle-path-printed-by-runner> --flowsignal-root FlowSignal
```

Run commands from the directory containing both checkouts. The runner prints an immutable run path. The verifier must print `"passed": true` and `"problems": []`. Each invocation writes a new directory under `evidence/flowsignal-ec009/evidence/runs/`, never replacing another run.

Windows PowerShell uses the same Python commands and `git -C` commands. Substitute Windows checkout paths for `FlowSignal` and `SafeAgent` and pass the printed bundle path to the verifier.

## Mechanism and scope

`BoundEC009Gateway` requires an upstream check both before SafeAgent consumes its permit and immediately before the provider call. The check independently recomputes the frozen action, verifies the FlowSignal receipt HMAC and signed permit against the pinned harness, requires ALLOW and the current authority state version, checks expiry, and compares the SafeAgent request to FlowSignal's deterministic Stripe projection. The SafeAgent permit expires no later than FlowSignal's permit. The gateway durably consumes it once; replay is blocked. Retrieval of the simulated PaymentIntent is recorded separately from executor SETTLED.

The bundle contains primary records, durable SQLite snapshots without WAL/SHM sidecars, negative cases, and a SHA-256 manifest. The verifier checks semantic relationships and re-runs representative negative cases, rather than trusting `summary.json`.

**This is a local reference harness.** FlowSignal's reference HMAC key and SafeAgent's offline private key are visible in code. The simulated account identity is declared by the fake provider and is not independently proven by Stripe. This does not establish production secret isolation, alternate-route closure, distributed atomicity, actual Stripe execution, or commercial adoption. The generic Stripe gateway also permits clients to omit the optional pre-dispatch hook; the bounded claim applies to the `BoundEC009Gateway` route only. Never give the agent a direct Stripe key.

## Next review gate

Please inspect and attack the fixture and offline verifier first. Only after Graham explicitly accepts the structural fixture should a separately operated Stripe Test Mode path be added and run, with independent Stripe account readback and retrieval evidence. This deliverable intentionally contains no live Stripe runner.
