# Trading Bot Case Study

## Summary

Execution Guard was shaped by the kinds of execution problems that show up in real automated trading systems.

In trading, retries and uncertain execution do not just create reliability issues. They create correctness issues:

- duplicate order attempts
- partial execution
- broker/state mismatches
- repeated close attempts
- uncertain flatten behavior

This is where Execution Guard matters.

---

## Why trading is a strong test case

Automated trading systems operate at the boundary between:

- decision logic
- broker APIs
- live market conditions
- local bot state

When something fails or times out, the system often does not know whether the side effect already happened.

That uncertainty is where duplicate execution risk comes from.

---

## Where the risk appears

### 1. Entry workflow

A spread entry is not one simple action.

It may include:

- buying a long leg
- waiting for fill
- selling a short leg
- handling timeout
- falling back to market order
- resetting if partial execution occurs

If the bot crashes or retries at the wrong point, duplicate or mismatched positions can happen.

---

### 2. Exit workflow

A spread exit can also fail in pieces.

For example:

- one leg closes but the other does not
- a retry happens after partial completion
- local bot state says closed, while broker state says otherwise

This creates correctness risk, not just operational inconvenience.

---

### 3. Flatten workflow

End-of-day flattening is a high-stakes side effect.

If flatten logic is retried without a durable execution record, the system can:

- re-submit close actions
- misread inventory
- create confusion between expected and actual positions

---

## What Execution Guard adds

Execution Guard sits at the side-effect boundary.

Instead of asking only:

> should the bot try again?

it asks:

> has this execution already happened?

Core pattern:

1. create or receive a stable execution ID
2. insert execution record if not exists
3. execute the side effect once
4. store the result
5. on retry, resolve against the recorded execution instead of re-running

---

## Why this matters beyond trading

Trading is only one example.

The same problem appears in:

- payments
- emails
- external API mutations
- webhook handlers
- agent tool calls
- database writes

That is why Execution Guard is a general pattern, not a trading-specific trick.

---

## Practical takeaway

A profitable bot is not the main point.

The stronger lesson is that real automated systems need execution correctness, not just good decisions.

Trading made that visible early.

Execution Guard generalizes the fix.