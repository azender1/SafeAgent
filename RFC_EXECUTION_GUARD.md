\# Execution Guard RFC (Draft)



\## Summary



Execution Guard is a pattern for exactly-once execution of agent side effects under retries and uncertainty.



It prevents duplicate irreversible actions such as:

\- payments

\- emails

\- trades

\- external API mutations

\- database writes



\## Problem



Agent systems retry under uncertainty.



Retries do not mean "nothing happened."

They mean "we do not know what happened."



Without an execution guard at the side-effect boundary, retries can duplicate real-world actions.



\## Core Pattern



1\. Accept a stable `request\_id` or `execution\_id`

2\. Insert execution record if not exists

3\. Execute side effect exactly once

4\. Store result

5\. On retry, resolve against the recorded execution instead of re-running



\## Three-Layer Model



\### 1. Intent nonce

Caller-derived identifier for simple retries.



\### 2. Execution guard

Insert-if-not-exists at each side-effect boundary.



\### 3. Correlation ID

Shared ID across composed tool boundaries for retry convergence without tight coupling.



\## Why This Matters



Most agent frameworks treat retry behavior as a reliability concern.



Execution Guard treats duplicate side effects as a correctness concern.



\## Storage Model



Execution records should support:

\- request\_id / execution\_id

\- action name

\- status

\- stored result

\- created\_at

\- optional TTL / cleanup sweep



\## Example Use Cases



\- payment tools

\- email tools

\- trading systems

\- background jobs

\- webhook handlers

\- multi-tool agent workflows



\## Open Questions



\- Should frameworks provide a protocol-level idempotency key?

\- How should nested tool calls inherit execution IDs?

\- What retention / TTL defaults make sense?

\- How should partial multi-step side effects be coordinated?



\## Current Reference Implementation



SafeAgent is a reference implementation of the Execution Guard pattern using:

\- SQLite for local development

\- Postgres for distributed / production environments

