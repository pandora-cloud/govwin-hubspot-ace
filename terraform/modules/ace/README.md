# `terraform/modules/ace`

Provisions the HubSpot-to-AWS-Partner-Central submission half of the pipeline.

## What it creates

- Three Lambda functions: `submit_to_ace`, `update_in_ace`, `handle_ace_event`
- Two UI Extension Lambdas behind API Gateway: `ui_extension_reads` (GET endpoints), `ui_extension_writes` (POST endpoints)
- The HubSpot webhook receiver Lambda + its API Gateway HTTP API
- Four SQS queues with DLQs: ACE submit, ACE update, webhook submit-route, webhook update-route
- Scheduler-driven `reconcile_pending` Lambda to retry deferred edits
- EventBridge rule subscribing `handle_ace_event` to `aws.partnercentral-selling`
- IAM execution roles scoped to the catalog (Sandbox or AWS) per `ace_catalog`

## Required inputs

`name_prefix`, `aws_region`, `kms_key_arn` (from `modules/kms`), `sync_state_table_*` and `entity_mappings_table_*` (from `modules/dynamodb`), `*_secret_*` (from `modules/secrets`), `sns_topic_arn` (from `modules/monitoring`), `ace_catalog`, `ace_default_solution_id`, `ace_trigger_stages`, the HubSpot developer-platform `hubspot_webhook_app_id` and `hubspot_webhook_client_secret`.

## Outputs

`hubspot_webhook_target_url` (paste into the HubSpot developer project's webhook config), the seven Lambda ARNs and four queue URLs for `modules/monitoring` to attach alarms to.

## Depends on

`modules/kms`, `modules/dynamodb`, `modules/secrets`, `modules/monitoring`. Apply order is enforced by Terraform's dependency graph; this module is the deepest leaf and applies last in a fresh deploy.
