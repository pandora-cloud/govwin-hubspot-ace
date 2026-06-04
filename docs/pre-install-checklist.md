# Before you install

Have these in hand before `terraform apply`. Everything else has
sensible defaults.

## What you need

- **AWS account in `us-east-1`**. The AWS Partner Central Selling API is
  region-locked.
- **AWS Partner Central enrollment** in good standing with at least one
  Approved Solution. Run
  `aws partnercentral-selling list-solutions --catalog AWS --region us-east-1`
  to confirm and note the Solution ID; you'll use it as
  `ace_default_solution_id`.
- **HubSpot Service Key** (or a Private App if Service Keys aren't
  available on your account) with the scopes listed in the
  [Deployment Guide step 2](deployment-guide.md#step-2-create-hubspot-api-token).
- **HubSpot developer-platform project** authenticated with `hs account auth`.
  The `hubspot-app/` directory in this repo is the project content; you
  don't need to run `hs project create`.
- **Deltek GovWin IQ WSAPI V3 credentials**: client ID, client secret,
  username, password. A dedicated API user account is recommended over
  a person's credentials.
- **Tooling**: Terraform >= 1.11, AWS CLI, Python 3.12, `uv`.

## What to set in `terraform.tfvars`

Two values actually need attention. The rest have sensible defaults.

1. **`ace_catalog`** — leave at `"Sandbox"` for the first deploy. Flip
   to `"AWS"` only after the sandbox smoke matrix passes. AWS-catalog
   submissions are permanent per opportunity.
2. **`ace_default_solution_id`** — the AWS Solution ID you noted above.

Worth setting on first install:

- **`notification_email`** — a shared ops mailbox subscribed to the
  alarms SNS topic.
- **`ace_trigger_stages`** — comma-separated numeric HubSpot deal-stage
  IDs that fire an ACE submission. You retrieve these AFTER your first
  apply against your live HubSpot pipeline; see
  [Deployment Guide step 9b.i](deployment-guide.md#9bi-find-your-hubspot-pipeline-stage-internal-ids-ace_trigger_stages).

## What can't be changed later

- **HubSpot deal-stage internal IDs** are HubSpot-assigned and stable
  per pipeline. You don't choose them; you discover and reference them.
- **AWS Partner Central catalog is permanent** per opportunity. A
  Sandbox submission stays in Sandbox; an AWS submission stays in AWS.
- **`PartnerOpportunityIdentifier`** is permanent forever per catalog.
  The project mints these as `pc-<uuid>`; the value is bound to an
  opportunity for the life of that opportunity.

## Cost

| Deployment size | Opportunities synced | BD ops/day | Monthly cost |
|---|---|---|---|
| Small | ~1,000 | 5-50 | ~$6 |
| Medium | ~10,000 | 100-200 | ~$20 |
| Large | ~100,000 | 500-1,000 | ~$80 |

Per-service breakdown in [docs/cost-model.md](cost-model.md).

## Next

Go to the [Deployment Guide](deployment-guide.md).
