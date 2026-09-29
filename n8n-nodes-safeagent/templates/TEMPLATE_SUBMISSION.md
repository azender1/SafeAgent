# n8n Template Submission — SafeAgent Duplicate Webhook Guard

## Title

Stop duplicate webhook side effects with SafeAgent — PROCEED once, SKIP retries

## Short description

Prevent repeated webhook deliveries, workflow retries, or duplicate triggers from executing the same external side effect twice. The first delivery gets **PROCEED**; repeats get **SKIP** before the payment, email, database write, API call, or other irreversible action runs.

## Who this template is for

n8n users running webhook- or agent-triggered workflows that can create costly duplicate consequences:

- payments or refunds
- outbound emails/messages
- database inserts
- order creation or fulfillment
- external API writes
- AI-agent tool calls

## Problem this solves

Webhook systems commonly deliver at least once. Workflows can also retry after timeouts or failures. If the external action already happened but the acknowledgement was lost, a blind retry can repeat the consequence.

SafeAgent puts a durable claim in front of the side effect.

## How it works

1. A Webhook receives an event with a stable `event_id`.
2. **SafeAgent Claim** uses that ID as the Scope.
3. On the first delivery, SafeAgent returns **PROCEED**.
4. The workflow runs the side effect.
5. **SafeAgent Settle** records the completed result.
6. The same `event_id` arriving again returns **SKIP** and never reaches the side-effect node.

## Try it

Install the community node:

```
n8n-nodes-safeagent
```

Send the same payload twice:

```json
{
  "event_id": "order-12345",
  "amount": 100
}
```

Expected behavior:

- first request → `PROCEED` → side-effect branch runs → settled
- second request → `SKIP` → side-effect branch is bypassed

## Important limitation

The n8n community node intentionally uses SafeAgent's free `/claim/test` endpoint, limited to 10 claim calls per IP address. This template is for evaluation and demonstration. Production-volume integrations should use a supported SafeAgent production path or self-hosted deployment.

## Why this differs from a simple in-workflow lookup

The guard is outside the individual workflow execution and uses durable claim state. A repeated workflow run does not need to trust its own transient execution memory to decide whether the consequence may run.

A PENDING claim remains blocked rather than being treated as proof that the external action failed. Ambiguous external outcomes require reconciliation rather than blind replay.

## Package

- npm: `n8n-nodes-safeagent`
- PyPI: `safeagent-exec-guard`
- GitHub: https://github.com/azender1/SafeAgent

## Suggested tags

webhook, idempotency, duplicate prevention, retries, payments, AI agents, workflow reliability, execution guard
