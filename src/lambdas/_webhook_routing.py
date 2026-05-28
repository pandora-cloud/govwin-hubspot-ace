"""Single source of truth for HubSpot webhook property routing.

The deploy-time subscription set is declared by the HubSpot Dev Platform
project at ``hubspot-app/src/app/webhooks/webhooks-hsmeta.json`` and
deployed via ``hs project upload``. The request-time receiver Lambda
(``hubspot_webhook_receiver.py``) imports the constants here to route
inbound events between the submit / update / audit SQS queues. Adding
a new property requires both: declare it in ``webhooks-hsmeta.json``
AND list it in the matching set here, otherwise the receiver drops
the event with no route match.
"""

from __future__ import annotations

# Property whose change should trigger initial ACE submission. Currently
# the Lambda only triggers when the change matches one of the configured
# stage internal IDs (see ACE_TRIGGER_STAGES env var), but we still
# subscribe to every dealstage change.
SUBMIT_TRIGGER_PROPERTY: str = "dealstage"

# Properties whose change should trigger an UpdateOpportunity call to AWS.
# These are content fields the BD team can edit after submission while
# the ACE opportunity is still mutable. Adding a property here without
# also handling it in update_in_ace._apply_delta is a no-op; the receiver
# enqueues but the worker doesn't know what to do with it. Keep both in
# sync.
#
# Write-quota note: one form save that PATCHes N of these properties at
# once fans out into N webhooks -> N SQS messages -> N
# UpdateOpportunity calls against AWS Partner Central (1 write/sec quota
# per partner). At the realistic BD-load envelope this codebase serves
# (5-50 ops/day, 10-20 changed fields per save, occasional bulk update)
# the fan-out is fine. At a 200-deal bulk recategorize, the queue
# serializes to ~30 minutes of catch-up. If that becomes a real BD
# complaint, coalesce per-deal in the webhook receiver (250ms window)
# and have update_in_ace consume the combined message in a single
# UpdateOpportunity. Tracked in /Users/isi/.claude/plans/...teacup.md
# non-goals (architecture review's larger fix).
UPDATE_TRIGGER_PROPERTIES: frozenset[str] = frozenset(
    {
        "amount",
        "closedate",
        "dealname",
        "description",
        "govwin_ace_use_case",
        # Extended BD-editable properties; each is a property that
        # update_in_ace._apply_delta forwards to AWS UpdateOpportunity.
        "govwin_ace_competitor_name",
        "govwin_ace_additional_comments",
        "govwin_ace_aws_account_id",
        "govwin_ace_next_steps",
        "govwin_ace_related_opportunity_id",
        "govwin_ace_marketing_source",
        "govwin_ace_marketing_campaign_name",
        "govwin_ace_marketing_use_cases",
        "govwin_ace_marketing_channel",
        "govwin_ace_marketing_dev_funded",
        # Added 2026-05-27 with the async /ui-extension/update path:
        # the form PATCHes these and the webhook -> update_in_ace
        # pipeline applies them to the AWS opportunity.
        "govwin_ace_lifecycle_stage",
        "govwin_ace_closed_lost_reason",
        "govwin_ace_aws_products",
        "govwin_ace_partner_need",
        "govwin_ace_delivery_model",
        "govwin_ace_sales_activities",
        "govwin_ace_national_security",
        "govwin_ace_opportunity_type",
        "govwin_ace_solution_id",
        "govwin_industry",
    }
)

# Properties whose change should fire a security-audit alert when the
# change source is anything other than the Lambda integration token.
# These are AWS-side identifiers the handle_ace_event Lambda writes back;
# a BD hand-edit would either be a typo (eventually caught by the
# update_in_ace self-heal verify, but only on the next save) or a
# malicious redirect of the Lambda's UpdateOpportunity to a foreign
# AWS opportunity. Surfacing the hand-edit immediately via SNS gives
# the operator a real-time signal instead of waiting for the next save
# to trip the verify path.
#
# Only add identifiers here whose hand-editing would be either
# anomalous (handle_ace_event is the only legitimate writer) or
# security-relevant. Display-only status mirrors (e.g.
# govwin_aws_cosell_status) are NOT in this set because a hand-edit
# there is wrong but not exploitable.
AUDIT_ONLY_PROPERTIES: frozenset[str] = frozenset(
    {
        "govwin_aws_cosell_id",
    }
)

# All properties the HubSpot app subscribes to. Order doesn't matter,
# but the order here maps 1:1 to the order in webhooks-hsmeta.json.
# AUDIT_ONLY_PROPERTIES are included so hand-edits surface via webhook
# delivery; the receiver discriminates by changeSource before alerting.
ALL_SUBSCRIBED_PROPERTIES: tuple[str, ...] = (
    SUBMIT_TRIGGER_PROPERTY,
    *sorted(UPDATE_TRIGGER_PROPERTIES | AUDIT_ONLY_PROPERTIES),
)


def classify_property_change(property_name: str | None) -> str:
    """Return "submit", "update", "audit", or "drop" for a property-change event."""
    if not property_name:
        return "drop"
    if property_name == SUBMIT_TRIGGER_PROPERTY:
        return "submit"
    if property_name in UPDATE_TRIGGER_PROPERTIES:
        return "update"
    if property_name in AUDIT_ONLY_PROPERTIES:
        return "audit"
    return "drop"
