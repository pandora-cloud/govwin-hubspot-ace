# EventBridge events from `aws.partnercentral-selling`

> Snapshot from https://docs.aws.amazon.com/partner-central/latest/APIReference/selling-api-events.html captured 2026-04-28.

## Why we use these

The Selling API emits events through Amazon EventBridge whenever an opportunity, engagement, or invitation changes. This is the cleanest way to keep HubSpot in sync with AWS-side state changes (e.g. AWS approves the deal → update `dealstage` in HubSpot to "Closed Won" prep, or AWS rejects → update with the rejection reason and notify the deal owner).

The `aws.partnercentral-selling` source publishes to the partner's **default** EventBridge bus in `us-east-1`. No third-party event bus configuration needed.

## All ten event types

AWS publishes ten detail-types from `aws.partnercentral-selling`. We subscribe to eight; two are deliberately not subscribed (rationale in the rightmost column). The current subscription set is enforced at deploy time by the EventBridge rules in `terraform/modules/ace/eventbridge.tf` and at test time by the drift guard in `tests/unit/test_handle_ace_event_lambda.py`, so the matrix below cannot silently fall out of sync with what runs in production.

| Event detail-type | Trigger | Action we take | Subscribed? |
|---|---|---|---|
| `Opportunity Created` | New opportunity created (by us or AWS) | If created by AWS (referral), call `GetOpportunity` and create matching HubSpot deal | Yes |
| `Opportunity Updated` | Existing opportunity changed | Call `GetOpportunity`, diff into HubSpot, advance the deal stage if review status changed | Yes |
| `Engagement Invitation Created` | AWS sent us a referral, OR our `StartEngagement` task created our outgoing invitation | If `participantType=Receiver`: notify deal owner of incoming AWS referral. If `Sender`: persist invitation ID in DynamoDB | Yes |
| `Engagement Invitation Accepted` | The other side accepted the invitation | Update HubSpot deal stage to reflect AWS engagement acceptance | Yes |
| `Engagement Invitation Rejected` | The other side declined | Update HubSpot deal stage to "Closed Lost" with rejection reason | Yes |
| `Engagement Invitation Expired` | 15 days elapsed without action | Notify the deal owner via SNS | Yes |
| `Engagement Created` | New engagement was created | Log the `aws_opp -> engagement` linkage to CloudWatch for audit. Full ACE# row persistence is deferred until a reverse `AWSOPP#` index exists in `src.sync.state`. | Yes |
| `Engagement Resource Snapshot Created` | A new revision of opportunity data was snapshotted (AWS-side amendment, customer linkage change, etc.) | Treat like `Opportunity Updated`: call `GetOpportunity`, diff into HubSpot. Catches changes that would otherwise only surface on the next hourly sync. | Yes |
| `Engagement Member Added` | A new member joined an engagement | Informational only; AWS sends this whenever any participant adds or removes a member. We have no per-member behavior in HubSpot. | **No** (intentionally filtered at the EventBridge rule pattern; subscribing would cost invocation per noop) |
| `Engagement Updated` | Engagement metadata changed | Informational only; we already get the relevant changes through the more specific detail-types above. | **No** (intentionally filtered at the EventBridge rule pattern) |

The dispatch in `src/lambdas/handle_ace_event.py` is keyed on the same exact strings; see the module-level `SUBSCRIBED_DETAIL_TYPES` and `UNSUBSCRIBED_DETAIL_TYPES` frozensets.

## Sample event: `Opportunity Updated`

```json
{
  "version": "1",
  "id": "01234567-0123-0123-0123-0123456789ab",
  "source": "aws.partnercentral-selling",
  "detail-type": "Opportunity Updated",
  "time": "2026-04-28T15:23:45Z",
  "region": "us-east-1",
  "account": "123456789012",
  "detail": {
    "schemaVersion": "1.0",
    "catalog": "AWS",
    "opportunity": {
      "identifier": "O123456789012345"
    }
  }
}
```

The detail payload carries only the opportunity ID. Per AWS best practice, batch a `GetOpportunity` lookup rather than calling on every event.

## Sample event: `Engagement Invitation Created` (incoming AWS referral)

```json
{
  "version": "0",
  "id": "01234567-0123-0123-0123-0123456789ab",
  "source": "aws.partnercentral-selling",
  "detail-type": "Engagement Invitation Created",
  "time": "2026-04-28T12:34:56Z",
  "region": "us-east-1",
  "account": "123456789012",
  "resources": [
    "arn:aws:partnercentral:us-east-1::catalog/AWS/engagement-invitation/engi-v7p8z56whnauo"
  ],
  "detail": {
    "catalog": "AWS",
    "engagementInvitation": {
      "arn": "arn:aws:partnercentral:us-east-1::catalog/AWS/engagement-invitation/engi-v7p8z56whnauo",
      "id": "engi-v7p8z56whnauo",
      "engagementId": "eng-12345678901234",
      "senderAccountId": "AWS",
      "receiverAccountId": "123456789012",
      "senderCompanyName": "AWS",
      "expirationDate": "2026-05-13T00:00:00Z",
      "participantType": "Receiver",
      "payloadType": "OpportunityInvitation"
    }
  }
}
```

`participantType` is the most important filter:

- `Receiver` = AWS is referring an opportunity to us — we should accept it via `StartEngagementByAcceptingInvitationTask` (after BD approval in HubSpot)
- `Sender` = we're the partner originating the engagement — the invitation ID is the one our `StartEngagementFromOpportunityTask` produced

## EventBridge rule patterns we'll use

### Catch all relevant events for catalog=AWS

```json
{
  "source": ["aws.partnercentral-selling"],
  "detail": {"catalog": ["AWS"]}
}
```

### Just opportunity changes (most common case)

```json
{
  "source": ["aws.partnercentral-selling"],
  "detail-type": ["Opportunity Created", "Opportunity Updated"],
  "detail": {"catalog": ["AWS"]}
}
```

### Just incoming AWS referrals (Receiver perspective)

```json
{
  "source": ["aws.partnercentral-selling"],
  "detail-type": ["Engagement Invitation Created"],
  "detail": {
    "catalog": ["AWS"],
    "engagementInvitation": {
      "participantType": ["Receiver"]
    }
  }
}
```

## Idempotency requirement

EventBridge can deliver duplicates. Handler must be idempotent. Approach:

1. Use the event `id` field as a dedup key in DynamoDB (TTL of 24 hours)
2. On each invocation, check the dedup key; if seen, return success without doing the work
3. Otherwise process and write the dedup key

This pattern matches the existing dedup logic in `src/sync/dedup.py` for GovWin updates.

## IAM scope to receive events

The EventBridge rule itself only needs `events:PutRule` and `events:PutTargets`. The Lambda target needs the standard EventBridge invoke permission (handled via `aws_lambda_permission` resource in Terraform).

## What direct EventBridge subscriptions enable

Unlike a fixed-webhook third-party connector, with direct EventBridge subscriptions we can:

- React to AWS-originated referrals (the partner-Receiver flow)
- See engagement member changes (multi-partner deals)
- Detect snapshot revisions (AWS edited the opportunity on their end)
- Filter and route events with native EventBridge rules instead of being limited to a fixed set of vendor-exposed webhooks
