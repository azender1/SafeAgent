# SafeAgent / Execution Guard

## Prevent duplicate or incorrect execution when retries happen

A missing execution boundary for **AI agents, trading bots, automations, and workflow systems**.

When a system hits:

- timeout
- partial failure
- retry
- uncertain completion

…it often **does not know whether the action already happened**.

That is how you get:

- duplicate trades
- duplicate payments
- duplicate emails
- duplicate API mutations

SafeAgent adds a durable execution boundary around **real side effects** so retries can be **reconciled instead of replayed**.

> Most systems can retry. Very few can decide when a retry is still correct.

---

## Trading Bot Retry Demo

### Same workflow. Same retry. Different result.

A broker timeout does **not** mean the order failed.  
It means the result is **uncertain**.

Without SafeAgent:
- the bot retries
- the same logical order is submitted again
- the position is duplicated

With SafeAgent:
- the retry resolves against the same execution record
- the result is reconciled
- the duplicate is blocked

![SafeAgent Trading Demo](assets/safeagent_trading_demo_v2.gif)

This is not just a trading problem.

The same failure mode shows up in:
- payments
- browser workflows
- background jobs
- webhooks
- ticket creation
- agent tool calls
- external API mutations

---

## Try it now

### Install

```bash
pip install safeagent-exec-guard
```

---

## Mental model

Without SafeAgent:
retry → replay → duplicate

With SafeAgent:
retry → resolve → safe

---

## Where this matters

- trading
- payments
- APIs
- agents
- workflows

---

## License

Apache-2.0
