# Runbook: relocate pipeline KMS key + flip SNS/DDB onto the CMK

This is a one-time apply runbook for the `feat(security): hoist pipeline KMS
key into its own module + flip SNS topic and DDB tables onto the CMK`
change. Skip if your environment is already on the new layout.

## What changed

Terraform refactor:

* New `terraform/modules/kms/` owns `aws_kms_key.pipeline` and its alias.
* `terraform/modules/ace/kms.tf` deleted; the `ace` module now consumes
  `var.kms_key_arn` for SQS encryption and IAM scoping.
* `terraform/modules/monitoring/main.tf`: SNS topic and catch-all DLQ
  now encrypt with the pipeline CMK instead of `alias/aws/sns` /
  `sqs_managed_sse`.
* `terraform/modules/dynamodb/main.tf`: both tables now encrypt with
  `kms_key_arn = var.kms_key_arn` instead of an AWS-owned key.

## Apply sequence

```bash
cd terraform
terraform init       # picks up the new kms module
terraform plan       # expect ~6 resource updates + 1 state move below
```

### Before applying: state-move the existing key

Without this, Terraform will plan to DESTROY the existing
`module.ace.aws_kms_key.pipeline` (which would break the SQS queues
that currently reference it) and CREATE
`module.kms.aws_kms_key.pipeline`. The state move makes Terraform
recognize the existing key under its new module address.

```bash
terraform state mv 'module.ace.aws_kms_key.pipeline' \
                   'module.kms.aws_kms_key.pipeline'

terraform state mv 'module.ace.aws_kms_alias.pipeline' \
                   'module.kms.aws_kms_alias.pipeline'

terraform state mv 'module.ace.data.aws_iam_policy_document.pipeline_kms' \
                   'module.kms.data.aws_iam_policy_document.pipeline_kms'
```

The data source move is optional (data sources are refreshed on every
plan) but keeps the state cleanly organized.

Re-plan after the moves:

```bash
terraform plan
```

Expected planned changes (none destructive):

* `aws_dynamodb_table.sync_state`: in-place update of
  `server_side_encryption.kms_key_arn`. DDB allows live CMK swaps;
  existing rows stay readable on the AWS-owned key indefinitely (DDB
  does not re-encrypt historical items, but the client transparently
  decrypts with whichever key the item was written under).
* `aws_dynamodb_table.entity_mappings`: same in-place update.
* `aws_sns_topic.sync_notifications`: in-place update of
  `kms_master_key_id`. SNS allows live CMK swaps.
* `aws_sqs_queue.dlq` (the catch-all DLQ in monitoring): in-place
  update from `sqs_managed_sse_enabled=true` to
  `kms_master_key_id=<cmk>`. SQS allows live CMK swaps.
* `module.kms.aws_kms_key.pipeline`: no-op once the state move
  completes. The KMS key policy itself is unchanged from the previous
  commit's prep grants.

Apply:

```bash
terraform apply
```

### After apply

1. **SNS subscription re-confirmation**: SNS does NOT re-issue a
   subscription confirmation when only the topic encryption changes.
   The existing email subscription stays Subscribed. If the operator
   reports they stopped receiving alerts, check
   `aws sns list-subscriptions-by-topic` and resubscribe if needed.

2. **DDB read after write smoke test**: write a test row and read it
   back via `aws dynamodb scan --table-name <prefix>-sync-state
   --max-items 5` to confirm the Lambda execution role can still
   decrypt under the CMK. The `kms:Decrypt` grant on the Lambda role
   already covers this through the account-root statement in the key
   policy.

3. **Fire a deliberate test alert** by hand-editing the
   `govwin_aws_cosell_id` property on any deal (introduced in the
   item 1 audit alert). Confirm the SNS email arrives. This validates
   the SNS topic's CMK encryption end-to-end.

## Rollback

If something fails:

```bash
terraform state mv 'module.kms.aws_kms_key.pipeline' \
                   'module.ace.aws_kms_key.pipeline'

terraform state mv 'module.kms.aws_kms_alias.pipeline' \
                   'module.ace.aws_kms_alias.pipeline'

git revert <this-commit-sha>
terraform plan   # should show no changes
```

The KMS key itself is preserved by the state moves on both sides, so a
rollback is purely a Terraform-state operation; no AWS-side data is
touched.
