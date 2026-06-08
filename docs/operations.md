# Operations runbook

This is the day-2 reference for operating a deployed instance of the integration. Pair it with [docs/testing-in-your-account.md](testing-in-your-account.md) for the deployment-time playbook.

Assumed `name_prefix`: `govwin-hubspot-prod`. Substitute your project's name prefix where noted.

## CloudWatch alarms

The `monitoring` Terraform module ships these alarms wired to the same SNS topic as terminal sync failures. Subscribe at least one email or PagerDuty integration to that topic. Alarm names use the resource name they monitor as the prefix so they sort together in the CloudWatch console.

| Alarm | Threshold | What it means | First action |
|---|---|---|---|
| `<lambda-name>-errors` (per Lambda) | `Sum(Errors) >= 1` over 5 min | The Lambda threw at least one uncaught exception. | Tail the function's log group: `aws logs tail /aws/lambda/<lambda-name> --follow`. Look for the most recent stack trace. |
| `<lambda-name>-throttles` (per Lambda) | `Sum(Throttles) >= 1` over 5 min | Reserved concurrency or account concurrency limit hit. | Check the function's `ReservedConcurrentExecutions` against current concurrent-invocation rate. Raise `worker_concurrency` (Terraform variable) if you have headroom against the GovWin 4k/hr budget. |
| `<dlq-name>-depth` (per DLQ) | `ApproximateNumberOfMessagesVisible >= 1` | A message exhausted SQS retries. | Inspect the DLQ; see [Stuck deal recovery](#stuck-deal-recovery) below. |
| `<name-prefix>-scheduler-target-errors` | `Sum(TargetErrorCount) >= 1` over 5 min | EventBridge Scheduler failed to invoke the orchestrator. | Check the Scheduler's role can still assume the orchestrator's role; confirm the orchestrator function exists. |
| `<name-prefix>-webhook-5xx-burst` | `Sum(5XXError) >= 5` over 5 min | The webhook receiver API Gateway returned 5xx repeatedly. | Tail the webhook receiver Lambda log group; usual cause is Secrets Manager unreachable or upstream SQS throttling. |
| `<name-prefix>-update-in-ace-high-rate` | `Sum(Invocations) >= threshold * 5` per 5-min period, sustained 15 min | The update path is approaching the AWS Partner Central 60-writes/min ceiling. Threshold is `var.update_in_ace_fanout_threshold` per minute (default 30). | See [Scaling and webhook fan-out](#scaling-and-webhook-fan-out) below. |

To list alarms in a console-friendly way:

```bash
aws cloudwatch describe-alarms \
  --alarm-name-prefix govwin-hubspot-prod \
  --query 'MetricAlarms[].{Name:AlarmName,State:StateValue,Reason:StateReason}' \
  --output table
```

### Audit alerts on integration-owned properties

The webhook receiver SNS-alerts on changes to `govwin_aws_cosell_id`
that come from any source other than this integration. Two subject
lines distinguish the cases:

- **`AWS Co-sell ID hand-edited on HubSpot deal <id>`**: the change
  came from `CRM_UI`, `API`, `WORKFLOWS`, `IMPORT`, or another
  non-integration source. Triage by running
  `make reconcile GOVWIN_ID=<opp>` and reverting the property if the
  edit was unintentional.
- **`AWS Co-sell ID written by foreign HubSpot integration (deal <id>, app <source_id>)`**:
  the change came from an INTEGRATION source whose `sourceId` did
  NOT match `var.hubspot_webhook_app_id`. Another HubSpot integration
  installed on the same portal wrote the property. Identify the app
  at `https://app.hubspot.com/integrations/<portal>/installed-apps`
  and decide whether its access should be revoked.

Either alert means the next deal save will trip the `update_in_ace`
self-heal verify and refuse the AWS write.

## DLQs and queues

Three operational SQS DLQs hold messages that have exhausted retries:

- `govwin-hubspot-prod-govwin-sync-dlq` - GovWin worker batch failed all 5 redeliveries.
- `govwin-hubspot-prod-ace-submit-dlq` - ACE submission failed all retries (typically permanent ValidationException).
- `govwin-hubspot-prod-ace-update-dlq` - ACE update failed all retries.

The general project DLQ (`govwin-hubspot-prod-dlq`) catches anything else.

```bash
# Peek at the next message without removing it
aws sqs receive-message \
  --queue-url $(terraform -chdir=terraform output -raw dlq_url) \
  --max-number-of-messages 1 \
  --visibility-timeout 30

# Drain a DLQ back into its source queue (idempotent if your handlers are)
aws sqs start-message-move-task \
  --source-arn arn:aws:sqs:us-east-1:ACCOUNT:govwin-hubspot-prod-ace-submit-dlq \
  --destination-arn arn:aws:sqs:us-east-1:ACCOUNT:govwin-hubspot-prod-ace-submit
```

### DLQ status and replay via Makefile

The DLQ commands above are wrapped by `make` targets that handle the
queue-url lookup and ARN composition:

```bash
make dlq-status                           # depth for every project DLQ
make dlq-redrive QUEUE=<dlq-name>         # start a redrive task

# Override the AWS profile / project prefix / region:
make dlq-status PROFILE=ops PREFIX=acme-cosell-prod REGION=us-east-1
```

`dlq-redrive` issues `aws sqs start-message-move-task` from the
source DLQ back into its parent queue; the source queue retries the
messages just like a fresh delivery. Make sure the underlying poison
condition is fixed before redriving, otherwise the messages cycle
back into the DLQ on the same code path.

### 4-way state reconciliation

When the self-heal mismatch alert fires, or the operator suspects DDB
state is out of sync with HubSpot or AWS, dump the 4-way view for a
single GovWin opportunity:

```bash
make reconcile GOVWIN_ID=OPP12345
make reconcile GOVWIN_ID=OPP12345 CATALOG=AWS   # production catalog
```

The script (`scripts/reconcile.py`) reads from DynamoDB, HubSpot
deal-properties, AWS `GetOpportunity`, and AWS `ListOpportunities`
(matching `PartnerOpportunityIdentifier`) and prints each side's
view plus a drift summary. Read-only; safe to run against production.

## Stuck deal recovery

A deal is "stuck" when:

- BD has moved it to a `ace_trigger_stages` stage.
- The webhook fired (`<name-prefix>-hubspot-webhook-receiver` log shows a 200 for the event).
- But `submit_to_ace` never created the AWS Partner Central opportunity (no `Created opportunity Id=O-...` log line, and `ACE#{govwin_id}` in DynamoDB has no `ace_opportunity_id`).

Common causes and recovery:

### Scenario 1: the SQS message landed in the DLQ

Identify it:

```bash
aws sqs receive-message \
  --queue-url $(aws sqs get-queue-url --queue-name govwin-hubspot-prod-ace-submit-dlq --query QueueUrl --output text) \
  --max-number-of-messages 10 \
  --message-attribute-names All \
  --visibility-timeout 60
```

Each message body contains the original HubSpot event. Read the `submit_to_ace` log group around the time the message was first delivered to find the rejection reason. Typical reasons:

- `ValidationException` for a missing or malformed required field. **Fix the deal in HubSpot, then redrive the DLQ.**
- `AccessDeniedException` because the Lambda role lost its `partnercentral:*` actions. **Fix the IAM, then redrive.**

After fixing the cause, redrive the DLQ back into the submit queue (see SQS commands above).

### Scenario 2: the trigger stage doesn't match `ace_trigger_stages`

Symptom: webhook receiver returned 200 with `dropped=1` in the log. The deal moved into a stage that isn't in `ace_trigger_stages`.

Fix: either move the deal into a configured trigger stage, or update `ace_trigger_stages` and `terraform apply`. See `docs/deployment-guide.md#9bi-find-your-hubspot-pipeline-stage-internal-ids-ace_trigger_stages`.

### Scenario 3: DynamoDB has stale state from a prior failed attempt

Sometimes a partial submission leaves `ACE#{govwin_id}` in an intermediate state (e.g. `ace_opportunity_id` set but `ace_engagement_id` missing). The `submit_to_ace` Lambda detects this and resumes from the appropriate step on the next SQS delivery, but if you need to force a clean retry:

```bash
# Inspect the current mapping
aws dynamodb get-item \
  --table-name govwin-hubspot-prod-entity-mappings \
  --key '{"pk":{"S":"ACE#OPP12345"},"sk":{"S":"MAPPING"}}'

# Delete the stale mapping (will cause the next submission to start fresh)
aws dynamodb delete-item \
  --table-name govwin-hubspot-prod-entity-mappings \
  --key '{"pk":{"S":"ACE#OPP12345"},"sk":{"S":"MAPPING"}}'
```

After deleting, toggle the deal's stage off and back on in HubSpot to re-trigger the webhook.

### Scenario 4: an EventBridge event got skipped

If the AWS-side state changed (Approved, Rejected, etc.) but the HubSpot deal stage didn't update, the EventBridge dedup table may have a stale entry. Inspect:

```bash
aws dynamodb get-item \
  --table-name govwin-hubspot-prod-entity-mappings \
  --key '{"pk":{"S":"EVT#<event-id>"},"sk":{"S":"SEEN"}}'
```

If you need to force re-processing, delete the entry. The TTL is 24h so this is rarely needed in steady state.

## Deferred edits and review-status reconciliation

AWS Partner Central rejects `UpdateOpportunity` while an opportunity's review status is `Submitted`, `In review`, or `Rejected`. Rather than dropping a HubSpot edit that lands during that window, `update_in_ace` parks it and replays it automatically once AWS lets the opportunity be edited again (`Approved` or `Action Required`). For BD, this is invisible: the edit applies on its own after review completes. This section is for the operator who needs to confirm a parked edit is progressing.

### What "queued" means

When BD edits a deal (amount, close date, name, AWS products, stage, and so on) while its AWS opportunity is still under review, the deal's **Next steps** field (`govwin_ace_next_steps`) shows a note like:

> Edit to 'amount' queued; will apply automatically after AWS completes its review.

That is the expected, healthy state. No alert fires, because deferral is a normal outcome, not a failure. The changed property name is recorded on the DynamoDB `ACE#{govwin_id}` mapping in a String Set attribute named `pending_reconcile_props`. Multiple edits during the same review window accumulate into that set and are coalesced into a single `UpdateOpportunity` when the opportunity becomes editable.

### How the replay is triggered

Two independent triggers, so a single dropped event never strands an edit:

1. **Event-driven (primary):** AWS emits an `Opportunity Updated` EventBridge event on approve or reject. `handle_ace_event` reads the review status from that event and, if the opportunity is now editable and has parked props, replays them immediately.
2. **Scheduled sweep (backstop):** `govwin-hubspot-prod-reconcile-pending` runs on the `rate(6 hours)` EventBridge Scheduler (`govwin-hubspot-prod-ace-reconcile-pending`). It scans the mapping table for rows with `pending_reconcile_props`, calls `GetOpportunity` on each, and replays the parked edits for any row whose review status has become editable. Rows still in a blocked status are skipped and stay queued. The sweep exists because AWS EventBridge delivery is best-effort: if the approve event is dropped, the sweep still catches the edit within one cadence.

On a successful replay, the parked set is cleared, the AWS opportunity's `LastModifiedDate` is refreshed in DynamoDB, and the deal's **Next steps** note is replaced with:

> Deferred edit(s) applied to AWS after review completed: amount, closedate.

### Inspecting a parked deal

The 4-way diagnostic (`make reconcile GOVWIN_ID=<opp>`, described above) prints the full DynamoDB mapping, so `pending_reconcile_props` and its members show up directly in section 1 of its output. It is read-only and does not conflict with the sweep; run it any time, including while the sweep is active. To check the parked set without the full diagnostic:

```bash
aws dynamodb get-item \
  --table-name govwin-hubspot-prod-entity-mappings \
  --key '{"pk":{"S":"ACE#OPP12345"},"sk":{"S":"MAPPING"}}' \
  --query 'Item.pending_reconcile_props'
```

A non-empty `SS` (string set) means edits are still parked. Cross-check the review status with `GetOpportunity`:

```bash
PYTHONPATH=. .venv/bin/python -c \
  "from src.config import load_config; from src.ace.client import ACEClient; \
   print(ACEClient(load_config()).get_opportunity('O-XXXXXXXX')['LifeCycle']['ReviewStatus'])"
```

- Status is `Submitted`, `In review`, or `Rejected`: parked is correct; wait for AWS. Nothing to do.
- Status is `Approved` or `Action Required` but the set is still non-empty after the next sweep cadence (6 hours): the replay is failing. Check the next subsection.

### Forcing a sweep and handling sweep failures

To replay immediately instead of waiting for the cadence (for example, after you have confirmed a deal left review):

```bash
aws lambda invoke \
  --function-name govwin-hubspot-prod-reconcile-pending \
  --payload '{}' /dev/stdout
```

The sweep is idempotent: clearing the parked set after a successful replay makes a second run a no-op, and the EventBridge-driven path dedups on event id, so a manual invoke racing the scheduler is safe.

If a replayed edit is permanently rejected by AWS on merit (a genuinely invalid field value, not a status lock), the sweep clears the parked set so it does not loop forever and writes the reason onto the deal's **Next steps** field (`AWS rejected the deferred edit after review (<code>): ...`). Treat that like any other `ValidationException`: fix the field in HubSpot and re-save to re-trigger the update path.

Sweep-level failures surface through standard monitoring:

- `govwin-hubspot-prod-reconcile-pending-errors`: the sweep Lambda threw. Tail `/aws/lambda/govwin-hubspot-prod-reconcile-pending`.
- `govwin-hubspot-prod-ace-reconcile-pending-target-errors`: EventBridge Scheduler could not invoke the sweep. After the scheduler exhausts its 2 retries, the failed invocation lands in `govwin-hubspot-prod-ace-reconcile-sweep-dlq`.
- `govwin-hubspot-prod-ace-reconcile-sweep-dlq-depth`: a sweep invocation exhausted retries. Inspect the DLQ as with any other (see [DLQs and queues](#dlqs-and-queues)).

## DynamoDB backup and restore

Both DynamoDB tables use on-demand billing and PITR (point-in-time recovery) is enabled by default in the production module.

```bash
# Verify PITR is on (should return PointInTimeRecoveryStatus=ENABLED)
aws dynamodb describe-continuous-backups \
  --table-name govwin-hubspot-prod-sync-state

# Take an on-demand backup before a risky migration or schema change
aws dynamodb create-backup \
  --table-name govwin-hubspot-prod-sync-state \
  --backup-name "pre-migration-$(date +%Y%m%d-%H%M%S)"

# List backups
aws dynamodb list-backups --table-name govwin-hubspot-prod-sync-state

# Restore PITR to a specific point (creates a new table; you migrate over)
aws dynamodb restore-table-to-point-in-time \
  --source-table-name govwin-hubspot-prod-sync-state \
  --target-table-name govwin-hubspot-prod-sync-state-restored \
  --restore-date-time 2026-05-01T12:00:00
```

## Lambda code-deploy procedure

For a code-only change (no Terraform infrastructure changes):

```bash
make package
cd terraform
terraform apply
```

Lambdas pick up the new code immediately. In-flight SQS messages already being processed by the previous version's containers complete on the old code; new messages run on the new code. There is no rolling deploy gate; the system tolerates a brief mixed-version window because every Lambda is idempotent.

For a Terraform-only change (no code change), `terraform apply` from the repo root is sufficient.

For a coordinated change that affects both code and infrastructure (e.g. adding a new env var that the code reads), do these in order:

1. Update the code first to read the new env var with a safe default.
2. `make package`.
3. `terraform apply` (introduces the new env var alongside the new code).
4. Once verified, in a follow-up commit, remove the safe default if the var is now mandatory.

## FIPS verification

The Lambdas should always resolve AWS service endpoints to FIPS-suffixed hostnames. Verify:

```bash
PYTHONPATH=. .venv/bin/python scripts/verify_fips.py
```

Expected output: `OK` for every service. If any line shows `FAIL`, that environment has been misconfigured (likely `AWS_USE_FIPS_ENDPOINT=false` was inherited from somewhere).

For an in-cluster sanity check (run from inside a Lambda or a workstation with the same env), the script also reads from boto3, so it sees what the Lambdas see.

## Fault-injection

`scripts/fault_inject.py` exercises the failure paths end-to-end so you can verify that DLQs and SNS alerts actually fire. Run it before flipping `ace_catalog` to `AWS` and after any change to the monitoring/alerting stack.

```bash
PYTHONPATH=. .venv/bin/python scripts/fault_inject.py --suite all
```

The script:

1. Publishes a malformed message to the GovWin sync queue and confirms it lands in the DLQ after retries.
2. Sends an HTTP request with a forged `X-HubSpot-Signature-v3` header and confirms the receiver returns 401.
3. Synthesizes an `Engagement Invitation Expired` EventBridge event and confirms the dedup table records it (run twice to verify the second is a no-op).
4. Confirms the SNS topic publishes a notification on terminal sync failure (uses a no-op subscription that records the message).

Run individual checks with `--suite dlq`, `--suite webhook`, `--suite eventbridge`, or `--suite sns`.

## Scaling and webhook fan-out

The HubSpot to AWS Partner Central update path is one-to-one by design:
every property-change webhook fires one `update_in_ace` invocation and
one AWS `UpdateOpportunity` call. This is fine at the steady-state load
the project was tuned for (5-50 ops/day, 10-20 property edits per
deal save, occasional bulk recategorize of fewer than 50 deals at a
time).

At higher steady-state load the per-partner 1-write/sec AWS quota
becomes the bottleneck. The alarm
`<name-prefix>-update-in-ace-high-rate` fires when sustained
invocations approach the ceiling.

### When the alarm fires

1. Tail the update Lambda log group and grep the recent invocations
   for distinct deal ids:

   ```bash
   aws logs tail /aws/lambda/<prefix>-update-in-ace --since 15m \
     | grep -oE 'deal=[0-9]+' | sort -u | wc -l
   ```

   Compare to the total invocation count in the same window. If they
   are close (most invocations are distinct deals), the cause is a
   bulk operation, which is benign: the queue drains and the alarm
   self-clears. If invocations are much higher than distinct deals,
   you are seeing per-deal fan-out (10 BD edits on one save fire 10
   `UpdateOpportunity` calls for the same deal).

2. **Bulk operation** path: no action required other than waiting for
   the alarm to OK. If your steady-state load legitimately exceeds the
   default 30/min threshold, raise
   `var.update_in_ace_fanout_threshold` to silence the alarm at the
   new baseline.

3. **Per-deal fan-out** path: implement webhook coalescing to collapse
   one form save's N property changes into a single
   `UpdateOpportunity` call. The change is:

   - **Receiver** (`src/lambdas/hubspot_webhook_receiver.py`): group
     update events by `deal_id` within each Lambda invocation before
     enqueueing; emit one SQS message per `(deal_id, list[events])`.
   - **update_in_ace** (`src/lambdas/update_in_ace.py`): the
     `_apply_delta` dispatch table already supports applying multiple
     property changes to one payload. `_process_event` would loop the
     event list before the single `UpdateOpportunity` call.
   - **Tests**: the existing single-event paths become a list-of-one
     degenerate case; add coverage for the list-of-N path.

   Scoping note: the fan-out only matters when the property changes
   are on the **same deal** within one Lambda delivery. AWS Products
   and Solution associations already coalesce at the diff layer
   (`_handle_aws_products_diff` / `_handle_solution_diff`) because
   list-valued properties fire one webhook per change to the full
   list, not per element.

### Tuning the threshold

The default 30 invocations/minute averaged over 15 minutes is
calibrated for the original Pandora deployment. To re-tune:

- **Disable**: set `update_in_ace_fanout_threshold = 0` in
  Terraform. The alarm is `count`-guarded out of the plan.
- **Raise**: set a value matching your normal peak burst plus
  headroom. For example, an OSS consumer running 200 BD ops/day might
  see legitimate bursts of 80-100/min during bulk imports; set
  `update_in_ace_fanout_threshold = 90` to avoid alarm noise.

The AWS hard ceiling is 60 writes/min per partner; setting the
threshold higher than that asks for `ThrottlingException`s instead
of an alarm.

## Disaster recovery

Recovery time objective: 1 hour. Recovery point objective: zero data loss for state that lives in DynamoDB (PITR), at most one orchestrator tick (default 1 hour) for in-flight GovWin discoveries.

Procedure:

1. Re-bootstrap the AWS account with `terraform/bootstrap/` if the account itself was lost.
2. Restore DynamoDB from PITR or the most recent on-demand backup.
3. Re-run `terraform apply` to recreate Lambdas, queues, and IAM.
4. `make package && terraform apply` to push the same code version.
5. Re-deploy the HubSpot project (`cd hubspot-app && hs project upload`) to re-register the webhook subscriptions and UI Extension card via the Dev Platform manifest.
6. Verify with `scripts/verify_fips.py` and a manual orchestrator invocation.

The HubSpot side (deals, properties, pipelines) is untouched by this procedure: HubSpot is the system of record, not us.
