# Pre-install planning and decisions

Read this before touching `terraform.tfvars`. The integration is small to
deploy (one `terraform apply`) but it crosses three external systems
(Deltek GovWin IQ, HubSpot CRM, AWS Partner Central) and a handful of
choices made up-front are awkward to reverse later. Resolve them now and
the install reduces to following the [Deployment
Guide](deployment-guide.md) end-to-end.

This is the OSS planning checklist. If you are evaluating whether to
deploy at all, the [README "Who this is for"](../README.md#who-this-is-for-and-not-for)
section is the starting point.

## Stakeholder map

Who you need to engage before, during, and after deployment. None of
these have to be the same person; on a small team one person often
wears 3-4 of these hats.

| Role | Why involved | What they do |
|---|---|---|
| AWS account owner / cloud admin | Owns the AWS account that the deployment lives in. | Approves the deployment, sets the AWS budget alarm, confirms the account is in a region where Partner Central Selling is available (today: `us-east-1` only). |
| AWS Partner Central admin | Owns the partner relationship with AWS. | Confirms the partner is enrolled in Partner Central, has at least one Approved Solution in the AWS catalog, and identifies which Solution ID to set as the default. Recovers the Sandbox vs AWS catalog choice if you have to switch later. |
| HubSpot Super Admin | Owns the HubSpot portal. | Provisions the HubSpot Service Key (or Private App), confirms the BD team is on a tier that supports custom properties, creates the developer-platform project that the webhooks ride on. |
| BD lead / RevOps | Owns the deal pipeline. | Designs the pipeline stages that trigger ACE submission, designates which dealstage internal IDs map to "Submit to AWS", trains BD on the manual fields (Delivery Model, Solution, Partner Primary Need from AWS). |
| GovWin admin | Owns the GovWin IQ subscription. | Provisions WSAPI V3 access, creates a dedicated API user (recommended) rather than using a person's credentials, confirms the API user has read access to the opportunity types you want to sync. |
| Security / compliance owner | Final say on the IAM scope, secrets handling, and audit trail. | Reviews [SECURITY.md](../SECURITY.md), signs off on the catalog gate (`partnercentral:Catalog` IAM condition), confirms the CMK rotation policy meets internal requirements. |
| On-call / operations | Receives the alarms after go-live. | Subscribes to the SNS notifications topic, owns the runbook in [docs/operations.md](operations.md), responds to the fan-out alarm if BD load grows beyond the default threshold. |

## Decisions to make BEFORE `terraform apply`

Each decision below is a variable in `terraform/terraform.tfvars` or a
choice made in HubSpot / AWS Partner Central. Awkward to reverse later
means either a destructive `terraform apply` (resources get destroyed
and recreated, losing in-flight state) or manual AWS console work to
clean up.

### 1. AWS catalog: Sandbox or AWS

- **Variable**: `ace_catalog` in `terraform.tfvars`. Defaults to `"Sandbox"`.
- **Sandbox**: opportunities you create are not visible to AWS reviewers, do not count against partner reporting, and the IAM role pins to `Catalog: Sandbox` (cannot accidentally write to production). LifeCycle stage transitions are blocked while ReviewStatus is `Pending Submission` — a deliberate engineering-only safeguard.
- **AWS**: production catalog. Real co-sell opportunities visible to AWS reviewers, count toward partner standing and MDF credits. `PartnerOpportunityIdentifier` is permanent per-catalog; an opportunity you create here cannot be deleted, only moved to Closed Lost.
- **Recommended path**: deploy with `Sandbox` first. Run the [sandbox smoke matrix](testing-in-your-account.md#ace-sandbox-smoke-matrix-phase-41) end-to-end. Flip to `AWS` only when you have a real first co-sell deal to submit. The flip is one `terraform apply` (IAM `Catalog` condition + Lambda env var update together).

### 2. AWS region

- **Variable**: `aws_region`. Default `us-east-1`.
- **Constraint**: the Partner Central Selling API is exposed in `us-east-1` only as of 2026-05. The `boto3` client for `partnercentral-selling` is hard-pinned in `src/aws_clients.py`. Other AWS services (DDB, SQS, Lambda) are region-flexible but it's simpler to keep everything in one region.
- **GovCloud**: not supported. See the README FAQ for the "what if I'm in GovCloud" answer.

### 3. Naming prefix

- **Variables**: `project_name` (default `govwin-hubspot`) + `environment` (default `prod`). Every resource is prefixed `{project_name}-{environment}-*`.
- **Reversal cost**: changing either after deploy forces destroy + recreate of every project resource because IAM policies, Lambda function names, log groups, queues, secrets, and the KMS alias all reference the prefix.
- **Recommendation**: pick once and don't change. If you genuinely need to migrate (e.g., merging projects across organizations), plan a parallel deploy + DDB data migration + DNS/webhook cutover; do not in-place rename.

### 4. Multi-environment strategy

- **Option A: single-environment**. Default. One AWS account, one HubSpot portal, `environment=prod`. Simplest. No isolation between testing and production data.
- **Option B: separate AWS accounts per environment**. Common DoD / FedRAMP pattern. Run `terraform apply` once per account with different `environment` values; each gets its own state file and its own bootstrap. Recommended for federal contracting workloads.
- **Option C: one AWS account, multiple environments via prefix**. Run `terraform apply` once per environment in the same account with different `project_name` or `environment` values. Resources are isolated by prefix; HubSpot still has to be a separate portal (or test sandbox) per environment.
- **Recommendation**: if you have any compliance pressure (CMMC, FedRAMP, ITAR), use Option B. Otherwise Option A is fine to start; Option C is a useful intermediate step.

### 5. HubSpot account scope

- **Decision**: does the integration run against the production HubSpot portal, a sandbox / test portal, or both?
- **Tier note**: standard HubSpot tier has no field-level RBAC, so the audit alert on `govwin_aws_cosell_id` is the only defense against BD hand-edits of the AWS-owned identifier. Enterprise tier customers can additionally lock the property at the role level.
- **Webhook scope**: the webhook subscription set is declared in `hubspot-app/src/app/webhooks/webhooks-hsmeta.json`. The Dev Platform app is per-portal; you cannot share one between sandbox and production. Plan to create a separate developer-platform app for each portal you deploy against.

### 6. Pipeline stage internal IDs

- **Variable**: `ace_trigger_stages`. Comma-separated numeric IDs that fire an ACE submission when a deal moves to one of them.
- **How to find them**: after `terraform apply`, run the API call documented in [Deployment Guide step 9b.i](deployment-guide.md#9bi-find-your-hubspot-pipeline-stage-internal-ids-ace_trigger_stages). They look like `3590200042`, not `submit_to_aws`.
- **Why this matters**: skipping this step is the most common "deployed but nothing happens" symptom. The default of `"submit_to_aws,submitted_to_aws"` is label-style placeholders that will never match a real internal ID.

### 7. Notification email (and SNS audience)

- **Variable**: `notification_email`. Defaults to empty (no email subscription).
- **What goes to this address**: terminal sync failures, DLQ depth alarms, ACE permanent error alerts (with body redaction), self-heal mismatch alarms, and the `govwin_aws_cosell_id` audit alerts.
- **Recommendation**: a shared mailbox monitored by the operations team, not an individual. If you want PagerDuty / Opsgenie routing, leave `notification_email` empty and subscribe the integration's HTTPS / SQS endpoint to the SNS topic directly (the topic ARN is a Terraform output).

### 8. AWS Partner Central Solution ID

- **Variable**: `ace_default_solution_id`. The Solution that gets associated with every co-sell submission unless BD picks a different one in the form.
- **How to find it**: `aws partnercentral-selling list-solutions --catalog AWS --region us-east-1` after the AWS account is linked to the partner. The id format is `S-1234567`.
- **Reversal cost**: low. BD can override per-deal in the Submit-to-AWS card; this is the default.
- **Sandbox gotcha**: the Sandbox catalog typically has zero Solutions, so the SolutionPicker dropdown will be empty during sandbox testing. This is expected behavior — `ListSolutions --catalog Sandbox` returning `[]` is not a misconfiguration.

### 9. Partner company legal name

- **Variable**: `ace_partner_company_name`. Surfaced to AWS Partner Central as `ExpectedCustomerSpend.TargetCompany` on every co-sell submission.
- **Default**: `"Partner Company"` (placeholder). Harmless in Sandbox; embarrassing in production.
- **Action**: set to your legal company name (e.g., `"Acme Cloud LLC"`) before flipping `ace_catalog = "AWS"`.

### 10. Sync cadence

- **Variable**: `sync_schedule`. EventBridge Scheduler expression. Default `"rate(1 hour)"`.
- **GovWin budget**: 4,000 calls / hour rolling window across the entire organization. The orchestrator + worker pair fits comfortably inside this budget at any sane cadence; the constraint is realistic only if you also have other GovWin integrations consuming the same budget.
- **Recommendation**: `rate(4 hours)` for the typical BD workflow ("I'll see new opportunities sometime today"). `rate(15 minutes)` only if you have a specific operational reason.

### 11. Fan-out alarm threshold

- **Variable**: `update_in_ace_fanout_threshold`. Default `30` (invocations per minute averaged over 15 minutes).
- **What it detects**: sustained `update_in_ace` invocations approaching the AWS Partner Central 60 writes/minute partner quota.
- **When to change**: if your steady-state BD load legitimately exceeds 30/min (e.g., 100+ ops/day with frequent bulk recategorizes), raise the threshold to silence false alarms. The runbook in [docs/operations.md "Scaling and webhook fan-out"](operations.md#scaling-and-webhook-fan-out) explains the diagnostic path and the coalescing upgrade if per-deal fan-out is the cause.

## Decisions that can wait (or auto-resolve)

These have sensible defaults. Revisit only if a specific need arises.

- `govwin_opp_types` — opportunity types to sync. Default `ALL` is the right starting choice; narrow only if you want to filter at sync time.
- `govwin_marked_version` — marked-for-sync filter. Default `"2.2"` is correct for the Web Services Download flow.
- `govwin_saved_search_id` / `govwin_bookmarked_only` — alternative filtering modes. Most teams stick with marked-for-sync.
- `initial_lookback_days` — how far back the first sync reaches. Default 365 picks up the past year of opportunities. Raise for a complete historical migration; lower if you only care about current pipeline.
- `max_concurrency` — worker Lambda concurrency cap. Default 2; tune only if the GovWin budget allows higher and you actually have queue depth.
- `batch_size` — opportunities per SQS message. Default 10 fits 99% of cases.
- `log_retention_days` — CloudWatch log retention. Default 30. Increase for longer audit windows.

## Compliance considerations

The project ships secure-by-default but does not by itself give you a
specific compliance posture. Items that operators should review against
their own controls:

- **Encryption at rest**: a single customer-managed CMK encrypts both DynamoDB tables, the SNS notifications topic, and every SQS queue (including DLQs). Key rotation is enabled. The key policy scopes service-principal use via `ViaService` / `EncryptionContext`. See [SECURITY.md "Known design decisions"](../SECURITY.md#known-design-decisions).
- **Encryption in transit**: every AWS API call resolves to a FIPS endpoint when `AWS_USE_FIPS_ENDPOINT=true` (the default in production Lambda env). The `scripts/verify_fips.py` CI gate fails if a future change introduces a non-FIPS-resolving service. `partnercentral-selling` is explicitly excepted because AWS does not publish a FIPS variant.
- **Secrets handling**: all credentials (GovWin OAuth2, HubSpot Service Key, HubSpot webhook signing secret) live in Secrets Manager. The Lambda execution roles each carry the minimum `secretsmanager:GetSecretValue` scope (specific ARNs, not wildcards).
- **CloudTrail**: every Lambda invocation, every KMS key use (now under our own keyId, not AWS-managed), every Secrets Manager fetch, and every Partner Central API call lands in CloudTrail. The KMS hoist (see `docs/operations/kms-relocation-runbook.md`) made the encryption audit trail visible under one auditable keyId.
- **Audit alert**: hand-edits of the AWS-owned `govwin_aws_cosell_id` HubSpot property fire a real-time SNS alert. The `update_in_ace` Lambda's self-heal verify refuses to write to AWS when the deal's recovered ACE id resolves to a foreign `PartnerOpportunityIdentifier`.
- **NOT FedRAMP-authorized**: the project runs on AWS commercial. GovCloud and FedRAMP authorization are tracked in [ROADMAP.md](../ROADMAP.md).

## What you cannot decide yourself

A small list of things AWS / HubSpot decide for you. Flag these for the
stakeholders above so nobody is surprised.

- **HubSpot deal stage internal IDs are HubSpot-assigned**, not chosen by you. They are stable per pipeline once created but cannot be renamed.
- **AWS Partner Central catalog is permanent per submission**. An opportunity submitted to `AWS` cannot be moved to `Sandbox` or vice versa.
- **`PartnerOpportunityIdentifier` is permanent forever per catalog**. The project mints these as `pc-<uuid>` so collisions are not a concern at any practical scale, but the choice cannot be re-used.
- **AWS reviewer cadence is set by AWS**, not you. Sandbox submissions stay in `Pending Submission` indefinitely; production submissions advance through review on AWS's timeline (typically days to a couple of weeks).

## Cost expectations

See [docs/cost-model.md](cost-model.md) for a per-component breakdown
across small / medium / large deployment sizes. Quick guide:

- **Small** (Pandora-scale, 5-50 ops/day, ~1k opps): ~$6/month
- **Medium** (200 ops/day, ~10k opps): ~$20/month
- **Large** (1000+ ops/day, ~100k opps): ~$80/month + ACE quota planning

## After this checklist

When you've answered every question in sections 1-11 above and
identified the stakeholders in the table at the top, proceed to the
[Deployment Guide](deployment-guide.md). The guide assumes you've made
these decisions and walks through the actual install.

For testing your deployment end-to-end before flipping
`ace_catalog = "AWS"`, see [Testing in your AWS
account](testing-in-your-account.md).
