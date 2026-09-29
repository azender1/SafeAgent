# n8n Template Submission Packet — SafeAgent Duplicate Webhook Guard

Status: READY FOR CREATOR SUBMISSION AFTER COMMUNITY NODE REVIEW
Package dependency: `n8n-nodes-safeagent@0.2.6`
Package release status: FROZEN — do not publish a new package version for this template.

## Listing title

**Stop duplicate webhook side effects with SafeAgent — PROCEED once, SKIP retries**

## Short description

Prevent repeated webhook deliveries, workflow retries, or duplicate triggers from executing the same external side effect twice. The first delivery gets **PROCEED**; repeats get **SKIP** before the payment, email, database write, API call, or other irreversible action runs.

## Long description

Webhook systems commonly deliver at least once, and n8n workflows can be retried after timeouts or failures. If an external action already happened but the acknowledgement was lost, blindly running the workflow again can repeat the consequence.

This template places SafeAgent in front of the side-effecting step.

The first delivery claims a durable execution slot and receives **PROCEED**. The workflow performs the side effect and settles the claim. A repeated delivery with the same stable `event_id` receives **SKIP** and never reaches the side-effect node.

Use this pattern for:

- payment or refund workflows
- outbound email or messaging
- order creation or fulfillment
- database inserts
- external API writes
- AI-agent tool calls

The included community node uses SafeAgent's free test endpoint and is intended for evaluation/demo use. It is currently limited to 10 claim calls per IP address.

## Workflow logic

```
Webhook
  |
  v
SafeAgent Claim
  |--------------------|
  |                    |
PROCEED               SKIP
  |                    |
  v                    v
Side Effect         SKIP Response
  |
  v
SafeAgent Settle
  |
  v
PROCEED Response
```

## Example request body

```json
{
  "event_id": "order-12345",
  "amount": 100,
  "customer_id": "customer-42"
}
```

Send the exact same `event_id` twice.

### Expected first response

```json
{
  "ok": true,
  "decision": "PROCEED",
  "message": "Side effect executed once and settled."
}
```

### Expected second response

```json
{
  "ok": true,
  "decision": "SKIP",
  "message": "Duplicate blocked before the side effect."
}
```

## Required community node

Install:

```
n8n-nodes-safeagent
```

Use the already-published `0.2.6` package. Do not publish another node version just for this workflow template while the node is under n8n review.

## Creator submission tags

- webhook
- idempotency
- duplicate prevention
- retries
- payments
- AI agents
- workflow reliability
- execution guard

## Suggested category

Workflow automation / Development / Reliability

## Screenshot set to capture after n8n node approval

Capture real n8n UI screenshots only. Do not use mock screenshots.

1. **Full workflow canvas**
   - Webhook
   - SafeAgent Claim
   - Your Side Effect
   - SafeAgent Settle
   - PROCEED Response
   - SKIP Response

2. **First execution**
   - SafeAgent Claim output visibly showing `PROCEED`
   - execution path visibly reaches `Your Side Effect`

3. **Duplicate execution**
   - same `event_id`
   - SafeAgent Claim output visibly showing `SKIP`
   - execution path visibly bypasses `Your Side Effect`

4. **Optional comparison graphic**
   - Run 1: PROCEED -> executes once
   - Run 2: SKIP -> duplicate blocked

## Submission evidence checklist

Before publishing the template listing:

- [ ] n8n community-node review is approved
- [ ] `n8n-nodes-safeagent@0.2.6` remains available
- [ ] template imports successfully
- [ ] first duplicate-demo request returns PROCEED
- [ ] second request using the same event ID returns SKIP
- [ ] no package release was required
- [ ] screenshots are captured from a real n8n execution
- [ ] listing does not claim production certification or exactly-once guarantees beyond the tested guard behavior

## Core positioning

**One webhook. One side effect.**

First delivery: **PROCEED**

Duplicate delivery: **SKIP**

The value proposition is not generic "idempotency." The workflow demonstrates an execution boundary that remains outside the individual n8n run and prevents a repeated logical action from reaching the side-effecting node.
