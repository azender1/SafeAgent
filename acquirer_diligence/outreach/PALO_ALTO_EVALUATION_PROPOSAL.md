# Palo Alto Networks — Strategic Evaluation Proposal

Status: DRAFT — NOT SENT

## Route

Primary:
- Prisma AIRS / AI Gateway product leadership
- AI Security strategy
- Corporate Development

Secondary:
- Technology Partner Program:
  https://technologypartners.paloaltonetworks.com/English/Onboarding/Account

## Subject

Prisma AIRS evaluation: consequence integrity after an authorized agent action

## Draft

Prisma AIRS and the Portkey AI Gateway provide governance, authentication, authorization, routing, and runtime control for autonomous agents. SafeAgent focuses on a different post-authorization question:

An agent action is authorized and dispatched. The external provider completes the action, but the acknowledgement is lost. Does the control plane know whether a second execution is safe?

SafeAgent adds durable action claims, one-use settlement capability, duplicate/replay suppression, explicit unresolved state, durable receipts, and external reconciliation.

The strategic question is whether this is already solved inside Prisma AIRS / Portkey or whether consequence integrity is a distinct execution-control layer worth evaluating.

We propose one bounded test:
- authorized agent action;
- successful external effect;
- lost acknowledgement;
- retry/replay;
- pass if a second consequence is prevented or held unresolved until provider evidence resolves the state.

SafeAgent is public, deployed, package-integrated, and backed by Control v15 reconciliation evidence. Independent EC-009 review work is also available with final bounded wording pending.

Please route this to the Prisma AIRS / AI Gateway product owner responsible for runtime execution controls if appropriate.

## Success criterion

A Palo Alto Networks owner confirms the boundary is distinct enough to evaluate.
