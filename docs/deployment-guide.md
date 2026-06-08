# Deployment Guide

Step-by-step instructions for deploying the GovWin-to-HubSpot integration on AWS.

## Prerequisites checklist

### AWS-side (upstream of this project)

These are AWS account-level prerequisites that this project does **not** automate. They must be in place before you deploy.

- [ ] **AWS Partner Central account** in good standing. You enroll once per organization; see https://partnercentral.aws.com for the registration flow.
- [ ] **At least one Approved Solution** registered in Partner Central. Verify with:
  ```
  aws partnercentral-selling list-solutions --catalog AWS --region us-east-1 \
    --query 'SolutionSummaries[?Status==`Active`].{Id:Id,Name:Name}'
  ```
  If this is empty, register a Solution in the Partner Central UI under **Sell -> My Solutions** before continuing. Approval typically takes 24-72 hours.

### Tooling on your machine

- [ ] **Terraform** >= 1.11 (we use the native S3 lockfile, `use_lockfile = true`)
- [ ] **AWS CLI** v2 configured
- [ ] **Python** >= 3.12 for the Lambda layer build
- [ ] **HubSpot CLI** for the developer-platform app: `npm install -g @hubspot/cli`

### Credentials and accounts

- [ ] **Deltek GovWin IQ** subscription with WSAPI V3 access (Client ID, Client Secret, username, password)
- [ ] **HubSpot Professional or Enterprise** with two app-style integrations:
  - **Existing private-app token** for REST API calls (`crm.objects.deals/companies/contacts.read+write`, schemas read+write)
  - **New developer-platform app** (`hs project create`) for webhook delivery; you'll get an `appId` and `clientSecret` after `hs project upload` completes
