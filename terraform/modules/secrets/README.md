# `terraform/modules/secrets`

Provisions three of the four AWS Secrets Manager secrets the integration uses to talk to external services. The HubSpot webhook-signing secret (`${name_prefix}/hubspot-webhook`) is created separately in `modules/ace` because it ships alongside the receiver Lambda that consumes it.

## What it creates

- `${name_prefix}/govwin`: GovWin WSAPI credentials (`client_id`, `client_secret`, `username`, `password`).
- `${name_prefix}/govwin-tokens`: GovWin OAuth access and refresh tokens, refreshed in place by the orchestrator Lambda on each scheduled run.
- `${name_prefix}/hubspot`: HubSpot Private App token (or Service Key).

All secrets are CMK-encrypted under the pipeline key; access is granted to the shared Lambda execution role with per-secret-arn IAM scoping.

## Required inputs

`name_prefix`, the four GovWin credential values (`sensitive=true`), `hubspot_private_app_token`. The HubSpot webhook signing secret is taken as input by `modules/ace`, not this module.

## Outputs

Secret ARNs and names for `modules/lambda` and `modules/ace` to grant per-secret IAM access. ADR [0007](../../../docs/decisions/0007-dynamodb-and-secrets-manager.md) records the choice of Secrets Manager over SSM Parameter Store.

## Depends on

`modules/kms`. Applies before `modules/lambda`.
