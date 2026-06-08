<!--
GENERATED FILE. Do not edit by hand.
Source of truth: src/hubspot/properties.py
Regenerate via: make docs-properties
-->

# HubSpot Custom Properties Reference

Authoritative listing of every HubSpot custom property the integration creates, generated from the property definitions in [`src/hubspot/properties.py`](../../src/hubspot/properties.py).

**Total:** 61 properties (53 deal + 5 company + 3 contact). All custom properties use the `govwin_` prefix; built-in HubSpot properties such as `dealname`, `amount`, `closedate`, and `description` are populated directly by the sync without a custom-property definition.

## Deal properties

**Total:** 53 properties.

| Name | Label | Type | Field Type | Group | Description |
|---|---|---|---|---|---|
| govwin_ace_additional_comments | ACE Additional Comments | string | textarea | govwin | BD-curated context for the AWS reviewer. Maps to Project.AdditionalComments. Supplements the GovWin-derived description. |
| govwin_ace_aws_account_id | ACE Customer AWS Account ID | string | text | govwin | Customer's AWS account number (12 digits). Populated when the customer is an existing AWS account holder. |
| govwin_ace_aws_products | ACE AWS Products | enumeration | checkbox | govwin | AWS product Identifiers consumed by this opportunity. Options are seeded at deploy time from resources/aws_products.json (sourced from github.com/aws-samples/partner-crm-integration-samples). AWS limit: 20 products per opportunity. Each is associated to the opportunity via AssociateOpportunity at submit time. |
| govwin_ace_closed_lost_reason | ACE Closed Lost Reason | enumeration | select | govwin | BD's chosen reason when moving the AWS opp to Closed Lost. Used by both the form's UpdateOpportunity payload and the webhook-driven update_in_ace path's _ensure_closed_lost_pair_consistency self-heal (reads this property when only the Stage webhook fires). |
| govwin_ace_competitor_name | ACE Competitor Name | enumeration | select | govwin | Competitor on this deal. AWS-published enum; when *Other is selected, free-form text goes in govwin_ace_other_competitor_names. Maps to Project.CompetitorName. Note the literal asterisk in *Other and the missing space in 'Other- Cost Optimization' (both AWS quirks). |
| govwin_ace_delivery_model | ACE Delivery Model | enumeration | checkbox | govwin | How the solution is delivered (manual entry for ACE submission) |
| govwin_ace_lifecycle_stage | ACE LifeCycle Stage | string | text | govwin | AWS-side LifeCycle.Stage as last seen by handle_ace_event. Source of truth for the Update form's stage pre-fill. |
| govwin_ace_marketing_campaign_name | ACE Marketing Campaign Name | string | text | govwin | Marketing campaign that sourced the opportunity (if any). |
| govwin_ace_marketing_channel | ACE Marketing Channel | enumeration | checkbox | govwin | Marketing channel(s) that sourced the opportunity. |
| govwin_ace_marketing_dev_funded | ACE Marketing Development Funded | enumeration | select | govwin | Did this opportunity use AWS Marketing Development Funds? |
| govwin_ace_marketing_source | ACE Marketing Source | enumeration | select | govwin | Was this opportunity sourced from a marketing activity? |
| govwin_ace_marketing_use_cases | ACE Marketing Use Cases | string | text | govwin | Comma-separated marketing use cases for AWS attribution. |
| govwin_ace_national_security | ACE National Security | enumeration | select | govwin | Whether the opportunity contains classified National Security information. AWS only accepts Yes when Customer.Account.Industry is Government. Maps to NationalSecurity. |
| govwin_ace_next_steps | ACE Next Steps | string | textarea | govwin | BD-curated next steps. Maps to LifeCycle.NextSteps; surfaces in the AWS reviewer UI. |
| govwin_ace_opportunity_type | ACE Opportunity Type | enumeration | select | govwin | AWS ACE opportunity type for Partner Central submission |
| govwin_ace_other_competitor_names | ACE Other Competitor Names | string | text | govwin | Free-form competitor name(s) when ACE Competitor Name is *Other. Maps to Project.OtherCompetitorNames (max 255 chars). |
| govwin_ace_other_solution_description | ACE Other Solution Description | string | textarea | govwin | Free-text description used in place of an associated AWS Solution (255 char max). Optional; the mapper auto-falls back to the deal title when this is blank. |
| govwin_ace_partner_need | ACE Partner Need from AWS | enumeration | checkbox | govwin | Type(s) of AWS support needed (manual entry for ACE submission) |
| govwin_ace_related_opportunity_id | ACE Related Opportunity ID | string | text | govwin | Prior AWS opportunity (O...) for renewals / expansions. Maps to Project.RelatedOpportunityIdentifier. |
| govwin_ace_sales_activities | ACE Sales Activities | enumeration | checkbox | govwin | BD-curated sales activities completed on this deal. AWS requires a non-empty list to advance ReviewStatus past Pending Submission. Maps to Project.SalesActivities. |
| govwin_ace_solution | ACE Solution Offered | string | text | govwin | AWS solution offered (manual entry for ACE submission) |
| govwin_ace_solution_id | ACE Solution Offered | enumeration | select | govwin | AWS Partner Central Solution ID to associate with the opportunity (S-NNNNNNN). Options are seeded at deploy time from ListSolutions; refresh by re-running setup_hubspot. |
| govwin_ace_use_case | ACE Customer Use Case | enumeration | select | govwin | AWS-published Customer Use Case (manual override; defaults to Migration / Database Migration). See src/ace/mapper.py:ALLOWED_CUSTOMER_USE_CASES for the full list. |
| govwin_agency | Government Agency | string | text | govwin | Buying government agency name |
| govwin_analyst_notes | Analyst Notes | string | textarea | govwin | GovWin analyst procurement notes and updates |
| govwin_aws_cosell_id | AWS Co-sell ID | string | text | govwin | AWS Partner Central opportunity id (O...). Populated by handle_ace_event after CreateOpportunity succeeds. |
| govwin_aws_cosell_products | AWS Co-sell Products (AWS-side) | string | text | govwin | Semicolon-joined list of AWS Product identifiers currently associated with the AWS Partner Central opportunity. Mirror of RelatedEntityIdentifiers.AwsProducts; written by handle_ace_event on every inbound opportunity event. The Submit-to-AWS card compares this against the BD-edited govwin_ace_aws_products and shows a 'syncing' pill when they differ (during the async Associate/Disassociate window after /ui-extension/update). |
| govwin_aws_cosell_status | AWS Co-sell Status | string | text | govwin | Latest AWS-side LifeCycle.ReviewStatus. Updated on every Opportunity Updated EventBridge event. |
| govwin_aws_marketplace_engagement_score | AWS Marketplace Engagement Score | string | text | govwin | AWS Marketplace engagement score (when AWS publishes it). Empty for opportunities that AWS has not scored. |
| govwin_cmmc_requirements | CMMC Requirements | string | text | govwin | Cybersecurity Maturity Model Certification requirements |
| govwin_competition_type | Competition Type | string | text | govwin | Type of competition (Full & Open, Set-Aside, etc.) |
| govwin_contract_type | Contract Type | string | text | govwin | Contract type (FFP, T&M, CPFF, etc.) |
| govwin_country | Country | string | text | govwin | Country (USA or CAN) |
| govwin_created_date | GovWin Created Date | datetime | date | govwin | When this opportunity was created in GovWin IQ |
| govwin_duration | Contract Duration | string | text | govwin | Expected contract duration |
| govwin_id | GovWin ID | string | text | govwin | Unique GovWin opportunity ID used for deduplication |
| govwin_industry | Industry (ACE) | string | text | govwin | AWS ACE industry classification derived from NAICS code |
| govwin_iq_opp_id | GovWin IQ Internal ID | string | text | govwin | Internal numeric GovWin ID |
| govwin_iq_url | GovWin IQ URL | string | text | govwin | Direct link to this opportunity in GovWin IQ |
| govwin_market | Market | enumeration | select | govwin | Federal or State/Local/Education |
| govwin_naics_code | NAICS Code | string | text | govwin | Primary NAICS classification code |
| govwin_opp_id | GovWin Opportunity ID | string | text | govwin | Global opportunity ID from GovWin IQ (e.g., OPP12345) |
| govwin_opp_type | GovWin Opportunity Type | enumeration | select | govwin | Type of GovWin opportunity |
| govwin_primary_naics | Primary NAICS | string | text | govwin | Primary NAICS classification title |
| govwin_primary_requirement | Primary Requirement | string | text | govwin | Main procurement requirement |
| govwin_priority | GovWin Priority | number | number | govwin | Bookmarked priority (1-5) |
| govwin_smart_tags | Smart Tags | string | text | govwin | GovWin smart-tagged categories |
| govwin_solicitation_date | Solicitation Date | date | date | govwin | Date the solicitation was released |
| govwin_solicitation_number | Solicitation Number | string | text | govwin | Government solicitation number |
| govwin_source_url | Source URL | string | text | govwin | Link to the government procurement page |
| govwin_status | GovWin Status | string | text | govwin | Raw opportunity status from GovWin |
| govwin_type_of_award | Type of Award | string | text | govwin | Award type classification |
| govwin_update_date | GovWin Update Date | datetime | date | govwin | When this opportunity was last updated in GovWin IQ |

