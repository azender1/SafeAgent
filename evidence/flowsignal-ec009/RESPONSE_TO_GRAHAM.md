# Review package for Graham — draft, not sent

The revised offline EC-009 fixture is pinned to your corrected FlowSignal commit `7fe99e13456a492a644a8760f126eee6503fc868` and canonical hash `bb7dbb55025471a21a216f5e08eea74f72a5a6e1bd0513e127d2f84abcc8de93`. SafeAgent's pinned commit and reproducible commands are in README.md and the bundle manifest.

The old `payment.release` GBP → USD Stripe translation is removed. The runner loads `EC-009_stripe_usd_collection.json`, recomputes the canonical action, checks the exact account, directly verifies your permit and receipt at the SafeAgent-controlled provider boundary, caps the downstream expiry, consumes SafeAgent's permit once, blocks replay, and records simulated provider readback separately. Each run has an immutable directory and stable SQLite snapshots. The verifier checks the action, expiry, projection, account string, replay, store records, and mutation cases from evidence files.

This package is only an offline structural test. It has not touched Stripe, cannot prove the account via an external provider, and does not close alternate execution routes. Please challenge the fixture and verifier before either side proceeds to Stripe Test Mode. In particular, I would value a test that mutates the frozen account identifier or the provider projection while recomputing the manifest.
