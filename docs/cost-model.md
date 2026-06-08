# AWS cost model

This is a planning document for operators evaluating the AWS bill at
different deployment sizes. Numbers assume `us-east-1` and ARM64
(Graviton2) Lambda runtime, and reflect publicly listed AWS prices at
the time of writing.

These are model estimates only; your actual bill depends on real
opportunity volume, BD activity patterns, current AWS pricing, and any
other workloads in the same account. Validate against AWS Cost
Explorer post-deploy; the project's resource tags
(`Application = <project_name>-<environment>`) make the filter trivial.

## TL;DR

| Deployment size | Opportunities synced | BD ops / day | Estimated monthly cost |
|---|---|---|---|
| Small | ~1,000 | 5-50 | **~$6** |
| Medium | ~10,000 | 100-200 | **~$20** |
| Large | ~100,000 | 500-1,000 | **~$80** |
| At-scale | 500,000+ | 2,000+ | $200-500 + ACE quota planning |

The cost ceiling is set by GovWin (4,000 calls/hour quota) and AWS
Partner Central (1 write/sec, 10 reads/sec partner quotas) long before
AWS infrastructure becomes the bottleneck. The largest tier in the
table assumes opportunity-per-sync efficiency that approaches the
GovWin quota ceiling.

## Per-service breakdown (medium tier ~$20/month)

Sized for a 10,000-opportunity catalog, 4-hour sync cadence, ~150 BD
ops/day, ~30 ACE submissions/month.

| Service | Monthly drivers | Monthly cost |
|---|---|---|
| **AWS Lambda** | ~110k orchestrator + worker invocations (4-hour sync, batched), ~15k UI Extension + webhook receiver, ~5k EventBridge handler. ARM64. Mostly within 1M free tier; spillover at $0.20 / 1M. | ~$2 |
| **DynamoDB** | PAY_PER_REQUEST, ~10k entity-mapping reads + ~50k sync-state writes / month. CMK-encrypted (key use within free tier 1k requests/month). | ~$3 |
| **Secrets Manager** | 4 secrets (GovWin creds, GovWin tokens, HubSpot PAT, HubSpot webhook signing) × $0.40/month. ~30k retrievals at $0.05/10k = $0.15. | ~$1.75 |
| **KMS (customer-managed CMK)** | 1 CMK × $1/month + ~100k API requests at $0.03/10k. | ~$1.30 |
| **SQS** | ~150k messages across submit / update / govwin-sync / 3 DLQs. CMK-encrypted. First 1M / month free. | ~$0 |
| **SNS** | ~30 notifications/month. Within free tier. | ~$0 |
| **API Gateway HTTP API** | ~30k webhook deliveries from HubSpot, ~10k UI Extension calls. $1 per 1M HTTP-API requests. | ~$0.04 |
| **EventBridge** | ~180 Scheduler invocations + ~30 partnercentral-selling events. Within free tier. | ~$0 |
| **CloudWatch Logs** | ~10 log groups × ~50 MB/month each + 7 metrics across alarms. ~$0.50/GB ingest + retention. | ~$3 |
| **CloudWatch Alarms** | ~25 alarms (per-Lambda errors + throttles, DLQ depth, scheduler, webhook 5xx, fan-out). $0.10/alarm/month. | ~$2.50 |
| **AWS X-Ray** | Active tracing on every Lambda. First 100k traces / month free; ~30k traces typical. | ~$0 |
| **Data transfer (egress)** | All API calls hit external HTTPS endpoints (GovWin, HubSpot, AWS service endpoints in-region). External egress ~50 MB/month. | ~$0.50 |
| **Lambda layer storage (S3)** | One ~30 MB zip in the build-artifacts bucket. Versioning enabled. | ~$0.10 |

**Total: ~$15-20/month at medium tier.**

## Per-service breakdown (small tier ~$6/month)

Sized for a typical small federal AWS partner: ~1,000 opportunities,
4-hour cadence, ~10 BD ops/day, ~5 ACE submissions/month.

| Service | Monthly cost |
|---|---|
| Lambda | ~$0 (within free tier) |
| DynamoDB | ~$0.50 |
| Secrets Manager | ~$1.75 |
| KMS | ~$1.20 |
| CloudWatch Logs + Alarms | ~$2.50 |
| Everything else | <$0.30 |

