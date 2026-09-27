# SafeAgent Buy-vs-Build Matrix

This document is the core strategic test. A company belongs in the acquisition program only if SafeAgent fills a capability that is expensive, slow, risky, or distracting for it to recreate.

| Target | What it already owns | SafeAgent's proposed missing layer | Biggest redundancy risk | Current fit |
|---|---|---|---|---|
| Dynatrace | Observability, autonomous operations, AI evaluation | Consequence control + reconciliation | May build execution guard inside autonomous ops | Research |
| Datadog | Observability, security, AI infrastructure | Guarded external side effects | May view this as app/workflow responsibility | Research |
| Cisco/Splunk | Identity, Agentic SOC, AI observability | Post-authorization consequence integrity | May frame duplicates as reliability not security | Research |
| CrowdStrike | Runtime agent security, continuous authorization | One-use action settlement + replay state | Reliability may sit outside Falcon scope | Research |
| ServiceNow | Workflow engine, AI control tower, autonomous work | External-action uncertainty/reconciliation | Existing workflow semantics may already cover much of it | Research |
| Palo Alto Networks | AI Gateway, Prisma AIRS, agent governance | Post-gateway execution state | Portkey may already cover enough control-plane scope | Research |
| n8n | Workflow execution + AI agents | Cross-retry durable consequence state | Easy for n8n to build natively | Research |
| Workato | AI control + execution plane | Ambiguous external action settlement | Highest overlap; may already solve it | Research |
| Stripe | Payment infrastructure + agentic commerce | Cross-provider consequence integrity | Native payment idempotency makes a payment-only pitch invalid | Research |

## Required proof before serious acquisition outreach

For each account, answer:

1. What exact external side-effect failure remains unsolved in its current stack?
2. Why can't native idempotency or workflow persistence solve it?
3. What does SafeAgent do that the buyer would otherwise have to build?
4. What evidence shows SafeAgent already works at that boundary?
5. What integration surface makes adoption/acquisition faster than internal development?
6. What would invalidate the acquisition thesis?

If question 6 cannot be answered, the brief is sales theater rather than diligence.
