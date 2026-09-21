# FlowSignal EC-009 × SafeAgent × Stripe Test Mode fixture

This package is a bounded, reproducible operator fixture requested in
`grahamb-ai/flowsignal-agentic-payments#4`.

It connects three separate controls without collapsing their claims:

1. **FlowSignal** evaluates current represented authority and issues an exact-action
   execution permit.
2. **SafeAgent** binds that FlowSignal permit to one Stripe PaymentIntent request,
   durably consumes a one-use dispatch permit before the provider call, and blocks
   exact replay.
3. **Stripe** is queried after dispatch to establish the provider-side outcome.

## Frozen sources

- FlowSignal commit: `471d8af596044d61f86f774f70cd8c6ff2efc31c`
- SafeAgent release: `v0.1.25`
- SafeAgent commit: `b8a2fa6baa98f0d469e3f3787b420648596295d0`

## Run a safe offline structural check

From a checkout of the frozen FlowSignal commit, with SafeAgent v0.1.25 installed:

```powershell
python run_fixture.py --flowsignal-root C:\path\to\flowsignal-agentic-payments --mode simulated
python verify_bundle.py evidence\latest
```

The simulated provider is deliberately labelled `SIMULATED`; it validates the
binding, capture and replay mechanics but is not Stripe evidence.

## Run Graham's independently operated Stripe Test Mode fixture

Set a Stripe **test** secret key and run:

```powershell
$env:STRIPE_SECRET_KEY="sk_test_..."
python run_fixture.py --flowsignal-root C:\path\to\flowsignal-agentic-payments --mode stripe-test
python verify_bundle.py evidence\latest
```

The runner refuses live keys. Send back the generated `evidence/latest` directory
or its zip. No secret key is written to evidence.

## Evidence emitted

- FlowSignal authority request, receipt and execution permit;
- explicit FlowSignal→SafeAgent binding record;
- SafeAgent permit claims, first dispatch, replay denial and boundary journal;
- Stripe operation correlation and authoritative retrieval result;
- provider call count for the simulated mode;
- SHA-256 manifest over every retained evidence file.

## Exact claim boundary

If the independently operated Stripe Test Mode run passes, the supported statement
is limited to:

> One correctly bound FlowSignal-permitted action produced one confirmed Stripe
> Test Mode payment, and reuse of the bound SafeAgent dispatch permit was blocked
> on the tested gateway-controlled path.

It does not establish production settlement, alternate-route closure,
deployment-wide non-bypassability, distributed atomicity or certification.

