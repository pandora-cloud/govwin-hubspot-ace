# ACE Integration Guide

How GovWin opportunities flow into AWS Partner Central via this integration's direct AWS Partner Central Selling API client. No third-party connector dependency.

## Overview

When a HubSpot deal stage transitions to **Submit to AWS**, a HubSpot webhook fires, the integration validates the signature, enqueues the event onto SQS, and a Lambda runs the three-call submission against the Partner Central Selling API:

```
CreateOpportunity -> AssociateOpportunity -> StartEngagementFromOpportunityTask
```

After submission, the opportunity is locked from edits while AWS reviews. EventBridge events on `aws.partnercentral-selling` flow into a separate handler that mirrors AWS-side state changes back to the HubSpot deal stage.

## Prerequisites

- This integration deployed end-to-end (see [Deployment Guide](deployment-guide.md))
- An AWS Partner Central account in good standing
- At least one Approved Solution registered in Partner Central. Discover via:
  ```
  aws partnercentral-selling list-solutions --catalog AWS --region us-east-1
  ```
  Set `ace_default_solution_id` in `terraform.tfvars` to the chosen `S-...` ID.
- HubSpot developer-platform app (2025.2+) uploaded from the bundled `hubspot-app/` project; `appId` and `clientSecret` provided as Terraform variables.

## End-to-end workflow

### 1. Opportunity syncs from GovWin

The hourly orchestrator Lambda (triggered by EventBridge Scheduler) picks up marked opportunities and fans batches out to the worker via SQS. The worker writes them into HubSpot. The deal lands in the **GovWin Pipeline** with around 30 properties pre-populated, including most of the fields ACE requires.

### 2. BD reviews the deal and fills the manual fields on the Submit card

Three fields cannot be reliably auto-populated from GovWin and must be filled on the Submit card before submission:

#### Partner Need from AWS (`govwin_ace_partner_need`)

One or more of (semicolon-separated):

- `Co-Sell - Architectural Validation`
- `Co-Sell - Business Presentation`
- `Co-Sell - Competitive Information`
- `Co-Sell - Pricing Assistance`
- `Co-Sell - Technical Consultation`
- `Co-Sell - Total Cost of Ownership Evaluation`
- `Co-Sell - Deal Support`
- `Co-Sell - Support for Public Tender / RFx`

#### Delivery Model (`govwin_ace_delivery_model`)

One or more of (semicolon-separated for multiple):

- `SaaS or PaaS`
- `BYOL or AMI`
- `Managed Services`
- `Professional Services`
- `Resell`
- `Other`

#### Customer Use Case (`govwin_ace_use_case`)

The primary AWS use case for the customer. Pick one value from the AWS-published Customer Use Case enum (38 service categories such as Migration, Data Analytics, Machine Learning, Storage, and so on).

The Lambda validates all three fields against the AWS-published enums and rejects deals with invalid values before any API call.

#### AWS Solution (conditional)

The Partner Central Solution to associate with the opportunity. The card defaults this from the `ace_default_solution_id` Terraform variable and the BD user can override per-deal. In the AWS production catalog a Solution is required; in the Sandbox catalog it is optional.

### 3. Submit the deal from the card

Click **Submit to AWS** on the Submit card. The card advances the deal stage automatically; BD does not drag pipeline stages by hand. The stage that the integration moves the deal to (and recognizes for re-submission as a no-op) is configurable via `ace_trigger_stages`.

### 4. The integration submits automatically

What happens, in order:

1. HubSpot webhook fires the `dealstage` property change.
2. The receiver Lambda validates `X-HubSpot-Signature-v3` and enqueues the event onto the submit SQS queue.
3. `submit_to_ace` Lambda fetches the deal, atomically reserves a `ClientToken` in DynamoDB, and calls:
   - `CreateOpportunity` -> persists the AWS opp ID and `LastModifiedDate`
   - `AssociateOpportunity` -> associates the configured Solution
   - `StartEngagementFromOpportunityTask` -> async submission task with its own persisted ClientToken
4. The opportunity is now locked from edits in AWS Partner Central.

If any step fails, SQS redelivery resumes from the last persisted step. Permanent errors (`ValidationException`, `AccessDeniedException`) are dropped from the batch instead of looping; transient errors (`ThrottlingException`, `InternalServerException`, `ConflictException`) are reported as batch failures and SQS redelivers them.

### 5. AWS reviews

Typical timeline: 24-72 hours. Review status flows back via EventBridge:

| AWS event | Mapped HubSpot stage |
|---|---|
| `Opportunity Updated` with `LifeCycle.Stage = Closed Lost` | `Closed Lost` (mirrors `ClosedLostReason`) |
| `Opportunity Updated` with `LifeCycle.Stage = Launched` | `Closed Won` |
| `Opportunity Updated` with `LifeCycle.ReviewStatus = Approved` | `approved_by_aws` |
| `Opportunity Updated` with `LifeCycle.ReviewStatus = Action Required` | `action_required` |
| `Opportunity Updated` with `LifeCycle.ReviewStatus = Rejected` | `closedlost` |
| `Engagement Invitation Accepted` | `approved_by_aws` |
| `Engagement Invitation Rejected` | `closedlost` |
| `Engagement Invitation Expired` | `closedlost` |

