# n8n-nodes-safeagent 0.2.6

Security compatibility release for the hardened SafeAgent hosted API.

## Changes

- Settle now requires the `settlement_token` returned by Claim.
- The node sends the token as `x-safeagent-settlement-token`.
- Claim output documentation now includes the settlement capability.
- README explains how to preserve the token across side-effect nodes.
- TypeScript build is verified in GitHub Actions.

This release is required for compatibility with the production API hardening merged in SafeAgent PR #19. Older published node versions that call `/settle` without the token receive 403 from hardened production.