## Company properties

**Total:** 5 properties.

| Name | Label | Type | Field Type | Group | Description |
|---|---|---|---|---|---|
| govwin_entity_id | GovWin Entity ID (Key) | string | text | govwin | Unique GovWin entity ID used for deduplication |
| govwin_entity_type | Entity Type | enumeration | select | govwin | Federal or State/Local |
| govwin_entity_url | GovWin Entity URL | string | text | govwin | Link to entity in GovWin IQ |
| govwin_gov_entity_id | GovWin Gov Entity ID | string | text | govwin | GovWin government entity ID |
| govwin_parent_agency | Parent Agency | string | text | govwin | Parent department/agency name |

## Contact properties

**Total:** 3 properties.

| Name | Label | Type | Field Type | Group | Description |
|---|---|---|---|---|---|
| govwin_contact_id | GovWin Contact ID | string | text | govwin | GovWin contact identifier |
| govwin_entity_level1 | Agency (Level 1) | string | text | govwin | Top-level government agency |
| govwin_entity_level2 | Sub-Agency (Level 2) | string | text | govwin | Sub-agency or office |

## Pipeline stage mapping

Default pipeline label: **"GovWin Pipeline"**.

Map of GovWin opportunity `status` values to HubSpot pipeline stage labels.
Statuses not in this map fall through to the `Other` stage with a CloudWatch
warning so the unmapped status surfaces for review.

| HubSpot Stage Label | GovWin Statuses |
|---|---|
| Closed Lost | `Canceled`, `Cancelled`, `Closed`, `Deleted/Canceled`, `Expired/Archived`, `Lost` |
| Closed Won | `Award`, `Awarded`, `Partial Award` |
| Declined | `Declined` |
| Opportunity Identified | `Forecast Pre-RFP`, `Pre-RFP`, `Pre-Solicitation`, `Umbrella Program` |
| Other | `Other` |
| Preparing Response | `Proposal Submitted` |
| Reviewing Requirements | `RFP`, `RFP Released`, `Solicitation` |
| Submitted | `Evaluation`, `Post-RFP`, `Source Selection`, `Under Evaluation` |
