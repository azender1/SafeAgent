# Execution Guard Architecture

```
Agent / App
    ↓
Execution Guard
    ↓
Execution Store (SQLite / Postgres)
    ↓
Side Effect (payment / email / trade / API write)

If retried:
    ↓
Execution Guard checks durable record
    ↓
Existing execution found
    ↓
Resolve instead of re-run
```

## Core idea

Execution Guard sits between decision logic and irreversible side effects.

Its job is simple:

- record the execution attempt
- execute once
- resolve retries safely

This makes retries converge into one durable outcome instead of duplicating the side effect.
