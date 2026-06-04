# Changelog

All notable changes to this project are documented in this file. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Future entries are generated automatically by [release-please](https://github.com/googleapis/release-please) from conventional commit messages on `main`.

## [Unreleased]

### Added
- Apache License, Version 2.0 (replaces MIT) with explicit patent grant.
- `src/aws_clients.py`: centralized boto3 client construction with FIPS endpoint enforcement and us-east-1 pinning for `partnercentral-selling`. All call sites refactored.
- `scripts/verify_fips.py`: CI-runnable check that every AWS service we use resolves to a FIPS endpoint.
- FIPS endpoints active in production: `AWS_USE_FIPS_ENDPOINT=true` on every Lambda, `use_fips_endpoint = true` on the Terraform AWS provider. Tests and LocalStack opt out via the same env var because moto / LocalStack do not implement FIPS-suffixed hostnames.
- OSS scaffolding: GitHub issue forms (bug, feature) + config.yml routing security/help/paid-support, MAINTAINERS.md, SUPPORT.md, CODEOWNERS, FUNDING.yml, SECURITY-INSIGHTS.yml, .editorconfig, .pre-commit-config.yaml, renovate.json, .github/dependabot.yml.
- Supply-chain workflows: CodeQL (security-extended + security-and-quality), OpenSSF Scorecard (badge gated until first-run review), CycloneDX SBOM on release, SLSA provenance on release.
- `scripts/generate_security_pgp.sh` + `.well-known/security/` for encrypted vulnerability disclosure.
- ACE-specific troubleshooting table in the deployment guide (signature mismatch, ConflictException, ValidationException, ResourceNotFoundException, ThrottlingException).
- Step 9b.i in the deployment guide: how to retrieve numeric HubSpot pipeline-stage IDs for `ace_trigger_stages`.
- `src/alerts.py`: shared SNS publish helper with HubSpot 4xx body redaction (strips `propertyValue` / `localizedErrorMessage` before sending).
- `module.kms`: cross-cutting customer-managed CMK. SNS topic, both DynamoDB tables, and every SQS queue (operational + DLQs) now encrypt at rest under one auditable keyId.
- `ui_extension_reads` + `ui_extension_writes` Lambdas (replacing the prior monolithic `submit_form_to_ace`). Reads role is minimal (`ListSolutions` + signing-secret read only); writes role excludes `CreateOpportunity` and `StartEngagementFromOpportunityTask` (those run only from the trusted shared role behind the SQS pipeline). Apply runbook in `docs/operations/ui-extension-split-runbook.md`.
- `AUDIT_ONLY_PROPERTIES` set in `src/lambdas/_webhook_routing.py` + audit-event handling in `hubspot_webhook_receiver`. Hand-edits of `govwin_aws_cosell_id` from non-integration sources fire a real-time SNS alert.
- `update_in_ace` self-heal verify: refuses to write to AWS when the deal's recovered ACE id resolves to a foreign `PartnerOpportunityIdentifier`, publishes a mismatch alert.
- `update_in_ace` Closed-Lost race fix: when a `LifeCycle.Stage = Closed Lost` change arrives without its companion `ClosedLostReason` (or vice versa), the Lambda reads the missing companion from the HubSpot deal so AWS gets both fields in one `UpdateOpportunity`.
- `update_in_ace._apply_delta` refactored into a dispatch table; new handlers for `govwin_ace_lifecycle_stage`, `govwin_ace_closed_lost_reason`, `govwin_ace_solution_id`, `govwin_ace_partner_need`, `govwin_ace_delivery_model`, `govwin_ace_sales_activities`, `govwin_ace_national_security`, `govwin_ace_opportunity_type`, `govwin_industry`, plus the AWS Products diff handler.
- `submit_form_to_ace` (now `ui_extension_writes`) `/update` endpoint: synchronous `GetOpportunity` + `UpdateOpportunity` + Associate/Disassociate from the Submit-to-AWS card. Replay protection now distinguishes `status=replay_detected` from `status=already_submitted`.
- CORS allowlist for the OPTIONS preflight reflection (`https://app.hubspot.com`, `app-na2`, `app-eu1`/`eu2`, `app-jp1`, `app-ap1`, sandbox variants). Unrecognized Origins fall back to the NA1 default rather than echoing the request value.
- CloudWatch alarm `<prefix>-update-in-ace-high-rate` for sustained fan-out detection. Threshold via `var.update_in_ace_fanout_threshold` (default 30/min averaged over 15 min, set to 0 to disable). New "Scaling and webhook fan-out" runbook in `docs/operations.md`.
- `docs/pre-install-checklist.md`: action-oriented "Before you install" page with the items you need ready, the three `terraform.tfvars` values that actually matter, what cannot be changed later, and the cost table.
- `docs/cost-model.md`: per-component cost breakdown across small / medium / large deployment sizes plus the cost monitoring runbook.
- `scripts/reconcile.py` + `make reconcile GOVWIN_ID=<id>`: 4-way state dump (DDB / HubSpot deal / AWS GetOpportunity / AWS ListOpportunities collision check) for triaging self-heal mismatch alerts. Read-only.
- `make dlq-status` + `make dlq-redrive QUEUE=<name>`: depth across every project DLQ and SQS message-move-task wrapper.
- `ACEClient.get_opportunity` now asserts the response `Catalog` matches the configured catalog and raises `CrossCatalogResponse` on mismatch. Defense-in-depth on top of the IAM Catalog condition.
- `HUBSPOT_INTEGRATION_APP_ID` env var (set from `var.hubspot_webhook_app_id`). The audit-alert handler cross-checks `ev.sourceId` against this value; INTEGRATION-source events from a different app installed on the same HubSpot portal fire a distinct "foreign HubSpot integration" alert.
- DDB `LeadingKeys` IAM condition on the `ui_extension_reads` and `hubspot_webhook_receiver` PutItem grants, restricting both roles to the `WHK#` prefix.
- `MRR_MONTHS_PER_YEAR` constant in `src/ace/mapper.py` replaces the magic `/12.0` in three call sites.
- `_validate_shared_form` extracted in `ui_extension_writes.py`; the prior ~110 lines of duplication between `_validate_enums` and `_validate_update_enums` collapsed.

### Changed
- HubSpot subscription registration is now manifest-driven (`hubspot-app/src/app/webhooks/webhooks-hsmeta.json` deployed by `hs project upload`). The `setup_hubspot_webhooks` Lambda (legacy private-app REST endpoint) is retired.
- Disaster-recovery runbook step 5 in `docs/operations.md` replaced "re-run `setup_hubspot_webhooks`" with "`hs project upload`".
- README + deployment-guide step 9d: simplified to one webhook activation path (manifest + `hs project upload`); the prior "Option A / Option B" choice between the legacy Lambda and the manifest is gone.
- `monitoring` module's `monitored_lambda_names` list now includes `ui-ext-reads` and `ui-ext-writes`; the legacy `setup-hubspot-webhooks` entry is removed.
- Bootstrap deployer role gains `kms:CreateGrant` / `RetireGrant` / `ListGrants` (tag-scoped to `Application = <project>-<environment>`) so the CMK migration applies cleanly without manual deployer-policy work.
- Webhook signature replay reservation TTL halved from `2 * webhook_max_age_seconds` to match the freshness window; replays past the window already fail the timestamp check, so the doubled TTL provided no marginal protection.
- `src/hubspot/client.py:_redact_hubspot_error_body` expanded the full-redact set to `CompanyName`, `Email`, `Phone`, `WebsiteUrl`, `Reason` (in addition to `propertyValue` and `localizedErrorMessage`); `message` / `Message` / `ErrorMessage` are trimmed to 200 chars so operators retain diagnostic context.
- `src/lambdas/submit_to_ace.py` and `update_in_ace.py` now pass the AWS error string through the redactor before writing it back to the HubSpot deal property; HubSpot deal properties are visible to anyone with deal-read.
- Multi-value `_apply_delta` handlers (`partner_need`, `delivery_model`, `sales_activities`) log a WARNING on empty input so the no-op (AWS does not accept empty arrays via this path) is visible in CloudWatch.
- `src/ace/mapper.py:_normalize_industry` renamed to `normalize_industry` (cross-module private import was a smell).
- `src/sync/state.py:mark_event_seen` (non-atomic, legacy) deleted; only `mark_event_seen_atomic` remains.
- `_trigger_stage_id` in `ui_extension_writes.py` raises on missing `ACE_TRIGGER_STAGES` instead of falling back to a hardcoded sandbox stage id.

### Changed
- LocalStack pinned to `localstack/localstack:3.8` (community edition). The `:latest` tag began requiring a paid auth token in mid-2026.
- `make local-test` now runs only `tests/integration/`; the unit tests use moto and should not be run against LocalStack.
- `src/ace/client.py` partnercentral-selling client is now hard-pinned to us-east-1, regardless of the operator-configured `aws_region`. This is an AWS-side endpoint constraint; non-us-east-1 deployments previously failed silently.
- Documentation merged: `docs/testing.md` consolidated into `docs/testing-in-your-account.md`. The merged doc is the single canonical place for the test pyramid + smoke matrices + production rollout reference.
- README + deployment guide: stale Step Functions references replaced with `aws lambda invoke` of the orchestrator (the v2.1 architecture used Lambda + SQS, not Step Functions).
- README + deployment guide Terraform-version requirement aligned at `>= 1.11`.
- README configuration table: added `ace_default_solution_id`, `ace_trigger_stages`, `hubspot_webhook_app_id`, `hubspot_webhook_client_secret`. Default `sync_schedule` corrected to `rate(1 hour)`.
- `hubspot-app/src/app/webhooks/webhooks-hsmeta.json`: every subscription reset to `"active": false` so first `hs project upload` does not flood a placeholder URL.
- Repository scrubbed of maintainer-specific identifiers in places where the OSS audience would otherwise inherit them: AWS account IDs, real Solution IDs, real API gateway hostnames, contact emails, and "Pandora-only" prose all replaced with placeholders, generic phrasing, or parameterized config.

### Fixed
- `src/ace/client.py` previously used `config.aws.region` for the partnercentral-selling boto3 client, which would 404 on any deployment configured to a region other than us-east-1. Now hard-coded to `us-east-1` via `make_client`.
- `tests/integration/test_localstack_state.py` constructed `AppConfig` without the required `ace=` argument; pre-existing bug, surfaced when LocalStack was finally runnable.
- `submit_to_ace` consolidates the dual writeback path into a single outer handler that records the actual AWS error string (trimmed to 480 chars) on permanent failure, rather than the prior "see pc@..." placeholder.
- `handle_ace_event` writes `govwin_ace_lifecycle_stage` only when the value actually changes, preventing the AWS-event -> HubSpot-PATCH -> AWS-event feedback loop.
- `handle_ace_event` writes `govwin_aws_cosell_products` (AWS-side mirror) so the Submit-to-AWS card can render a "Products syncing..." pill without burning a Partner Central read quota call.
- `_handle_aws_products_diff` collects non-Conflict failures into a `failures` list and surfaces them so partial-product-association failures are visible rather than silent.
- Multi-value `_apply_delta` handlers (`govwin_ace_partner_need`, `govwin_ace_delivery_model`, `govwin_ace_sales_activities`, `govwin_ace_national_security`, `govwin_ace_opportunity_type`) now return `False` on empty / garbage / invalid-enum values so the DDB mapping is not marked "updated" on a no-op.
- `govwin_industry` handler now routes through `mapper._normalize_industry` so `OtherIndustry` is cleared when the new industry maps to a closed-enum value.
- Webhook signature 4xx responses gate detailed mismatch context behind `LOG_LEVEL=DEBUG` so a CloudWatch ingest leak does not give an attacker a precise oracle.
- HubSpot 4xx error bodies and SNS alert bodies pass through `_redact_hubspot_error_body` before logging / publishing, stripping `propertyValue` and `localizedErrorMessage` echoes.
- `scripts/verify_fips.py` honors the `_NO_FIPS_ENDPOINT` exception set in `src/aws_clients.py` (currently `partnercentral-selling`, which has no FIPS variant published) and reports SKIP rather than FAIL.

## [v2.1.0] - 2026-04-30

### Added
- X-Ray Active tracing on every Lambda for end-to-end observability.
- CloudWatch alarms on DLQ depth, orchestrator/worker error counts, ACE submission failure counts.
- IAM bootstrap module (`terraform/bootstrap/`) with MFA-gated deployer role and a separate one-time bootstrap-operator policy.
- Sandbox MFA escape hatch (`require_mfa_to_assume_deployer = false` + `acknowledge_no_mfa_for_sandbox_only = true`) with a mandatory expiry date.

### Changed
- **Architecture**: replaced the v2.0 Step Functions chain with EventBridge Scheduler + SQS fan-out + reserved-concurrency-governed Lambdas. Removes the 256KB inter-state payload limit and lets each opportunity batch retry independently.
- ACE submission path now atomically reserves ClientTokens in DynamoDB via conditional writes; concurrent SQS retries cannot mint duplicate ACE opportunities.

## [v2.0.0] - 2026-04-28

### Added
- HubSpot to AWS Partner Central submission half: `submit_to_ace`, `update_in_ace`, `handle_ace_event`, `hubspot_webhook_receiver`, `setup_hubspot_webhooks` Lambdas.
- AWS Partner Central Selling API direct integration (`src/ace/`) replacing the prior dependency on a paid third-party connector.
- HubSpot developer-platform 2025.2+ webhook app (`hubspot-app/`).
- Three-call submission flow: `CreateOpportunity` -> `AssociateOpportunity` -> `StartEngagementFromOpportunityTask`.
- Optimistic locking on `UpdateOpportunity` via `LastModifiedDate` with `ConflictException` retry.
- EventBridge subscription on `aws.partnercentral-selling` to mirror AWS-side state changes back to HubSpot.
- 11-scenario sandbox smoke matrix and `scripts/sandbox_smoke.py` automation for scenarios 1-10.
- HubSpot `govwin_ace_*` BD-editable property surface for the three ACE-required fields and supporting marketing/use-case context.

### Changed
- ACE catalog defaults to `Sandbox`. Production deployments must explicitly set `ace_catalog = "AWS"`. IAM policy adds a `partnercentral:Catalog: Sandbox` condition when in Sandbox mode.

## [v1.0.0] - 2026-04-08

### Added
- GovWin to HubSpot sync: hourly Step Function (in v1; superseded by v2.1's EventBridge Scheduler + Lambda + SQS).
- GovWin WSAPI V3 client with OAuth2, rate limiting (4,000/hr), and discovery modes (marked / saved-search / bookmarked / date-range).
- HubSpot CRM v3 API client with batch upsert, custom properties (`govwin_*`), pipeline mapping, and contact/company associations.
- DynamoDB-backed sync state (cursors, opportunity update dates, entity mappings).
- 130 unit tests including production-data quirk regression tests.
- Pre-deployment validation script (`scripts/validate.py`).
- Dry-run script (`scripts/dry_run.py`).
- LocalStack integration test suite.

[Unreleased]: https://github.com/pandora-cloud/govwin-hubspot-integration/compare/v2.1.0...HEAD
[v2.1.0]: https://github.com/pandora-cloud/govwin-hubspot-integration/compare/v2.0.0...v2.1.0
[v2.0.0]: https://github.com/pandora-cloud/govwin-hubspot-integration/compare/v1.0.0...v2.0.0
[v1.0.0]: https://github.com/pandora-cloud/govwin-hubspot-integration/releases/tag/v1.0.0
