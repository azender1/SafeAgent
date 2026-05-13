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



\### 4. Execution receipt

Post-execution proof anchored externally — independently verifiable without trusting the runtime that produced it.

The guard proves "this ran exactly once." The receipt proves "this is what ran" to a party that does not trust the system that ran it.

**Interface**

\- `request\_id` (Layer 1) maps to `action\_ref` in the receipt layer. Both are derived from tool arguments before execution using the same SHA-256 content-addressing:

```
action_ref = SHA-256(agent_id + ":" + action_type + ":" + scope + ":" + timestamp)
```

No coupling changes needed on either side — the key is already content-addressed and consistent across layers.

\- `payment\_hash` is the natural cross-rail key for x402-gated actions: it links the payment primitive to the execution receipt without coupling the payment and execution layers.

**Anchor requirement for regulated workflows**

For workflows subject to external audit (financial, compliance, regulated), the receipt should be anchored on an external chain — a verifier can replay from any RPC node without querying the runtime.

**What an auditor can verify from the receipt alone**

\- The action ran (trail exists, anchored on-chain)
\- The action was authorized (scope field, optionally linked via delegation\_ref)
\- The action was paid (payment\_hash cross-references the settlement layer)
\- The action ran exactly once (idempotency enforced by Layer 2; receipt is write-once)

**Reference implementation**

[Mycelium Trails](https://argentum.rgiskard.xyz/trails/demo) — on-chain execution receipts anchored on Base mainnet, keyed by `action\_ref`. Joint interface spec: giskard09/argentum-core#7.



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

\- **Layer 4 scope** — should the RFC specify a canonical anchor chain, or leave that to the implementor? (Current reference: Base mainnet)

\- **delegation\_ref format** — URL, content hash, or UUID? Currently caller-defined; Mycelium stores verbatim.



\## Current Reference Implementation



SafeAgent is a reference implementation of the Execution Guard pattern using:

\- SQLite for local development

\- Postgres for distributed / production environments

## Canonical Key Derivation (v1)

All four fields are required. No optional fields in the byte contract.

action_ref = SHA-256(agent_id || action_type || scope || timestamp_ms)

### Field definitions

| Field | Type | Description |
|-------|------|-------------|
| `agent_id` | string | Stable identifier of the executing agent. ERC-8004 compatible identifiers are preferred but not required for v1. |
| `action_type` | string | The tool or action name (e.g. `stripe:charge`, `file:write`, `email:send`) |
| `scope` | string | What the agent was authorized to do. Maps to DashClaw `authorization_scope`. |
| `timestamp_ms` | int64 | Millisecond-precision Unix timestamp at claim time, before execution. |

### Cross-system field mapping

| Joint spec field | SafeAgent | DashClaw | Mycelium Trails |
|-----------------|-----------|----------|-----------------|
| `agent_id` | claim payload | `agent_id` | anchor key input |
| `action_type` | claim payload | `action_type` | anchor key input |
| `scope` | claim payload | `authorization_scope` | anchor key input |
| `action_ref` / `request_id` | `request_id` | `idempotency_key` (caller-computed) | `action_ref` |
| outcome endpoint | `POST /settle/{id}` | `GET /api/actions/:actionId/outcome` | reads after settle |

### Byte encoding

`timestamp_ms` is encoded as int64, 8 bytes, big-endian. All string fields are UTF-8 encoded. Implementations must use this exact encoding to produce compatible hashes across systems.

```
SHA-256(
  agent_id.encode('utf-8') ||
  action_type.encode('utf-8') ||
  scope.encode('utf-8') ||
  timestamp_ms.to_bytes(8, 'big')
)
```

### Key properties

- Derived from tool arguments **before** execution — same inputs always produce the same `action_ref`
- `agent_id` is required to prevent collision in multi-agent systems where two agents make identical calls
- DashClaw consumes `action_ref` as `idempotency_key` opaquely — no runtime coupling required
- Mycelium reads `GET /api/actions/:actionId/outcome` after SafeAgent settles and anchors on-chain using `action_ref`

Joint interface spec: [giskard09/argentum-core#7](https://github.com/giskard09/argentum-core/issues/7)