- [ ] **AWS account** with **two distinct IAM identities** (see [IAM model](#iam-model) below for details):
  - A **bootstrap operator** identity, used once, with a small scoped policy ([`terraform/bootstrap/policies/bootstrap-operator.json`](../terraform/bootstrap/policies/bootstrap-operator.json))
  - A **deployer identity** that day-to-day deployers use (with `sts:AssumeRole` on the project-scoped deployer role, created by the bootstrap)

> **No identity in this project requires AWS administrator access.** The bootstrap-operator policy is ~30 IAM actions, all scoped to `arn:aws:s3:::govwin-hubspot-*-tfstate-*` and `arn:aws:iam::*:role/govwin-hubspot-*-deployer`. The deployer role's policy is the project's full deploy manifest, scoped to `${name_prefix}-*` resource ARNs.

## IAM model

This project follows the principle of least privilege. Three identities are involved, each with a documented and version-controlled policy:

| Identity | Used by | When | Permissions |
|---|---|---|---|
| **Bootstrap operator** | Security team / one-time setup | Once per environment | `terraform/bootstrap/policies/bootstrap-operator.json` (S3 state bucket + deployer role creation only) |
| **Deployer role** | `terraform apply` for the main module | Every deploy | Created by bootstrap; inline policies in `terraform/bootstrap/deployer_role.tf`. Scoped to `govwin-hubspot-${env}-*` resources. |
| **Lambda execution role** | The deployed Lambdas at runtime | Continuous | Created by `terraform/modules/lambda` and `terraform/modules/ace`. Scoped to specific table / queue / secret ARNs and `partnercentral:Catalog: ${env}` condition. |

The day-to-day deployer's personal IAM identity needs only `sts:AssumeRole` on the deployer role's ARN. CloudTrail records every assumption, so audit logs show "Alice assumed govwin-hubspot-prod-deployer at T1, applied 12 changes."

## Step 1: Clone the Repository

```bash
git clone https://github.com/pandora-cloud/govwin-hubspot-ace.git
cd govwin-hubspot-ace
```

## Step 1a: Run the bootstrap (one-time per environment)

This creates the Terraform state bucket and the project's least-privilege deployer role. After this completes once, the security team deletes the bootstrap-operator credentials and all subsequent deploys go through `sts:AssumeRole`. See [`terraform/bootstrap/README.md`](../terraform/bootstrap/README.md) for the full workflow; the short version:

1. Have your security team create a one-time IAM user with the policy in `terraform/bootstrap/policies/bootstrap-operator.json`. Generate access keys and hand them to the deployer.
2. As the deployer, run:
   ```bash
   cd terraform/bootstrap
   cp terraform.tfvars.example terraform.tfvars
   # Edit terraform.tfvars: set deployer_principal_arns to the IAM users/roles
   # that should be allowed to assume the deployer role for day-N applies.
   AWS_PROFILE=govwin-hubspot-bootstrap terraform init
   AWS_PROFILE=govwin-hubspot-bootstrap terraform apply
   ```
3. Capture the outputs (`state_bucket_name`, `deployer_role_arn`).
4. Have the security team delete the bootstrap-operator user.

You will not need the bootstrap operator again unless you change the list of `deployer_principal_arns` later.

## Step 1b: Prepare HubSpot

Before deploying, the integration expects an existing pipeline named **"GovWin Pipeline"** in HubSpot. The integration does not create a pipeline (HubSpot Professional accounts are limited to two custom pipelines, so creating one for every deployer is unsafe).

1. Go to **Settings > Objects > Deals > Pipelines**.
2. Either create a new pipeline named exactly `GovWin Pipeline` or rename an existing one.
3. Add stages with these labels (or the labels you prefer; if you change them, the configuration variable `GOVWIN_STATUS_TO_STAGE` in the project's HubSpot properties module must be updated to match):
   - Opportunity Identified
   - Reviewing Requirements
   - Preparing Response
   - Submitted
   - Closed Won
   - Closed Lost
   - Declined
   - Other

If you want a different pipeline name, set the `PIPELINE_NAME` configuration before building the Lambda layer.

## Step 2: Create HubSpot API Token

HubSpot offers two methods for API authentication. Use **Service Keys** (recommended) or Private Apps (legacy).

### Option A: Service Key (Recommended)

Service Keys are HubSpot's modern replacement for Private Apps. They provide a non-expiring bearer token for API-only integrations.

1. Log in to HubSpot as a **Super Admin**
2. Go to **Settings > Integrations > Service Keys**
3. Click **"Create service key"**
4. Name it `GovWin Integration`
5. Select these scopes:
   - `crm.objects.deals.read` and `crm.objects.deals.write`
   - `crm.objects.companies.read` and `crm.objects.companies.write`
   - `crm.objects.contacts.read` and `crm.objects.contacts.write`
   - `crm.schemas.deals.read` and `crm.schemas.deals.write`
   - `crm.schemas.companies.read` and `crm.schemas.companies.write`
   - `crm.schemas.contacts.read` and `crm.schemas.contacts.write`
6. Click **Create** and copy the token (starts with `pat-na1-` or `pat-na2-`)

If your HubSpot account does not yet expose Service Keys, use Option B below.

### Option B: Private App (Legacy, still works)

1. Log in to HubSpot as a **Super Admin**
2. Go to **Settings > Integrations > Private Apps**
3. Click **"Create a private app"**
4. Name it `GovWin Integration`
5. Under the **Scopes** tab, enable the same 12 scopes listed in Option A
6. Click **Create app** and copy the access token

Heads up: Private Apps are marked as "legacy" by HubSpot. They still work and have no announced sunset date, but new integrations should prefer Service Keys when available.

### Token Format

Both options produce a bearer token in the same format:
```
pat-na1-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```
This token does not expire. Store it securely. Anyone with this token has full read/write access to your CRM data within the granted scopes.

## Step 3: Get GovWin API Credentials

You need 4 values from GovWin: a Client ID, Client Secret, username, and password. The integration uses OAuth2 password grant to authenticate.

### 3a. Get Client ID and Client Secret

Your organization's GovWin administrator provisions API access:

1. Log in to [GovWin IQ](https://iq.govwin.com) as an **administrator**
2. Navigate to **Admin > Web Service API** (or contact your GovWin account manager to enable WSAPI access)
3. In the API management section, create or locate your **Client ID** and **Client Secret**
4. Copy both values. These are organization-level credentials shared across all API users

> **Note:** If you don't see the Web Service API option in Admin, your GovWin subscription may not include WSAPI access. Contact Deltek GovWin support or your account manager to add it.

### 3b. Get Username and Password

The API authenticates as a specific GovWin user:

1. Use an existing GovWin user account, OR create a dedicated API user (recommended for production)
2. The **username** is the user's email address (e.g., `api-user@company.com`)
3. The **password** is the user's GovWin login password

> **Important security notes:**
> - The API user's permissions determine what data is accessible. A user with access to Federal opportunities will only sync Federal data.
> - GovWin accounts **lock for 30 minutes after 5 failed authentication attempts**. Use a dedicated API user to avoid locking out a human user.
> - GovWin passwords may need to be updated periodically (check with your admin). If the password changes, update it in AWS Secrets Manager or redeploy with the new value.
> - The API rate limit of **4,000 calls/hour** is shared across all users in your organization. If other tools also use the WSAPI, coordinate to avoid exhausting the limit.

### 3c. Verify Your Credentials

Before deploying, you can test your credentials locally:

```bash
# Copy and fill in your .env file
cp .env.example .env
# Edit .env with your GovWin credentials

# Run validation against GovWin only
set -a && source .env && set +a
python scripts/validate.py --skip-hubspot
```

Or test manually with curl:
```bash
curl -X POST https://services.govwin.com/neo-ws/oauth/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "client_id=YOUR_CLIENT_ID&client_secret=YOUR_CLIENT_SECRET&grant_type=password&username=YOUR_EMAIL&password=YOUR_PASSWORD&scope=read"
```

A successful response returns an `access_token` and `refresh_token`.

## Step 4: Configure the main module

### 4a. Wire the bootstrap outputs into the backend

Copy the example backend file and fill in the values from `terraform output` in the bootstrap directory:

```bash
cp terraform/backend.tf.example terraform/backend.tf
```

Edit `terraform/backend.tf`:

```hcl
terraform {
  backend "s3" {
    bucket   = "govwin-hubspot-prod-tfstate-XXXXXXXX"     # from bootstrap output
    key      = "govwin-hubspot/terraform.tfstate"
    region   = "us-east-1"
    encrypt  = true
    use_lockfile = true
    profile  = "default"                                   # local profile with sts:AssumeRole on the deployer role
    role_arn = "arn:aws:iam::ACCOUNT:role/govwin-hubspot-prod-deployer"  # from bootstrap output
  }
}
```

### 4b. Set the variable values

```bash
cp terraform/terraform.tfvars.example terraform/terraform.tfvars
```

Edit `terraform/terraform.tfvars`:

```hcl
# Required - tells the provider which role to assume for resource operations
deployer_role_arn = "arn:aws:iam::ACCOUNT:role/govwin-hubspot-prod-deployer"

# Required - GovWin credentials
govwin_client_id     = "your-client-id"
govwin_client_secret = "your-client-secret"
govwin_username      = "your-email@company.com"
govwin_password      = "your-password"

# Required - HubSpot credentials (existing private-app token)
hubspot_private_app_token = "pat-na1-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"

# Required for the HubSpot to AWS Partner Central submission path
ace_default_solution_id       = "S-1234567"          # from `aws partnercentral-selling list-solutions`
ace_trigger_stages            = "3590200042,3590200043"  # numeric HubSpot pipeline-stage IDs (see step 9b.i)
hubspot_webhook_app_id        = "12345678"           # from `hs project upload`
hubspot_webhook_client_secret = "<from HubSpot dev portal>"

# Optional - keep defaults unless you need to override
ace_catalog        = "Sandbox"                        # flip to "AWS" only after sandbox smoke passes
aws_region         = "us-east-1"
sync_schedule      = "rate(1 hour)"
notification_email = "alerts@company.com"
```

> **Security:** `terraform.tfvars` and `terraform/backend.tf` are both in `.gitignore` and will never be committed.

## Step 5: Build the Lambda Layer

Before deploying, you need to package the Python dependencies into a Lambda layer zip. The Lambdas run on ARM64 (Graviton2) for cost savings, so the layer must be built for that architecture even if your dev machine is x86_64 or Apple Silicon:

```bash
make package
```

This runs:
```bash
pip install \
  --platform manylinux2014_aarch64 \
  --only-binary=:all: \
  --implementation cp \
  --python-version 3.12 \
  -r requirements.txt \
  -t package/python/
```

The result is `lambda-layer.zip` (~21MB) in the project root. Terraform references this file when creating the Lambda layer. Rebuild it whenever you change `requirements.txt`.

## Step 6: Deploy

```bash
cd terraform

# Initialize Terraform (downloads providers and configures backend)
terraform init

# Preview what will be created
terraform plan

# Deploy (type "yes" when prompted)
terraform apply
```

Terraform will create:
- 2 DynamoDB tables (`govwin_sync_state`, `govwin_entity_mappings`)
- 4 Secrets Manager secrets (`govwin`, `govwin-tokens`, `hubspot`, `hubspot-webhook`)
- 10 Lambda functions + shared dependency layer (`govwin_orchestrator`, `govwin_worker`, `setup_hubspot`, `hubspot_webhook_receiver`, `submit_to_ace`, `update_in_ace`, `handle_ace_event`, `reconcile_pending`, `ui_extension_reads`, `ui_extension_writes`)
- 1 API Gateway HTTP API (multi-route: `/webhook`, `/ui-extension/*`)
- 2 EventBridge Scheduler schedules (`govwin_orchestrator` hourly, `reconcile_pending` periodic) plus 1 EventBridge rule on `aws.partnercentral-selling`
- 1 SNS topic (notifications)
- 8 SQS queues: 3 operational (`govwin-sync`, `ace-submit`, `ace-update`) plus 5 DLQs (one per operational queue, one project-wide, plus the reconcile-sweep DLQ)
- 1 customer-managed KMS CMK encrypting DynamoDB, SNS, and all SQS queues
- IAM roles and policies (least-privilege per Lambda)
- CloudWatch log groups (one per Lambda + API Gateway access logs)

## Step 7: Mark Opportunities for Sync

By default, only opportunities your BD team explicitly marks in GovWin IQ will sync to HubSpot. This is controlled by the `govwin_marked_version` variable (default: `"2.2"`).

**To mark an opportunity for sync:**
1. Open the opportunity in GovWin IQ
2. Click **"Add to Web Services Download"** on the opportunity detail page
3. The opportunity will be picked up on the next scheduled sync

**Alternative filtering modes** (set in `terraform.tfvars`):
- `govwin_saved_search_id = "12345"`; sync opps matching a GovWin saved search
- `govwin_bookmarked_only = true`; sync only bookmarked opps
- `govwin_marked_version = ""`; disable filtering, sync all opps (not recommended for production)

## Step 8: Verify Deployment

### Check HubSpot Setup

The `setup_hubspot` Lambda runs automatically during deployment and creates the custom properties (it does not create a pipeline). Verify in HubSpot:
- Go to **Settings > Properties > Deal properties**; you should see `govwin_*` properties
- Go to **Settings > Objects > Deals > Pipelines**; the **GovWin Pipeline** you prepared in Step 1b is where new deals will appear

### Trigger First Sync

The first scheduled sync will run automatically. To trigger it immediately, invoke the orchestrator Lambda directly:

```bash
aws lambda invoke --function-name govwin-hubspot-prod-govwin-orchestrator \
  --region us-east-1 /tmp/orch.json && cat /tmp/orch.json
```

The orchestrator handles token refresh, runs the configured discovery mode (marked / saved-search / bookmarked / date-range), and fans the resulting opportunity batches out to SQS. The worker Lambda then drains the queue.

### Monitor

- **Lambda console**: see invocation counts, errors, and durations for `govwin-hubspot-prod-govwin-orchestrator` and `govwin-hubspot-prod-govwin-worker`
- **CloudWatch Logs**: per-function log streams under `/aws/lambda/<function-name>`
- **SQS console**: backlog and DLQ depth for the sync, ACE submission, and ACE update queues, plus the reconcile-sweep DLQ
- **SNS Notifications**: summary email after each sync (if `notification_email` is set)

## Step 9: Wire up the AWS Partner Central submission half

The GovWin to HubSpot half is now running. To submit deals onward to AWS Partner Central via this project's own Selling-API client:

### 9a. Confirm AWS Partner Central prerequisites

```bash
# Confirm sandbox catalog access (should return an empty list, not AccessDenied)
aws partnercentral-selling list-opportunities --catalog Sandbox --region us-east-1

# Discover your Approved Solutions and pick one to set as ace_default_solution_id
aws partnercentral-selling list-solutions --catalog AWS --region us-east-1 \
  --query 'SolutionSummaries[].{Id:Id,Name:Name,Status:Status,Category:Category}'
```

If `list-solutions` returns nothing, register one in the Partner Central UI under **Sell -> My Solutions** before continuing.

### 9b. Create the HubSpot developer-platform app

HubSpot's developer-platform projects framework owns the webhook subscriptions and the Submit-to-AWS UI Extension card. **The repo already ships a complete `hubspot-app/` project, so you do NOT run `hs project create` — the project files already exist.** Authenticate the HubSpot CLI to your account, then upload:

```bash
npm install -g @hubspot/cli
hs init                   # opens a browser, authenticates against your HubSpot account
hs accounts use <your-portal-name>   # if you authenticated against multiple accounts
hs project upload         # uploads the bundled hubspot-app/ project
```

`hs init` requires browser-based OAuth and writes the resulting credentials to `~/.hubspot.config.yml`; this will not work in a fully headless environment. The project (`hubspot-app/`) is committed to the repo with the right scopes and webhook subscriptions pre-declared. Subscriptions cover `dealstage`, `amount`, `closedate`, `dealname`, `description`, `govwin_industry`, `govwin_aws_cosell_id`, and the full `govwin_ace_*` property family (delivery_model, partner_need, use_case, solution_id, opportunity_type, lifecycle_stage, sales_activities, aws_account_id, aws_products, competitor_name, national_security, closed_lost_reason, additional_comments, next_steps, related_opportunity_id, and the marketing block). See `hubspot-app/src/app/webhooks/webhooks-hsmeta.json` for the authoritative manifest. Subscriptions ship inactive; we activate them in step 9d after the API Gateway URL is known.

After upload, the **App ID** and **client secret** are visible in the HubSpot developer portal at:

> **Settings (gear icon) > Integrations > Connected Apps > [your app name] > Auth tab**

The client secret is shown only once; copy it immediately and put it in the next step's Terraform variable.

#### 9b.i Find your HubSpot pipeline stage internal IDs (`ace_trigger_stages`)

Webhooks for ACE submission fire when a HubSpot deal moves to one of the stages listed in `ace_trigger_stages`. HubSpot identifies stages by **numeric internal ID**, not by their visible label. The repo's default value (`submit_to_aws,submitted_to_aws`) is a label-style placeholder so a first-deploy plan/apply succeeds; production must override it with real numeric IDs.

To find them:

```bash
# Replace <PIPELINE_ID> with your GovWin Pipeline ID. Get pipeline IDs from:
#   curl -s -H "Authorization: Bearer $HUBSPOT_TOKEN" \
#     https://api.hubapi.com/crm/v3/pipelines/deals | jq '.results[] | {id, label}'

curl -s -H "Authorization: Bearer $HUBSPOT_TOKEN" \
  https://api.hubapi.com/crm/v3/pipelines/deals/<PIPELINE_ID>/stages \
  | jq '.results[] | {id: .id, label: .label}'
```

The `id` is what you want; it looks like `3590200042`. Pick the stage that BD will move deals to when they're ready to submit (typically labeled "Submit to AWS" or similar) and any subsequent stages where re-submission should be a no-op (typically "Submitted to AWS"). Set:

```hcl
ace_trigger_stages = "3590200042,3590200043"
```

If you skip this step, the webhook receiver will still receive HubSpot events but will never recognize a stage match, and no ACE submission will ever fire. Symptom: `hs project upload` succeeds and deals appear in HubSpot, but nothing arrives in AWS Partner Central.

#### 9b.ii Verify the Submit-to-AWS card renders on a deal record

`hs project upload` deploys three things in one go: the webhook subscriptions, the developer-platform app credentials (visible in the Auth tab), and the **Submit-to-AWS** UI Extension card that shows up on every deal record on the GovWin Pipeline. The card is what BD uses day-to-day; if it does not render, the rest of the integration is dark to them even when the backend is working.

To confirm it deployed:

1. In HubSpot, open any deal that is on the GovWin Pipeline (or create a test one with the **GovWin Pipeline** selected).
2. The right-hand sidebar (the "About" section) should list **Submit to AWS Partner Central** as one of the cards. If the card is collapsed, click to expand it.
3. The card shows a status badge ("Not submitted" for a fresh deal), an opportunity-type chip, and either a **Submit to AWS** or **Update opportunity in AWS** button. If those elements render, the card is working.

If the card does not appear:

- **Confirm the project uploaded.** Run `hs project list` and look for the project name (matches `hubspot-app/src/app/extensions/app.json`). The project should show as deployed with a recent timestamp.
- **Confirm the right portal.** `hs accounts list` shows which HubSpot portal is active. If you authenticated against multiple portals and uploaded to the wrong one, the card will be missing in the portal you are looking at and present in the other. Use `hs accounts use <portal>` to switch.
- **Confirm card placement.** UI Extension cards must be added manually to the deal record sidebar the first time. Go to **Settings -> Objects -> Deals -> Record customization -> Default view**, click **Customize the right sidebar**, and drag the **Submit to AWS Partner Central** card into the layout. Save. This is a one-time HubSpot quirk; once placed, the card stays for all deals.
- **Confirm permissions.** The HubSpot user who deployed the project needs **Super Admin** (or at minimum a role with developer-platform-projects access). If `hs project upload` succeeds for an underprivileged user, the card files upload but the card is not enabled. Re-run from a Super Admin account.
- **Confirm UI Extension Lambda reachability.** The card backs onto the `ui_extension_reads` / `ui_extension_writes` Lambdas via API Gateway. If the card renders the layout but shows "Failed to load" inside, the Lambda or its CORS or signature configuration is the problem; tail CloudWatch logs on those Lambdas while you reload the card to see the error.

### 9c. Set the Partner Central Terraform variables

```hcl
ace_catalog                   = "Sandbox"             # flip to "AWS" only after sandbox tests pass
ace_default_solution_id       = "S-1234567"           # from list-solutions output above
hubspot_webhook_app_id        = "12345678"            # from HubSpot dev portal
hubspot_webhook_client_secret = "client-secret-from-hubspot"
```

Then re-run `terraform apply`. The output `hubspot_webhook_target_url` is the public URL HubSpot should POST to.

### 9d. Activate the webhook subscriptions

Paste the `hubspot_webhook_target_url` Terraform output value into `hubspot-app/src/app/webhooks/webhooks-hsmeta.json` (replacing the `<api-id>` placeholder), confirm every subscription has `"active": true`, and deploy:

```bash
cd hubspot-app
hs project upload
```

The Dev Platform 2025.2+ manifest is the source of truth for subscription state. `hs project upload` is idempotent: subscriptions already active stay active. HubSpot starts delivering deal property changes to the API Gateway URL; the receiver Lambda validates the signature, routes events to the submit / update / audit destinations, and the `submit_to_ace` / `update_in_ace` Lambdas drain them. The `govwin_aws_cosell_id` audit subscription fires an SNS alert when the property changes from any source other than the integration token (e.g., a BD hand-edit).

### 9e. Smoke test

Run the sandbox smoke matrix (see [Testing in your own AWS account](testing-in-your-account.md#ace-sandbox-smoke-matrix-phase-41)) before flipping `ace_catalog` to `AWS`. Once green, change to production:

```hcl
ace_catalog = "AWS"
```

`terraform apply` rebuilds the IAM policy without the `Catalog: Sandbox` condition. Production smoke is a single low-stakes opportunity end-to-end.

## Updating

To update the integration after pulling new code:

```bash
cd terraform
terraform plan    # Review changes
terraform apply   # Apply updates
```

## Uninstalling

```bash
cd terraform
terraform destroy   # Removes all AWS resources
```

This will delete all AWS resources. HubSpot custom properties and deals created by the integration will remain in HubSpot and must be removed manually if desired.

## Troubleshooting

### Common GovWin / HubSpot issues

| Issue | Cause | Solution |
|---|---|---|
| GovWin auth fails (401) | Bad credentials or locked account | Verify credentials; account locks for 30 min after 5 failed attempts |
| Rate limit errors (403) | Exceeded 4,000 calls/hour | The integration handles this automatically; if persistent, increase `sync_schedule` interval |
| HubSpot 429 errors | Exceeded 100 req/10s | Built-in backoff handles this; check for other integrations consuming rate limit |
| Missing deals in HubSpot | Opportunity type filtered out | Check `govwin_opp_types` variable |
| Worker batch timeout | Very large initial sync | Increase `worker_concurrency` (Lambda reservedConcurrency) or run initial sync in stages |

### Common AWS Partner Central / webhook issues

| Issue | Cause | Solution |
|---|---|---|
| Webhook receiver returns 401 ("invalid signature") | `hubspot_webhook_client_secret` does not match the secret currently set on the developer-platform app, or the request body was modified by a proxy | Re-copy the client secret from the HubSpot developer portal (**Settings > Integrations > Connected Apps > [app] > Auth**) into the Terraform variable, `terraform apply`, and have HubSpot retry. Confirm no API Gateway request transformations are altering the body. |
| Webhook receiver returns 401 ("expired timestamp") | Clock skew on the sender or replay attempt | HubSpot enforces a 5-minute window per the `X-HubSpot-Request-Timestamp` header. If your traffic is from a real HubSpot delivery and not a replay, this is a HubSpot-side clock issue and resolves itself. |
| Submission silently never fires | `ace_trigger_stages` does not match the actual numeric stage IDs of your "Submit to AWS" stage | Re-verify with the API call in step 9b.i; replace label-style placeholders with numeric IDs (`3590200042` shape). |
| `submit_to_ace` returns `ValidationException` | Required ACE field missing or out of range. Common offenders: `Project.Title` length, `Customer.Account.Industry`, `LifeCycle.NextSteps` length, `ExpectedCustomerSpend.Amount` non-positive | Inspect the Lambda log entry; the boto3 error message names the offending field. Fix the source data in HubSpot (BD must update the deal) and re-trigger by toggling the deal stage off and back on. |
| `submit_to_ace` returns `ConflictException` | Optimistic-locking failure on `UpdateOpportunity` (concurrent edits) | The client's tenacity retry refetches `LastModifiedDate` and retries; if it persists past 5 attempts the message lands in the DLQ. Inspect DynamoDB `ACE#{govwin_id}` to clear stale state if needed. |
| `submit_to_ace` returns `ResourceNotFoundException` immediately after `CreateOpportunity` | Eventual consistency on the Partner Central side: the `Id` returned by `CreateOpportunity` is not yet readable by `AssociateOpportunity` / `StartEngagementFromOpportunityTask` | The client retries up to 5 times with exponential backoff. If still failing, AWS may be experiencing a regional issue; check the AWS Health dashboard. |
| `submit_to_ace` returns `ThrottlingException` | Burst above the 1 write/sec quota or 10K writes/24h | The token bucket and tenacity retry handle short bursts. For sustained throttling, reduce SQS `batch_size` or stagger BD's stage transitions. |
| `hs project upload` is rejected as unauthenticated | `hs account auth` token expired | Re-run `hs account auth` against the HubSpot account that owns the developer project, then re-upload. |
| Webhook delivers but `submit_to_ace` never invoked | SQS event-source mapping disabled, or `webhooks-hsmeta.json` still has `active=false` | Check the SQS queue's "ApproximateNumberOfMessages" metric. If zero, the webhook receiver isn't enqueueing — verify in CloudWatch logs. If non-zero with no Lambda invocations, re-enable the event-source mapping. If subscriptions are `active=false`, flip them in the manifest and re-run `hs project upload`. |

### Checking logs

```bash
# Tail the orchestrator
aws logs tail /aws/lambda/govwin-hubspot-prod-govwin-orchestrator --follow

# Tail the worker
aws logs tail /aws/lambda/govwin-hubspot-prod-govwin-worker --follow

# Tail the webhook receiver
aws logs tail /aws/lambda/govwin-hubspot-prod-hubspot-webhook-receiver --follow

# Tail the ACE submitter
aws logs tail /aws/lambda/govwin-hubspot-prod-submit-to-ace --follow

# DLQ depth (look here first when an event "disappears")
aws sqs get-queue-attributes \
  --queue-url $(terraform -chdir=terraform output -raw dlq_url) \
  --attribute-names ApproximateNumberOfMessages
```

For deeper operational guidance (alarm names, stuck-deal recovery, fault-injection), see [docs/operations.md](operations.md).
