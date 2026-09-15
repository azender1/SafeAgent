# REPORT: CrewAI retry / SafeAgent A/B, v3

## What this proves

Across 180 real trials (30 per mode x case cell, 2 modes x 3 cases), with
CrewAI 1.15.21 and SafeAgent 0.1.23 genuinely installed and genuinely
executed -- no mocking of either library -- every trial's independently
observed effect_count and guard_status matched the expected table below,
and every trial additionally passed the stronger structural checks
described under "What v3 additionally enforces."

| Mode | Case | effect_count | guard_status |
|---|---|---:|---|
| unguarded | clean | 1 | -- |
| unguarded | pre_effect | 1 | -- |
| unguarded | post_effect | 2 | -- |
| safeagent | clean | 1 | COMMITTED |
| safeagent | pre_effect | 0 | PENDING |
| safeagent | post_effect | 1 | PENDING |

**180/180 PASS. 0 FAIL. 0 VOID.**

## Isolation model

The effect being protected is recorded by the worker through an HTTP
call to a separate OS process (`ledger_server.py`), authorized by a
`record_token` that grants exactly one capability: append an entry. The
worker holds no token, endpoint, or code path capable of reading the
current count, resetting the ledger, modifying or deleting a prior
entry, sealing the ledger, or reaching the control channel at all. Those
operations require a disjoint `control_token` generated fresh per trial
by the parent and never written into the worker's command line or
environment anywhere in this codebase.

The parent's sequence per trial, exactly as required:
1. Start a fresh `ledger_server.py` subprocess; wait for its readiness
   signal.
2. Call `/control/reset` (belt-and-suspenders on an already-empty fresh
   process).
3. Launch the worker subprocess with only the record_token.
4. Block on the worker's exit (`subprocess.run`, with a timeout that
   voids the trial rather than hanging forever).
5. Only after the worker has fully exited: call `/control/seal`, then
   `/control/read`.
6. Independently open SafeAgent's own guard database and read the
   claim's final status.
7. Terminate the ledger process.

## What v3 additionally enforces, beyond effect_count and guard_status

A trial is only marked PASS if ALL of the following hold, not just the
headline effect_count/guard_status match (this is the direct response to
the v1 review finding that the original acceptance gate under-enforced
what it claimed to check):

- Exactly the expected number of tool-body entries occurred (1 for
  clean, 2 for both failure cases), with no duplicate attempt_ids.
- For failure cases, `llm_calls == 2` and `agent_retries == 0` on the
  worker's own runtime_result -- since the scripted LLM only ever
  authorizes the tool action on its first call, this is what
  distinguishes a second attempt caused by CrewAI's own internal
  `ToolUsage._use` retry from one that would have required the LLM (or
  the agent's own retry loop) to reissue the action.
- Exactly one `injected_failure` event, at the declared injection point
  (`before_effect` for pre_effect, `after_effect_before_confirmation`
  for post_effect), on the first attempt specifically.
- For post_effect specifically: the injected failure's timestamp is
  strictly after the `effect_committed` event's timestamp for the same
  attempt, and that `effect_committed` event carries a real ledger
  acknowledgment (`ledger_seq` is not null) -- i.e., the failure is
  proven, not just claimed, to have happened after the ledger accepted
  the effect.
- The ledger was sealed before the parent read it.
- The ledger's own entries have monotonically increasing sequence
  numbers with no duplicates.
- No malformed event lines, no worker timeout, no ledger or guard-DB
  read error.

Any single one of these failing marks the trial FAIL (or VOID for
infrastructure-level problems like a timeout or an unreadable ledger --
see below); a VOID or FAIL trial is never silently treated as if its
effect_count were 0.

## Fail-closed behavior, verified

`test_v3.py` includes direct tests proving the acceptance gate actually
rejects: a correct effect_count paired with a wrong guard_status; a
timed-out worker (asserts `effect_count is None`, never 0); a missing
runtime_result; an unsealed ledger read; a ledger read error; duplicate
attempt IDs in either the event stream or the ledger; a wrong injection
point; an injected failure timestamped before its own effect's
acknowledgment; and a second tool attempt whose `llm_calls` count can't
be attributed to `ToolUsage._use`'s internal retry.

## Capability isolation, verified

`test_v3.py` starts a real `ledger_server.py` process and: confirms the
record_token gets 403 on `/control/reset`, `/control/seal`, and
`/control/read`; confirms the control_token itself gets 403 on
`/record` (the separation is a disjoint pair of capabilities, not a
privilege hierarchy); confirms `/record` is rejected with 409 once
`/control/seal` has been called, using the correct record_token.

## Network isolation, verified

The worker patches `socket.socket.connect` before importing `crewai`,
allowing only the ledger's `(127.0.0.1, <port>)`. `test_v3.py` proves
both directions in isolated subprocesses: a connection attempt to a
different address is blocked with `PermissionError`; a connection
attempt to the configured ledger address reaches the real socket layer
(observed as `ConnectionRefusedError` when nothing is listening, proving
it was not intercepted). `CREWAI_DISABLE_TELEMETRY=true` and
`OTEL_SDK_DISABLED=true` are set before `crewai` is imported, verified
statically by `test_v3.py`. Across all 180 real trials, worker stderr
was empty in every case -- no telemetry warnings, no connection errors,
no unexpected output of any kind.

## What this still does not validate (unchanged claim boundary)

This validates SafeAgent 0.1.23 against CrewAI 1.15.21's same-process
synchronous `ToolUsage._use` retry, using a scripted deterministic LLM
and a harmless effect recorded through an out-of-process,
capability-separated ledger. It does not validate:

- **Process death.** The retry tested here occurs within one worker
  process's call stack (`_use` recursively calling `self.use(...)`).
  It does not test SafeAgent's claim surviving an actual crash (kill
  -9, OOM) between claim and settle, with a different, later process
  resuming the same `logical_id`.
- **Fresh-worker recovery** from that crash scenario specifically.
- **A real external provider or a real external side effect.** The
  ledger is a local process on the same machine; it is authoritative
  and capability-separated, but it is not a real payment API, webhook,
  or third-party system with its own independent network/latency
  failure modes.
- **SafeAgent Control reconciliation** (the separate reconciliation
  engine) -- this package only exercises the execution-guard layer.
- **A hostile subject** beyond the specific capability/token boundary
  described above -- e.g., a worker that has been compromised at the
  OS process level (not just "doesn't have the token") is out of scope.

## Reproduction

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python parent.py --k 30 --output evidence.json
.venv/bin/python test_v3.py
```

Full 180-trial run: ~10-13 minutes on the machine this was built on.
