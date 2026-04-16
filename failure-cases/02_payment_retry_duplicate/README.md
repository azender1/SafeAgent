# Payment Retry Duplicate

## Scenario

A payment workflow submits a charge request for **$100.00**.
The provider times out before the caller receives confirmation.
The system retries the same logical payment.

## Failure

The second attempt posts the charge again.

**Effect:**

- Intended debit: **$100.00**
- Actual debit after replay: **$200.00**
- User impact: duplicate charge, refund handling, support escalation

## Without SafeAgent

```text
[12:00:01] CHARGE customer_123 amount=$100.00
[12:00:03] ERROR: timeout waiting for provider confirmation
[12:00:03] Retrying request...
[12:00:04] CHARGE customer_123 amount=$100.00   <-- DUPLICATE
[12:00:05] Total charged: $200.00
```

## With SafeAgent

```text
[12:00:01] CHARGE customer_123 amount=$100.00
[12:00:03] ERROR: timeout waiting for provider confirmation
[12:00:03] Retrying request...
[12:00:03] SafeAgent: request_id already exists
[12:00:03] SafeAgent: returning cached result
[12:00:04] Total charged: $100.00
```

## Why it happens

The provider may already have committed the charge.
The caller cannot prove that immediately.
Retry turns uncertainty into a second financial side effect.

## SafeAgent outcome

SafeAgent resolves against the prior request record before allowing another irreversible action to fire.
