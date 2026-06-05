# `terraform/modules/kms`

Provisions the cross-cutting customer-managed KMS key (CMK) that encrypts every at-rest data store in the pipeline under one auditable key.

## What it creates

- One KMS key with a key policy that grants `kms:Encrypt`/`Decrypt`/`GenerateDataKey` to the AWS services that need it: SQS (operational queues + DLQs), SNS (notification topic), EventBridge (Scheduler + partner-central rule), DynamoDB (via service condition), CloudWatch Logs (scoped by EncryptionContext to the project's log group prefix).
- One key alias `alias/${name_prefix}-pipeline`.

Mutating use of the key requires the principal to be in the same AWS account AND tagged `Application=${name_prefix}` for administrative actions; see `terraform/bootstrap/deployer_role.tf` for the deployer-side scoping.

## Why this module exists separately

Originally the key lived inside `modules/ace`, but `ace` consumes outputs from `modules/monitoring` and `modules/dynamodb` (SNS topic ARN, table ARNs), which made it impossible for those two modules to consume the key without a dependency cycle. Hoisting the key into its own leaf module lets all three downstream modules encrypt at-rest data with one auditable key and no circular wiring.

## Required inputs

`name_prefix`, `aws_region`.

## Outputs

`kms_key_arn` and `kms_key_id` (consumed by `dynamodb`, `monitoring`, `ace`).

## Depends on

Nothing. This module is a leaf; apply it first.
