# 2. Sandbox-first ACE catalog default

Date: 2026-06-04

## Status

Accepted

## Context

The integration writes opportunities to AWS Partner Central via the `partnercentral-selling` API. The API exposes two distinct catalogs:

- **Sandbox**: opportunities are not visible to AWS reviewers and do not count against any partner reporting.
- **AWS**: production. Opportunities are real, visible to AWS, and contribute to partner metrics.

A misconfigured deployment that writes test traffic to the AWS catalog produces real, visible damage: AWS reviewers see junk submissions, partner reporting metrics get polluted, and there is no `DeleteOpportunity` API to undo it. The deployer-controlled Terraform variable `ace_catalog` could default to either value. Three options were considered:

1. Default to `Sandbox`, require explicit opt-in to AWS production.
2. Default to `AWS`, rely on developer discipline as the safety check.
3. Have no default and force the operator to choose explicitly every time.

## Decision

`ace_catalog` defaults to `"Sandbox"`. Production deployments must explicitly set `ace_catalog = "AWS"` in `terraform.tfvars`.

In addition, when `ace_catalog` is `Sandbox`, the IAM policy on every Lambda includes a `partnercentral:Catalog` condition that restricts API actions to the Sandbox catalog. Code that accidentally passes `Catalog: "AWS"` while `ace_catalog` is Sandbox fails with `AccessDeniedException` at the IAM layer, not at the application layer. Defense in depth on top of the application-level guard.

## Consequences

Positive:

- A first-time deployer cannot accidentally write test traffic to production.
- The deliberate act of changing `ace_catalog` from `Sandbox` to `AWS` is logged in git history and surfaces in code review.
- The IAM condition catches application bugs that the application-level guard would miss.

Negative:

- Production deployments require one extra variable change after the initial Terraform apply.
- The Sandbox catalog has known idiosyncrasies (different validation rules, no `DeleteOpportunity`, `ReviewStatus` state machine differences) that the codebase has to accommodate. These are documented in `docs/testing-in-your-account.md`.

Operational:

- The criteria for flipping to `"AWS"` are enumerated in `docs/testing-in-your-account.md` (sandbox smoke matrix green, an Approved Solution registered in the production catalog, MFA enabled on the deployer role, SNS notifications wired). Operators should not flip without ticking every box.
- Option 3 (no default) was rejected as it would block every first `terraform plan` and the friction would push operators toward inlining `Sandbox` anyway. The explicit-default approach captures the same intent without the friction.
