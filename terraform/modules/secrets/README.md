# `terraform/modules/secrets`

Provisions the three AWS Secrets Manager secrets the integration uses to talk to external services.

## What it creates

- `${name_prefix}/govwin`: GovWin WSAPI credentials (`client_id`, `client_secret`, `username`, `password`).
- `${name_prefix}/govwin-tokens`: GovWin OAuth access and refresh tokens, refreshed in place by the orchestrator Lambda on each scheduled run.
- `${name_prefix}/hubspot`: HubSpot Private App token (or Service Key).
- `${name_prefix}/hubspot-webhook`: HubSpot developer-platform app's webhook signing secret, used by the receiver Lambda to validate `X-HubSpot-Signature-v3`.

All secrets are CMK-encrypted under the pipeline key; access is granted to the shared Lambda execution role with per-secret-arn IAM scoping.

## Required inputs

`name_prefix`, the four GovWin credential values (`sensitive=true`), `hubspot_private_app_token`, `hubspot_webhook_client_secret`, `kms_key_arn`.

## Outputs

Secret ARNs and names for `modules/lambda` and `modules/ace` to grant per-secret IAM access. ADR [0007](../../../docs/decisions/0007-dynamodb-and-secrets-manager.md) records the choice of Secrets Manager over SSM Parameter Store.

## Depends on

`modules/kms`. Applies before `modules/lambda`.