**Total: ~$6.25/month.**

The CMK adds ~$1 vs the AWS-managed key default, in exchange for
CloudTrail auditability and the ability to scope key use via key
policy. The CloudWatch Alarms line ($2.50) is the largest single
controllable cost; setting `update_in_ace_fanout_threshold = 0`
disables one alarm if you want to trim.

## Per-service breakdown (large tier ~$80/month)

Sized for an OSS consumer running at 100k opportunities, 1-hour
cadence, ~800 BD ops/day, ~200 ACE submissions/month.

| Service | Monthly cost |
|---|---|
| Lambda (~10M invocations) | ~$15 |
| DynamoDB (PAY_PER_REQUEST, ~10M writes + 5M reads) | ~$25 |
| Secrets Manager | ~$1.50 |
| KMS (~10M key uses) | ~$5 |
| CloudWatch Logs (~5 GB/month ingest) | ~$15 |
| CloudWatch Alarms (~30 alarms) | ~$3 |
| SQS (~10M messages, CMK-encrypted) | ~$5 |
| API Gateway (~1M requests) | ~$1 |
| Data transfer | ~$2 |
| Everything else | <$5 |

**Total: ~$70-85/month.**

At this tier the Lambda + DynamoDB + Logs lines start to dominate.
If you find CloudWatch ingest is the largest controllable item,
consider:

- Raising `log_retention_days` does not affect ingest (just storage),
  so it's safe.
- Reducing the Lambda log level from `INFO` to `WARN` (env var
  `LOG_LEVEL=WARN`) on the hot-path Lambdas (`govwin_worker`,
  `update_in_ace`, `submit_to_ace`) cuts CloudWatch ingest by ~70%
  while keeping error visibility. Tradeoff: less context for triage.
- The fan-out alarm threshold runbook in
  [operations.md](operations.md#scaling-and-webhook-fan-out) explains
  the coalescing upgrade path that reduces both Lambda invocations
  AND CloudWatch log lines for the update path.

## What's NOT in the cost model

The integration intentionally does NOT provision:

- **No VPC**. All traffic goes to external HTTPS endpoints; NAT
  Gateway and VPC Endpoint costs are zero. If you wrap this in a VPC
  for compliance reasons, add ~$30/month per AZ for NAT.
- **No RDS**. DynamoDB is the only data store.
- **No EBS / EC2**. Pure serverless.
- **No load balancers**. API Gateway HTTP API is the only ingress
  surface.

## External costs (not in the AWS bill)

- **Deltek GovWin IQ subscription**: required for the GovWin source side. Pricing is per-seat and negotiated; this is by far the largest line item if you're scoping total program cost.
- **HubSpot CRM tier**: Professional or Enterprise tier required for the custom-property scope. Pricing is per-seat; tier choice depends on features beyond this integration.
- **AWS Partner Central enrollment**: free for AWS Partners; no marginal cost.

## Cost monitoring runbook

Post-deploy, set a baseline:

```bash
# Tag-filtered cost report for the current month
aws ce get-cost-and-usage \
  --time-period Start=$(date -u +%Y-%m-01),End=$(date -u +%Y-%m-%d) \
  --granularity DAILY \
  --metrics UnblendedCost \
  --filter '{"Tags":{"Key":"Application","Values":["govwin-hubspot-prod"]}}'
```

After a week of live traffic, the daily numbers should be predictable
within ~20%. A sudden 2x jump usually means either:

1. A new external integration with the same secrets is hammering
   Secrets Manager — check CloudTrail.
2. A Lambda regression is firing in a hot loop — check the new alarm
   `govwin-hubspot-prod-update-in-ace-high-rate` and the Errors
   alarm for each Lambda.
3. DynamoDB throttling under unexpected load is causing retry storms —
   check the table's `ConsumedReadCapacityUnits` and
   `ConsumedWriteCapacityUnits`.

Configure an AWS Budgets alert on the `Application` tag for ~$2 over
your steady-state baseline. The budget alarm catches the failure
modes above before they compound.
