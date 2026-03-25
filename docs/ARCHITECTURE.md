\# Execution Guard Architecture



```text

Agent / App

&#x20;   ↓

Execution Guard

&#x20;   ↓

Execution Store (SQLite / Postgres)

&#x20;   ↓

Side Effect (payment / email / trade / API write)



If retried:

&#x20;   ↓

Execution Guard checks durable record

&#x20;   ↓

Existing execution found

&#x20;   ↓

Resolve instead of re-run

