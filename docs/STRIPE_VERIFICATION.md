# Stripe Test Mode verification

Date: 2026-09-20  
SafeAgent candidate: 0.1.25  
Stripe environment: Test Mode

## Claim under test

For a PaymentIntent forced through the SafeAgent boundary, one signed permit
authorizes one exact provider dispatch. Reusing that permit must be denied, and
the external outcome must be established by authoritative Stripe evidence.

## Procedure

1. Create a one-use permit bound to `stripe.payment_intent.create`, USD 1.00,
   `payment_method_types=["card"]`, Stripe test payment method `pm_card_visa`,
   and `confirm=true`.
2. Dispatch through `StripePaymentIntentGateway` with a Stripe Test Mode secret.
3. Attempt the exact same dispatch with the consumed permit.
4. Reconcile the stored provider correlation through Stripe retrieval.
5. Inspect Stripe's Test Mode dashboard.

## Observed result

| Check | Result |
| --- | --- |
| First dispatch | `SETTLED` |
| Stripe PaymentIntent status | `succeeded` |
| Exact replay | `BLOCKED - permit_already_consumed` |
| Reconciliation | `CONFIRMED` |
| Evidence source | Stripe `retrieve` |
| Dashboard | One successful USD 1.00 test payment, test card ending 4242 |

The earlier unconfirmed setup run remains a separate incomplete Test Mode
PaymentIntent. It is not a duplicate successful payment.

## Guarantee boundary

This result establishes the adapter's bounded behavior for the exercised path.
It does not establish provider-wide exactly-once execution, prevent calls that
bypass the gateway, or make SQLite a multi-host production store. Live use
requires restricted provider credentials outside the agent sandbox, enforced
egress through the gateway, operator policy for amount and velocity, and a
shared transactional store.

## Reproduce

Install the Stripe extra, set `STRIPE_SECRET_KEY` to a Test Mode key, and run:

```bash
python examples/stripe_payment_intent_guard.py
```

The example refuses live Stripe keys.
