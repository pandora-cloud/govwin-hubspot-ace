# 3. Direct AWS Partner Central Selling API client, not a paid third-party connector

Date: 2026-06-04

## Status

Accepted

## Context

The HubSpot to AWS Partner Central half of the pipeline requires the integration to call the `CreateOpportunity`, `AssociateOpportunity`, and `StartEngagementFromOpportunityTask` operations on the AWS Partner Central Selling API. Three implementation paths existed:

1. **Buy a paid third-party connector** (for example SaaSify or a similar middleware product) that wraps the Selling API and provides a managed integration surface. Subscription cost, typically per-seat or per-opportunity.
2. **Write a direct boto3 client** against `partnercentral-selling`, run it inside the project's own Lambdas.
3. **Use an AWS-published reference connector** if one existed. As of project start, AWS did not publish a complete reference connector for partner CRM workflows that mapped to HubSpot.

Path 1 is the fastest to a working integration; paths 2 and 3 are owned by the project.

## Decision

We own the client. `src/ace/client.py` is a thin boto3 wrapper around `partnercentral-selling`, with project-specific concerns layered on top: optimistic locking on `LastModifiedDate`, tenacity-based retries with backoff, atomic `ClientToken` reservation in DynamoDB, a token-bucket rate limiter that respects the Selling API's 1 write per second and 10 reads per second quotas, and a `Catalog` cross-check that raises `CrossCatalogResponse` on mismatch.

The integration ships with no paid runtime dependencies. The only third-party services it requires are the three the operator already pays for: Deltek GovWin IQ, HubSpot CRM, and AWS itself.

## Consequences

Positive:

- The end-to-end pipeline is fully open source. Operators can fork, audit, and redistribute without licensing concerns.
- Behavior is debuggable: every API call is in the project's own code, with logs, traces, and metrics on the project's own Lambdas. There is no opaque connector layer between us and the AWS API.
- The cost model is just AWS infrastructure cost. No per-opportunity or per-seat connector pricing.
- Federal compliance posture is simpler: no third-party data processor in the path; data flows partner → AWS only.

Negative:

- The project carries the maintenance burden of any AWS Partner Central API change. New fields, deprecations, schema shifts, and behavioral surprises (the Sandbox idiosyncrasies documented in `docs/testing-in-your-account.md`) all land in our codebase first.
- Idempotency, retries, rate limiting, and the `ClientToken` reservation pattern had to be engineered rather than inherited from a managed product. See ADR 0006 for the atomic-token approach.

Operational:

- The pinned region `us-east-1` for the `partnercentral-selling` boto3 client is an AWS-side constraint, not a project choice. Reflected in `src/aws_clients.py` and ADR 0002's IAM policy.
- Reference docs from AWS are mirrored into `docs/reference/aws-partner-central/` so that contributors do not have to keep open the AWS console to understand the field surface.
