# Dependency and License Inventory

Status: repo-derived first pass for acquisition diligence. This is not a legal opinion.

## SafeAgent repository license

The repository root `LICENSE` is Apache License 2.0.

## Python package

Package: `safeagent-exec-guard`  
Version: `0.1.25`

Direct runtime dependencies declared in `pyproject.toml`:

- `psycopg[binary]>=3.1`
- `mcp>=1.0`
- `httpx>=0.27`
- `cryptography>=42.0`
- `rfc8785>=0.1.4`

Optional Stripe dependency:

- `stripe>=10.0`

Optional payment dependencies:

- `x402[fastapi,httpx,evm]>=2.0`
- `fastapi>=0.115`
- `uvicorn>=0.30`
- `eth-account>=0.12`
- `jsonschema>=4.0`
- `opentimestamps-client>=0.7`

Test dependencies:

- `pytest>=8.0`
- `httpx>=0.27`
- `rfc8785>=0.1.4`
- `stripe>=10.0`

## n8n package

Package: `n8n-nodes-safeagent`  
Version: `0.2.6`  
Declared package license: MIT.

Peer dependency:
- `n8n-workflow`

Development dependencies:
- `@n8n/node-cli`
- `eslint`
- `@types/node`
- `n8n-workflow`
- `typescript`

## Diligence work still required

Before any transaction process, legal diligence should verify:
1. exact licenses and versions for all transitive dependencies;
2. whether any copyleft or source-availability obligations apply;
3. notices/attribution requirements;
4. compatibility between the repository Apache-2.0 license and separately packaged MIT n8n node;
5. provenance of copied/generated fixtures and third-party code;
6. whether any contributor-specific assignment or CLA issue exists.

No claim is made here that the dependency tree is transaction-ready until that review is complete.
