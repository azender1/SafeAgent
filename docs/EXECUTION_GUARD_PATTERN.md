# The Execution Guard Pattern



## Summary



The Execution Guard Pattern protects irreversible side effects from being executed more than once under retries, timeouts, crashes, or uncertain completion.



It exists for one specific problem:



- the system does not know whether the side effect already happened



That uncertainty is where duplicate payments, emails, trades, and external mutations come from.



---



## The failure mode



Retries are usually treated as a reliability mechanism.



But once a system produces side effects, retries also become a correctness problem.



Examples:



\- a payment request times out after the payment may already have been sent

\- an email send retries after the provider accepted the first request

\- a trade bot retries a close action after uncertain fill state

\- an agent tool call restarts and repeats the same external mutation



The dangerous assumption is:



- no response means nothing happened



That assumption is often false.



---



## Core idea



Instead of asking only:



- should this retry?



also ask:



- has this execution already happened?



The Execution Guard Pattern adds a durable execution record at the side-effect boundary.



That record allows retries to resolve safely against prior execution instead of blindly re-running.



---



## Minimal flow



1\. Receive or derive a stable execution ID

2\. Attempt to insert an execution record

3\. If record is new, execute the side effect once

4\. Store the result / receipt

5\. On retry, resolve against the existing record instead of re-executing



---



## Why this matters



Execution Guard is useful when:



\- the action is unsafe to run twice

\- completion can be uncertain

\- retries are expected

\- external state can diverge from local state



This is common in:



\- payments

\- emails

\- trades

\- webhook handlers

\- external API mutations

\- agent tool calls

\- business-critical database writes



---



## What it is not



Execution Guard is not:



\- a workflow engine

\- a scheduler

\- a queue

\- a retry library

\- a replacement for orchestration



It is a narrow correctness layer for irreversible side effects.



---



## Relationship to idempotency



Execution Guard is adjacent to idempotency, but more operational.



Idempotency is often treated as a property of a request or endpoint.



Execution Guard is the reusable execution-side pattern that protects the side effect itself under uncertainty.



---



## Short version



Execution Guard makes retries resolve safely instead of running the same irreversible action twice.



---



## Reference implementation



SafeAgent is a reference implementation of the Execution Guard Pattern.



Repo:

`github.com/azender1/SafeAgent`

