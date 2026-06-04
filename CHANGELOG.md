# Changelog

All notable changes to this project are documented in this file. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Apache License 2.0 (replacing MIT) with explicit patent grant.
- Centralized boto3 client construction (`src/aws_clients.py`) with FIPS endpoint enforcement and `us-east-1` pinning for the `partnercentral-selling` API.
- FIPS endpoint policy: `AWS_USE_FIPS_ENDPOINT=true` on every Lambda and `use_fips_endpoint = true` on the Terraform AWS provider. Local tests opt out via the same env var since moto and LocalStack do not implement FIPS-suffixed hostnames.
- Customer-managed KMS key (`module.kms`): SNS topic, both DynamoDB tables, and every SQS queue (operational and DLQs) encrypt at rest under one auditable key.
- UI Extension split into separate read and write Lambdas. The reads role is minimal (`ListSolutions` plus signing-secret read); the writes role excludes `CreateOpportunity` and `StartEngagementFromOpportunityTask` so those calls only run from the trusted shared role behind the SQS pipeline.
- Audit handling for `govwin_aws_cosell_id`: hand-edits from any source other than the integration token fire a real-time SNS alert.
- `update_in_ace` self-heal verify: refuses to write to AWS when the deal's recovered ACE id resolves to a foreign `PartnerOpportunityIdentifier`, and publishes a mismatch alert.
- `update_in_ace` Closed-Lost race fix: when a `LifeCycle.Stage = Closed Lost` change arrives without its companion `ClosedLostReason` (or vice versa), the Lambda reads the missing companion from the HubSpot deal so AWS gets both fields in one `UpdateOpportunity`.
- `update_in_ace` dispatch table for property delta handlers: `govwin_ace_lifecycle_stage`, `govwin_ace_closed_lost_reason`, `govwin_ace_solution_id`, `govwin_ace_partner_need`, `govwin_ace_delivery_model`, `govwin_ace_sales_activities`, `govwin_ace_national_security`, `govwin_ace_opportunity_type`, `govwin_industry`, and the AWS Products diff handler.
- `/update` UI Extension endpoint: synchronous `GetOpportunity` + `UpdateOpportunity` + Associate/Disassociate from the Submit-to-AWS card. Replay protection distinguishes `status=replay_detected` from `status=already_submitted`.
- CORS allowlist for the OPTIONS preflight reflection (`https://app.hubspot.com`, regional `app-naN`/`app-euN`/`app-jpN`/`app-apN`, sandbox variants). Unrecognized origins fall back to the NA1 default rather than echoing the request value.
- CloudWatch alarm `<prefix>-update-in-ace-high-rate` for sustained fan-out detection. Threshold via `var.update_in_ace_fanout_threshold` (default 30/min averaged over 15 min; set to 0 to disable). New "Scaling and webhook fan-out" runbook in `docs/operations.md`.
- `docs/pre-install-checklist.md` and `docs/cost-model.md`.
- `scripts/reconcile.py` and `make reconcile GOVWIN_ID=<id>`: read-only 4-way state dump (DDB, HubSpot deal, AWS `GetOpportunity`, AWS `ListOpportunities` collision check) for triaging self-heal mismatch alerts.
- `make dlq-status` and `make dlq-redrive QUEUE=<name>`: depth across every project DLQ plus an SQS message-move-task wrapper.
- `ACEClient.get_opportunity` asserts the response `Catalog` matches the configured catalog and raises `CrossCatalogResponse` on mismatch; defense in depth on top of the IAM `Catalog` condition.
- `HUBSPOT_INTEGRATION_APP_ID` env var (set from `var.hubspot_webhook_app_id`). The audit-alert handler cross-checks `ev.sourceId` against this value; INTEGRATION-source events from a different app installed on the same HubSpot portal fire a distinct "foreign HubSpot integration" alert.
- DynamoDB `LeadingKeys` IAM condition on the `ui_extension_reads` and `hubspot_webhook_receiver` `PutItem` grants, restricting both roles to the `WHK#` prefix.

### Changed
- HubSpot subscription registration is manifest-driven (`hubspot-app/src/app/webhooks/webhooks-hsmeta.json` deployed by `hs project upload`); the legacy `setup_hubspot_webhooks` Lambda is retired.
- `partnercentral-selling` boto3 client is hard-pinned to `us-east-1` regardless of the operator-configured `aws_region`. This reflects an AWS-side endpoint constraint; non-`us-east-1` deployments previously failed silently.
- HubSpot 4xx error bodies and SNS alert bodies pass through a redactor that strips `propertyValue` and `localizedErrorMessage` before logging or publishing, and fully redacts `CompanyName`, `Email`, `Phone`, `WebsiteUrl`, and `Reason`. `message` / `Message` / `ErrorMessage` are trimmed to 200 characters so operators retain diagnostic context.
- AWS error strings written back to HubSpot deal properties pass through the same redactor; HubSpot deal properties are visible to anyone with deal-read.
- Webhook signature replay reservation TTL halved from `2 * webhook_max_age_seconds` to match the freshness window; replays past the window already fail the timestamp check, so the doubled TTL provided no marginal protection.
- Webhook signature 4xx responses gate detailed mismatch context behind `LOG_LEVEL=DEBUG` so a CloudWatch ingest leak does not give an attacker a precise oracle.
- `_trigger_stage_id` in `ui_extension_writes.py` raises on missing `ACE_TRIGGER_STAGES` instead of falling back to a hardcoded sandbox stage id.
- LocalStack pinned to `localstack/localstack:3.8` (community edition).
- README configuration table: added `ace_default_solution_id`, `ace_trigger_stages`, `hubspot_webhook_app_id`, `hubspot_webhook_client_secret`. Default `sync_schedule` corrected to `rate(1 hour)`.
- `hubspot-app/src/app/webhooks/webhooks-hsmeta.json`: every subscription ships `"active": false` so the first `hs project upload` does not flood a placeholder URL.

### Fixed
- `submit_to_ace` consolidates the dual writeback path into a single outer handler that records the actual AWS error string (trimmed to 480 chars) on permanent failure.
- `handle_ace_event` writes `govwin_ace_lifecycle_stage` only when the value actually changes, preventing the AWS-event -> HubSpot-PATCH -> AWS-event feedback loop.
- `handle_ace_event` writes `govwin_aws_cosell_products` (AWS-side mirror) so the Submit-to-AWS card can render a "Products syncing..." pill without burning a Partner Central read quota call.
- `_handle_aws_products_diff` collects non-Conflict failures into a `failures` list and surfaces them so partial product-association failures are visible rather than silent.
- Multi-value `_apply_delta` handlers (`govwin_ace_partner_need`, `govwin_ace_delivery_model`, `govwin_ace_sales_activities`, `govwin_ace_national_security`, `govwin_ace_opportunity_type`) return `False` on empty, garbage, or invalid-enum values so the DDB mapping is not marked "updated" on a no-op.
- `govwin_industry` handler clears `OtherIndustry` when the new industry maps to a closed-enum value.

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

[Unreleased]: https://github.com/pandora-cloud/govwin-hubspot-ace/compare/v2.1.0...HEAD
[v2.1.0]: https://github.com/pandora-cloud/govwin-hubspot-ace/compare/v2.0.0...v2.1.0
[v2.0.0]: https://github.com/pandora-cloud/govwin-hubspot-ace/compare/v1.0.0...v2.0.0
[v1.0.0]: https://github.com/pandora-cloud/govwin-hubspot-ace/releases/tag/v1.0.0
