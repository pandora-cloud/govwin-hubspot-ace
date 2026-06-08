# `terraform/modules/ace`

Provisions the HubSpot-to-AWS-Partner-Central submission half of the pipeline.

## What it creates

- Seven Lambda functions:
  - The three core async-pipeline handlers: `submit_to_ace`, `update_in_ace`, `handle_ace_event`
  - The HubSpot webhook receiver: `hubspot_webhook_receiver`
  - The two UI Extension Lambdas behind API Gateway: `ui_extension_reads` (GET endpoints) and `ui_extension_writes` (POST endpoints)
  - The scheduler-driven reconciliation Lambda: `reconcile_pending`
- One API Gateway HTTP API serving both the webhook receiver and the UI Extension routes
- The `${name_prefix}/hubspot-webhook` Secrets Manager secret holding the HubSpot developer-platform app's webhook signing secret (kept here, not in `modules/secrets`, because it is consumed only by the receiver Lambda in this module)
- Five SQS queues:
  - Two operational queues: `${name_prefix}-ace-submit` and `${name_prefix}-ace-update` (the receiver writes directly into these based on which property changed; no separate webhook-route queue)
  - Three DLQs: a DLQ per operational queue plus the `reconcile_sweep_dlq` for the scheduler-driven reconciliation Lambda
- EventBridge rule subscribing `handle_ace_event` to `aws.partnercentral-selling` and an EventBridge Scheduler schedule firing `reconcile_pending` periodically
- IAM execution roles scoped to the catalog (Sandbox or AWS) per `ace_catalog` so the IAM policy itself denies cross-catalog calls

## Required inputs

`name_prefix`, `aws_region`, `kms_key_arn` (from `modules/kms`), `sync_state_table_*` and `entity_mappings_table_*` (from `modules/dynamodb`), `*_secret_*` (from `modules/secrets`), `sns_topic_arn` (from `modules/monitoring`), `ace_catalog`, `ace_default_solution_id`, `ace_trigger_stages`, the HubSpot developer-platform `hubspot_webhook_app_id` and `hubspot_webhook_client_secret`.

## Outputs

`webhook_target_url` (paste into the HubSpot developer project's webhook config), the UI Extension base URL and per-route URLs, the seven Lambda ARNs, the two operational queue URLs, the two ACE DLQ URLs/names, and the webhook-signing secret ARN. `modules/monitoring` consumes the queue + Lambda outputs to attach alarms.

## Depends on

`modules/kms`, `modules/dynamodb`, `modules/secrets`, `modules/monitoring`. Apply order is enforced by Terraform's dependency graph; this module is the deepest leaf and applies last in a fresh deploy.