A terminal `LifeCycle.Stage` (Closed Lost or Launched) takes precedence over `ReviewStatus`. A closed or launched opportunity keeps `ReviewStatus = Approved`, so mapping by review status alone would revert a just-closed deal back to `approved_by_aws`; the stage check wins so the deal stays terminal. When AWS reports Closed Lost, the handler also mirrors `ClosedLostReason` onto the deal.

The handler dedups on EventBridge `id` with a 24-hour TTL via a conditional `put_item` so duplicate deliveries are no-ops.

### 6. Updates after submission

If the deal's `amount`, `closedate`, `dealname`, or `description` changes after submission, the receiver routes the property-change event to the update queue. `update_in_ace` calls `UpdateOpportunity` using the `LastModifiedDate` we persisted on the prior write. On `ConflictException`, the call refetches and retries up to three times.

Edits to any of these fields, including Customer Use Case, Delivery Model, and Partner Need from AWS, are deferred while AWS is actively reviewing (`ReviewStatus = Submitted`, `In review`, or `Rejected`); the `reconcile_pending` Lambda sweeps the parked edits on its schedule and replays them once review exits. See [operations.md "Deferred edits and review-status reconciliation"](operations.md) for the operator view of that flow.

## Sandbox vs production catalog

The integration defaults `ace_catalog = Sandbox`. Production deployments must explicitly flip this to `AWS` in `terraform.tfvars`:

```hcl
ace_catalog = "AWS"
```

The IAM policy is the real safety net: when `ace_catalog = Sandbox`, the policy is conditional on `partnercentral:Catalog: Sandbox`, so even if code accidentally passes `Catalog: "AWS"`, the API rejects with `AccessDeniedException`.

The Sandbox catalog mirrors production validation but is isolated. Run the sandbox smoke matrix (see [Testing in your own AWS account](testing-in-your-account.md#ace-sandbox-smoke-matrix-phase-41)) before flipping.

## Field mapping: GovWin -> HubSpot -> ACE

| ACE field | HubSpot property | GovWin source | Notes |
|---|---|---|---|
| `Project.Title` | `dealname` | `title` | Auto |
| `Project.ExpectedCustomerSpend[].Amount` | `amount` | Two-stage transform: GovWin `oppValue` x 1000 lands as HubSpot `amount`; the mapper then divides by 12 to send a monthly figure to ACE | Auto |
| `Project.CustomerBusinessProblem` | `description` | `description` | Auto (sanitized) |
| `Project.CustomerUseCase` | `govwin_ace_use_case` |; | **Manual** (AWS-published enum) |
| `Project.DeliveryModels[]` | `govwin_ace_delivery_model` |; | **Manual** |
| `LifeCycle.TargetCloseDate` | `closedate` | `pAwardDateTo` | Auto |
| `LifeCycle.NextSteps` |; |; | Reserved for future use |
| `Customer.Account.CompanyName` | Company `name` | `govEntity.title` | Auto via association |
| `Customer.Account.Industry` | `govwin_industry` | NAICS mapped to AWS | Auto |
| `Customer.Account.Address.CountryCode` | `govwin_country` | `country` | Auto (defaulted to US) |
| `OpportunityType` | `govwin_ace_opportunity_type` | Default `Net New Business` | Auto |
| `Origin` |; |; | Auto (`Partner Referral`) |
| `PrimaryNeedsFromAws[]` | `govwin_ace_partner_need` |; | **Manual** |
| `PartnerOpportunityIdentifier` | `govwin_opp_id` | GovWin opp ID | Auto |
| Solution association | `govwin_ace_solution_id` |; | **Manual** (defaulted to `ace_default_solution_id`) |
| `Catalog` |; |; | From `ACE_CATALOG` env (Sandbox or AWS) |
| `ClientToken` |; |; | UUID, persisted in DynamoDB for idempotency |

## Idempotency notes

- Each deal gets one `ClientToken` for `CreateOpportunity` and a separate one for `StartEngagementFromOpportunityTask`. Both are reserved atomically via DynamoDB conditional writes so concurrent SQS retries cannot mint duplicate ACE opportunities.
- Resume-from-step: after each successful API call we update the DynamoDB mapping. A retried message reloads the mapping and skips already-completed steps.
- AssociateOpportunity returns `ConflictException` for a duplicate; we treat that as success and continue, with a WARN log so an operator notices if the intended Solution was changed mid-retry.

## Operational references

- [AWS Partner Central Selling API best practices](reference/aws-partner-central/best-practices.md)
- [CreateOpportunity field reference](reference/aws-partner-central/api-create-opportunity.md)
- [StartEngagementFromOpportunityTask reference](reference/aws-partner-central/api-start-engagement-from-opportunity.md)
- [EventBridge events from `aws.partnercentral-selling`](reference/aws-partner-central/eventbridge-events.md)
- [Sandbox catalog setup](reference/aws-partner-central/sandbox-testing.md)
- [API quotas and limits](reference/aws-partner-central/quotas-and-limits.md)
- [HubSpot private-app webhooks](reference/hubspot/private-app-webhooks.md)
