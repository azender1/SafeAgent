\# Execution Guard Positioning



\## What it is



Execution Guard is a lightweight execution correctness layer for irreversible side effects.



It helps systems safely handle retries, timeouts, and uncertain completion without running the same side effect twice.



\## What problem it solves



Some failures are not reasoning failures.



They are execution failures.



Examples:



\- duplicate payments

\- duplicate emails

\- duplicate trades

\- repeated external API mutations

\- side effects retried after timeout or uncertain completion



Execution Guard exists to make retries converge safely.



\## What it is not



Execution Guard is \*\*not\*\*:



\- a workflow engine

\- a queue

\- a scheduler

\- an orchestration platform

\- a reasoning framework

\- an agent runtime

\- a replacement for retries



\## How it is different from idempotency



Idempotency is usually treated as a property of an endpoint or request.



Execution Guard is a reusable pattern for protecting irreversible side effects at the execution boundary.



In practice, it gives systems a durable execution record they can resolve against instead of re-running blindly.



\## Mental model



Do not ask only:



> should this retry?



Also ask:



> has this execution already happened?



\## Best fit



Execution Guard is strongest when:



\- a side effect is unsafe to run twice

\- completion can be uncertain

\- retries are likely

\- external state can drift from local state



\## Short version



Execution Guard helps retries resolve safely instead of re-running the same irreversible action twice.

