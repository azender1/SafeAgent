# Notification Duplicate Send

## Scenario

An agent sends a confirmation email or push notification.
The mail or messaging provider times out before acknowledgment returns.
The system retries.

## Failure

The same user-facing notification is sent twice.

**Effect:**

- duplicate confirmation
- user confusion
- trust degradation
- support noise

## Without SafeAgent

```text
[14:32:10] SEND confirmation email to user@example.com
[14:32:11] ERROR: timeout waiting for provider response
[14:32:11] Retrying request...
[14:32:12] SEND confirmation email to user@example.com   <-- DUPLICATE
```

## With SafeAgent

```text
[14:32:10] SEND confirmation email to user@example.com
[14:32:11] ERROR: timeout waiting for provider response
[14:32:11] Retrying request...
[14:32:11] SafeAgent: request_id already exists
[14:32:11] SafeAgent: returning cached result
```

## Why it matters

The consequence is smaller than payments or trading, but the pattern is identical:
uncertain completion plus retry equals duplicate external behavior.
