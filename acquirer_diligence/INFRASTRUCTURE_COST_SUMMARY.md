# Infrastructure and Cost Summary

Status: architecture verified; exact monthly cost data not yet captured.

## Current hosted footprint

Production components currently include:
- Railway production service for SafeAgent;
- Railway Postgres;
- Vercel dashboard frontend;
- GitHub Actions CI/release automation;
- PyPI distribution;
- npm distribution;
- MCP Registry distribution.

## Current verified operational posture

The hardened hosted service has been live-verified with:
- health endpoint reachable;
- unauthenticated audit access rejected;
- stranger settlement rejected;
- authorized settlement accepted;
- duplicate retry returning SKIP.

## Cost diligence gap

No exact monthly infrastructure cost figure is recorded in this diligence room yet.

Before serious acquirer diligence, capture:
1. Railway monthly spend by service;
2. Vercel monthly spend;
3. domain/hosting costs;
4. registry/package costs, if any;
5. external API/provider costs;
6. monitoring/observability costs;
7. one-time versus recurring expenses;
8. current revenue and gross margin, even if zero.

Until billing statements are collected, do not publish or estimate a monthly run-rate.
