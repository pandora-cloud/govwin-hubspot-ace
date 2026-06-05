# `terraform/modules/lambda`

Provisions shared Lambda runtime infrastructure: the layer that carries all Python dependencies, the execution role used by every Lambda function in the pipeline, and the `setup_hubspot` one-time Lambda that creates HubSpot custom properties and seeds dynamic option sets.

## What it creates

- One Lambda layer (`${name_prefix}-deps`) built from `lambda-layer.zip` at the repo root. ARM64 (Graviton2) per ADR 0010. Required Python version 3.12.
- One shared IAM execution role (`${name_prefix}-lambda-role`) used by every Lambda in `modules/govwin_sync` and `modules/ace`. Scoped to the project's resources via `${name_prefix}-*` ARN patterns.
- The `setup_hubspot` Lambda + a `terraform-managed null_resource` that invokes it once per deploy to ensure HubSpot custom properties match what's declared in `src/hubspot/properties.py`.
- CloudWatch log groups for every Lambda with the configured retention.

## Required inputs

`name_prefix`, `aws_region`, `aws_profile`, the DDB table names/ARNs and secret ARNs/names from upstream modules, `ace_catalog` (drives whether `setup_hubspot` seeds the solution dropdown from the Sandbox or production Partner Central catalog), `log_retention_days`.

## Outputs

`lambda_role_arn`, `lambda_layer_arn`, `setup_hubspot_function_name` (consumed by `govwin_sync` and `ace`).

## Depends on

`modules/dynamodb`, `modules/secrets`, `modules/monitoring`. Applies after all three; before `modules/govwin_sync` and `modules/ace`.
