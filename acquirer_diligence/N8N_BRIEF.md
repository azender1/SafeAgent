# n8n Strategic Brief

Status: RESEARCHED — NOT CONTACTED

## Why this account

n8n is increasingly focused on enterprise AI-agent governance and production controls. It already has a direct integration surface with SafeAgent through the published n8n community node.

Sources:
https://blog.n8n.io/ai-agent-governance/
https://n8n.io/partners/
https://support.n8n.io/article/im-looking-for-a-partnership

## SafeAgent thesis

n8n orchestrates workflows and controls agent access. SafeAgent specializes in a narrower state boundary around irreversible effects:

- claim-before-execute;
- durable PENDING/SETTLED state;
- replay/duplicate blocking;
- capability-bound settlement;
- reconciliation when the external outcome is uncertain.

The existing node lowers integration friction, but it is not evidence that n8n wants to own or buy SafeAgent.

## Build-vs-buy question to test

Would n8n rather implement exactly-once/uncertain-outcome primitives directly in core workflow execution?

If yes, SafeAgent's acquisition case must be speed, hardened evidence, and cross-platform interoperability rather than feature novelty.

## Professional route

Primary:
- product leadership for Agents / Enterprise Governance
- tech/product partnerships
- corporate/strategic leadership

Official routes:
https://n8n.io/contact/
https://n8n.io/partners/

Important:
Do not pitch through n8n community/forum threads. Those are evidence and support channels, not the acquisition route.

## Evaluation proposition

Use an existing n8n workflow with a real external side-effect node. Force a timeout after the provider completes the action and verify whether SafeAgent prevents the workflow from duplicating the action on retry.

## Current signal

Existing package distribution/integration only. No acquisition interest.
