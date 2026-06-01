"""HubSpot deal -> AWS Partner Central CreateOpportunity payload.

The pipeline keeps three fields **manual** because they cannot be reliably
auto-populated from GovWin data:

* ``PrimaryNeedsFromAws`` (HubSpot property: ``govwin_ace_partner_need``)
* ``Project.DeliveryModels`` (HubSpot property: ``govwin_ace_delivery_model``)
* The Solution association (handled via :func:`AssociateOpportunity`,
  defaulting to ``ACEConfig.default_solution_id`` when the deal does not
  override it via ``govwin_ace_solution_id``)

If any required manual field is missing the mapper raises ``ValueError`` so
the submission Lambda can DLQ the deal back to the BD team for completion.
"""

from __future__ import annotations

import logging
from typing import Any

from src.config import AppConfig

logger = logging.getLogger(__name__)


# Single source of truth for the PrimaryNeedsFromAws enum.
# Each entry is (HubSpot label, AWS-published wire value). The HubSpot
# property options (src/hubspot/properties.py:DEAL_PROPERTIES) and the
# AWS-side enum match these short and long forms respectively. Two
# legacy alias entries handle drift: AWS calls it "Competitive
# Information" but a HubSpot label of "Competitive Intelligence" was in
# use at one point; both map to the same wire value.
_PRIMARY_NEED_PAIRS: list[tuple[str, str]] = [
    ("Architectural Validation", "Co-Sell - Architectural Validation"),
    ("Business Presentation", "Co-Sell - Business Presentation"),
    ("Competitive Intelligence", "Co-Sell - Competitive Information"),
    ("Competitive Information", "Co-Sell - Competitive Information"),
    ("Pricing Assistance", "Co-Sell - Pricing Assistance"),
    ("Technical Consultation", "Co-Sell - Technical Consultation"),
    ("Total Cost of Ownership Evaluation", "Co-Sell - Total Cost of Ownership Evaluation"),
    ("Deal Support", "Co-Sell - Deal Support"),
    ("Support for Public Tender", "Co-Sell - Support for Public Tender / RFx"),
    ("Support for Public Tender / RFx", "Co-Sell - Support for Public Tender / RFx"),
]

# HubSpot label -> AWS wire value (also handles wire-form pass-through).
_HUBSPOT_PARTNER_NEED_TO_AWS: dict[str, str] = {
    **{hubspot_label: aws_value for hubspot_label, aws_value in _PRIMARY_NEED_PAIRS},
    **{aws_value: aws_value for _, aws_value in _PRIMARY_NEED_PAIRS},
}

# Set of valid AWS wire values, derived from the same canonical list.
ALLOWED_PRIMARY_NEEDS: set[str] = {aws_value for _, aws_value in _PRIMARY_NEED_PAIRS}


def _normalize_partner_need(value: str) -> str:
    """Translate HubSpot short labels to AWS-long PrimaryNeedsFromAws values."""
    return _HUBSPOT_PARTNER_NEED_TO_AWS.get(value, value)


# Allowed values for ``Project.DeliveryModels``.
ALLOWED_DELIVERY_MODELS: set[str] = {
    "SaaS or PaaS",
    "BYOL or AMI",
    "Managed Services",
    "Professional Services",
    "Resell",
    "Other",
}

# Allowed values for ``Project.CustomerUseCase``. Server-side enum (not in
# the boto3 client model). Sourced from the API's ValidationException error
# message; refresh by calling CreateOpportunity with an invalid value to
# get the latest list. The enum is broader than just "what AWS service is
# being used"; treat as a use-case category.
ALLOWED_CUSTOMER_USE_CASES: set[str] = {
    "AI Machine Learning and Analytics",
    "Archiving",
    "Big Data: Data Warehouse / Data Integration / ETL / Data Lake / BI",
    "Blockchain",
    "Business Applications: Mainframe Modernization",
    "Business Applications & Contact Center",
    "Business Applications & SAP Production",
    "Centralized Operations Management",
    "Cloud Management Tools",
    "Cloud Management Tools & DevOps with Continuous Integration & Continuous Delivery (CICD)",
    "Configuration, Compliance & Auditing",
    "Connected Services",
    "Containers & Serverless",
    "Content Delivery & Edge Services",
    "Database",
    "Edge Computing / End User Computing",
    "Energy",
    "Enterprise Governance & Controls",
    "Enterprise Resource Planning",
    "Financial Services",
    "Healthcare and Life Sciences",
    "High Performance Computing",
    "Hybrid Application Platform",
    "Industrial Software",
    "IOT",
    "Manufacturing, Supply Chain and Operations",
    "Media & High performance computing (HPC)",
    "Migration / Database Migration",
    "Monitoring, logging and performance",
    "Monitoring & Observability",
    "Networking",
    "Outpost",
    "SAP",
    "Security & Compliance",
    "Storage & Backup",
    "Training",
    "VMC",
    "VMWare",
    "Web development & DevOps",
}

DEFAULT_CUSTOMER_USE_CASE = "Migration / Database Migration"


# AWS Project.SalesActivities enum (partnercentral-selling 2022-07-26).
# AWS now requires a non-empty SalesActivities array before allowing the
# opportunity to advance to ReviewStatus=Submitted (server-side validation).
# We seed a sensible BD-default at CreateOpportunity time so the partner
# review path is not blocked on it; BD can refine via UpdateOpportunity.
ALLOWED_SALES_ACTIVITIES: set[str] = {
    "Initialized discussions with customer",
    "Customer has shown interest in solution",
    "Conducted POC / Demo",
    "In evaluation / planning stage",
    "Agreed on solution to Business Problem",
    "Completed Action Plan",
    "Finalized Deployment Need",
    "SOW Signed",
}

DEFAULT_SALES_ACTIVITIES = ["Initialized discussions with customer"]


# Per-opportunity association quotas published in the AWS Partner Central
# Selling API quotas doc. Exceeding any of these causes AWS to reject the
# AssociateOpportunity call with ValidationException. Surfacing these
# constants here lets the form, the backend Lambda, and tests share one
# source of truth.
# Source: https://docs.aws.amazon.com/partner-central/latest/selling-api/quotas.html
MAX_AWS_PRODUCTS_PER_OPPORTUNITY = 20

# HubSpot stores deal.amount as the annual / full-contract value;
# AWS Partner Central's ExpectedCustomerSpend.Amount is paired with
# Frequency=Monthly. Divide by this constant to convert.
MRR_MONTHS_PER_YEAR = 12
MAX_SOLUTIONS_PER_OPPORTUNITY = 10
MAX_MARKETPLACE_OFFERS_PER_OPPORTUNITY = 1


# The remaining closed enums below are sourced verbatim from the boto3
# service model for partnercentral-selling (CreateOpportunity input shape)
# at the date the audit doc was last updated. Their primary job is to drive
# server-side payload validation in submit_form_to_ace AND to populate the
# HubSpot custom-property option sets so BD's dropdowns can never produce a
# value AWS would reject. A regression test (tests/unit/test_ace_enums.py)
# diffs each constant against the live boto3 model so new enum members or
# renames surface in CI rather than at submission time.

# Sentinel value of CompetitorName that AWS requires to be paired with a
# free-text OtherCompetitorNames. Exported so mapper, validator, and TS
# enums import the same literal; a typo here was a real source of
# enum-validation drift in early-2026 iterations.
COMPETITOR_OTHER_SENTINEL: str = "*Other"

# Project.CompetitorName (11 values). Note the literal asterisk in
# COMPETITOR_OTHER_SENTINEL (when picked, populate
# Project.OtherCompetitorNames) and the missing space in
# "Other- Cost Optimization".
ALLOWED_COMPETITORS: frozenset[str] = frozenset(
    {
        "Oracle Cloud",
        "On-Prem",
        "Co-location",
        "Akamai",
        "AliCloud",
        "Google Cloud Platform",
        "IBM Softlayer",
        "Microsoft Azure",
        "Other- Cost Optimization",
        "No Competition",
        COMPETITOR_OTHER_SENTINEL,
    }
)

# Marketing.Channels[] (13 values; multi-select). Only allowed when
# Marketing.Source == "Marketing Activity".
ALLOWED_MARKETING_CHANNELS: frozenset[str] = frozenset(
    {
        "AWS Marketing Central",
        "Content Syndication",
        "Display",
        "Email",
        "Live Event",
        "Out Of Home (OOH)",
        "Print",
        "Search",
        "Social",
        "Telemarketing",
        "TV",
        "Video",
        "Virtual Event",
    }
)

# OpportunityType (3 values). The default for net-new co-sell is "Net New Business".
ALLOWED_OPPORTUNITY_TYPES: frozenset[str] = frozenset(
    {
        "Net New Business",
        "Flat Renewal",
        "Expansion",
    }
)

# Origin (2 values). For Catalog == "AWS" the value MUST be "Partner Referral".
# "AWS Referral" is only valid in Sandbox catalog for simulating AWS-originated flows.
ALLOWED_ORIGINS: frozenset[str] = frozenset({"AWS Referral", "Partner Referral"})

# NationalSecurity (2 values). AWS only accepts "Yes" when
# Customer.Account.Industry == "Government"; we default to "No" when the
# industry is Government and omit otherwise.
ALLOWED_NATIONAL_SECURITY: frozenset[str] = frozenset({"Yes", "No"})

# Marketing.Source (2 values). When "None", the entire Marketing block must
# be omitted from the CreateOpportunity payload or AWS rejects with
# ACTION_NOT_PERMITTED on the companion fields.
ALLOWED_MARKETING_SOURCES: frozenset[str] = frozenset({"Marketing Activity", "None"})

# Marketing.AwsFundingUsed (2 values).
ALLOWED_FUNDING_USED: frozenset[str] = frozenset({"Yes", "No"})

# LifeCycle.ClosedLostReason (19 values). Only meaningful when the
# opportunity is being moved to LifeCycle.Stage == "Closed Lost".
ALLOWED_CLOSED_LOST_REASONS: frozenset[str] = frozenset(
    {
        "Customer Deficiency",
        "Delay / Cancellation of Project",
        "Legal / Tax / Regulatory",
        "Lost to Competitor - Google",
        "Lost to Competitor - Microsoft",
        "Lost to Competitor - SoftLayer",
        "Lost to Competitor - VMWare",
        "Lost to Competitor - Other",
        "No Opportunity",
        "On Premises Deployment",
        "Partner Gap",
        "Price",
        "Security / Compliance",
        "Technical Limitations",
        "Customer Experience",
        "Other",
        "People/Relationship/Governance",
        "Product/Technology",
        "Financial/Commercial",
    }
)

# LifeCycle.Stage (7 values). handle_ace_event maps HubSpot lifecyclestage
# to one of these on the inbound AWS EventBridge writeback. "Launched" is
# AWS-set after spend is verified; we never set it from this codebase.
ALLOWED_LIFECYCLE_STAGES: frozenset[str] = frozenset(
    {
        "Prospect",
        "Qualified",
        "Technical Validation",
        "Business Validation",
        "Committed",
        "Launched",
        "Closed Lost",
    }
)

# SoftwareRevenue.DeliveryModel (3 values). Only relevant for partners
# carrying SoftwareRevenue commitments. Not currently emitted by the mapper.
ALLOWED_SOFTWARE_REVENUE_DELIVERY_MODELS: frozenset[str] = frozenset(
    {
        "Contract",
        "Pay-as-you-go",
        "Subscription",
    }
)

# OpportunityTeam[].BusinessTitle server-side enum (NOT in the boto3 model;
# documented on the Contact API page). AWS rejects any other value with
# INVALID_VALUE at CreateOpportunity time. OpportunityOwner is the right
# fit for the HubSpot deal owner driving the engagement.
ALLOWED_OPPORTUNITY_TEAM_BUSINESS_TITLES: frozenset[str] = frozenset(
    {
        "PartnerAccountManager",
        "OpportunityOwner",
    }
)


# AWS Customer.Account.Industry enum. Sourced from the boto3 service model
# (partnercentral-selling 2022-07-26).
ALLOWED_INDUSTRIES: frozenset[str] = frozenset(
    {
        "Aerospace",
        "Agriculture",
        "Automotive",
        "Computers and Electronics",
        "Consumer Goods",
        "Education",
        "Energy - Oil and Gas",
        "Energy - Power and Utilities",
        "Financial Services",
        "Gaming",
        "Government",
        "Healthcare",
        "Hospitality",
        "Life Sciences",
        "Manufacturing",
        "Marketing and Advertising",
        "Media and Entertainment",
        "Mining",
        "Non-Profit Organization",
        "Professional Services",
        "Real Estate and Construction",
        "Retail",
        "Software and Internet",
        "Telecommunications",
        "Transportation and Logistics",
        "Travel",
        "Wholesale and Distribution",
        "Other",
    }
)


def normalize_industry(value: str | None) -> tuple[str, str | None]:
    """Map an arbitrary HubSpot industry to an AWS-accepted enum value.

    Returns ``(industry, other_industry)``. When the supplied value is in
    the AWS enum, returns it as-is and ``None``. When it isn't, returns
    ``"Other"`` and the original value (so AWS sees both ``Industry`` and
    ``OtherIndustry``). When no value is supplied, defaults to
    ``Government`` (this project's federal-AWS-partner audience).
    """
    if not value:
        return "Government", None
    if value in ALLOWED_INDUSTRIES:
        return value, None
    return "Other", value[:255]


# StateOrRegion is a server-side enum that uses full names (with the
# specific oddity "Dist. of Columbia" instead of "District of Columbia").
# We keep a postal-code-to-enum-name lookup so HubSpot company records
# carrying the standard 2-letter abbreviation Just Work; everything else
# falls through unchanged.
_STATE_ABBR_TO_FULL: dict[str, str] = {
    "AL": "Alabama",
    "AK": "Alaska",
    "AS": "American Samoa",
    "AZ": "Arizona",
    "AR": "Arkansas",
    "CA": "California",
    "CO": "Colorado",
    "CT": "Connecticut",
    "DE": "Delaware",
    "DC": "Dist. of Columbia",
    "FM": "Federated States of Micronesia",
    "FL": "Florida",
    "GA": "Georgia",
    "GU": "Guam",
    "HI": "Hawaii",
    "ID": "Idaho",
    "IL": "Illinois",
    "IN": "Indiana",
    "IA": "Iowa",
    "KS": "Kansas",
    "KY": "Kentucky",
    "LA": "Louisiana",
    "ME": "Maine",
    "MH": "Marshall Islands",
    "MD": "Maryland",
    "MA": "Massachusetts",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MS": "Mississippi",
    "MO": "Missouri",
    "MT": "Montana",
    "NE": "Nebraska",
    "NV": "Nevada",
    "NH": "New Hampshire",
    "NJ": "New Jersey",
    "NM": "New Mexico",
    "NY": "New York",
    "NC": "North Carolina",
    "ND": "North Dakota",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "OR": "Oregon",
    "PW": "Palau",
    "PA": "Pennsylvania",
    "PR": "Puerto Rico",
    "RI": "Rhode Island",
    "SC": "South Carolina",
    "SD": "South Dakota",
    "TN": "Tennessee",
    "TX": "Texas",
    "UT": "Utah",
    "VT": "Vermont",
    "VA": "Virginia",
    "VI": "Virgin Islands",
    "WA": "Washington",
    "WV": "West Virginia",
    "WI": "Wisconsin",
    "WY": "Wyoming",
}


# A handful of full state names HubSpot stores that don't match AWS's
# enum verbatim. Most full names ARE accepted by AWS (it accepts both
# postal abbreviation and full name for most states), but DC has the
# specific "Dist. of Columbia" form that "District of Columbia" misses.
_STATE_FULL_NAME_TO_AWS_ENUM: dict[str, str] = {
    "DISTRICT OF COLUMBIA": "Dist. of Columbia",
}


def _normalize_state(value: str | None) -> str:
    """Return the AWS-accepted full state name for a HubSpot state value.

    Accepts the standard 2-letter postal abbreviation (most HubSpot company
    records use this) and returns the matching full name. Unknown values
    pass through unchanged so a real full name like "California" still
    works, and AWS's enum check is the final gate. When no value is
    supplied, defaults to "Dist. of Columbia" because this project's
    audience is federal AWS partners and most opportunities are
    DC-centric. Callers that operate outside the US should not invoke
    this function at all.
    """
    if not value:
        return "Dist. of Columbia"
    upper = value.strip().upper()
    if upper in _STATE_ABBR_TO_FULL:
        return _STATE_ABBR_TO_FULL[upper]
    if upper in _STATE_FULL_NAME_TO_AWS_ENUM:
        return _STATE_FULL_NAME_TO_AWS_ENUM[upper]
    return value


class ACEMappingError(ValueError):
    """Raised when a HubSpot deal cannot be mapped to a valid ACE payload."""


def _split_csv(value: str | None) -> list[str]:
    """Split a HubSpot multi-select property into a list of clean values.

    Despite the ``_csv`` suffix, the delimiter is **semicolon**, not comma.
    HubSpot multi-select properties (and `govwin_ace_*` checkbox groups) emit
    semicolon-separated values; the legacy name dates from when this helper
    handled both. Empty inputs and whitespace-only segments are filtered out
    so the caller never has to deal with empty strings.

    >>> _split_csv("a;b;c")
    ['a', 'b', 'c']
    >>> _split_csv("  Migration ; Modernization ;  Cost Optimization  ")
    ['Migration', 'Modernization', 'Cost Optimization']
    >>> _split_csv("solo")
    ['solo']
    >>> _split_csv("trailing;;empty;")
    ['trailing', 'empty']
    >>> _split_csv("")
    []
    >>> _split_csv(None)
    []
    >>> _split_csv("   ")
    []
    """
    if not value:
        return []
    return [segment.strip() for segment in value.split(";") if segment.strip()]


def _get(deal: dict[str, Any], key: str) -> Any:
    """Pull a property off a HubSpot deal payload (handles nested ``properties``)."""
    if "properties" in deal and isinstance(deal["properties"], dict):
        return deal["properties"].get(key) or deal.get(key)
    return deal.get(key)


# ISO-3166 alpha-2 country codes. Full set is ~250 entries; we ship the
# subset a federal/SLED partner audience plausibly encounters and force
# any other value through the lookup map below. The strict allowlist
# prevents the "United States" -> "UN" or "Germany" -> "GE" wrong-code
# misroutings that AWS would silently accept and that violate CMMC L2
# 3.1.3 (controlled flow of CUI by jurisdiction).
_VALID_ISO3166_ALPHA2: frozenset[str] = frozenset(
    {
        # North America
        "US",
        "CA",
        "MX",
        # Europe (most common for federal partners)
        "GB",
        "FR",
        "DE",
        "IT",
        "ES",
        "NL",
        "BE",
        "PT",
        "IE",
        "AT",
        "CH",
        "SE",
        "NO",
        "DK",
        "FI",
        "PL",
        "CZ",
        "GR",
        "RO",
        "HU",
        # APAC
        "JP",
        "KR",
        "AU",
        "NZ",
        "SG",
        "IN",
        "ID",
        "MY",
        "PH",
        "TH",
        "VN",
        "TW",
        "HK",
        "CN",
        # Middle East / Africa (federal-relevant)
        "IL",
        "AE",
        "SA",
        "QA",
        "KW",
        "TR",
        "EG",
        "ZA",
        "NG",
        "KE",
        # Latin America
        "BR",
        "AR",
        "CL",
        "CO",
        "PE",
        # Other
        "RU",
        "UA",
    }
)

_COUNTRY_NAME_TO_ISO2: dict[str, str] = {
    "UNITED STATES": "US",
    "UNITED STATES OF AMERICA": "US",
    "USA": "US",
    "U.S.": "US",
    "U.S.A.": "US",
    "AMERICA": "US",
    "CANADA": "CA",
    "MEXICO": "MX",
    "UNITED KINGDOM": "GB",
    "UK": "GB",
    "GREAT BRITAIN": "GB",
    "ENGLAND": "GB",
    "SCOTLAND": "GB",
    "WALES": "GB",
    "GERMANY": "DE",
    "FRANCE": "FR",
    "ITALY": "IT",
    "SPAIN": "ES",
    "NETHERLANDS": "NL",
    "JAPAN": "JP",
    "AUSTRALIA": "AU",
    "INDIA": "IN",
    "BRAZIL": "BR",
    "CHINA": "CN",
    "SOUTH KOREA": "KR",
    "KOREA": "KR",
    "ISRAEL": "IL",
    "UNITED ARAB EMIRATES": "AE",
    "UAE": "AE",
    "SAUDI ARABIA": "SA",
}


def _normalize_country(value: str | None) -> str:
    """Convert a HubSpot country string to AWS's expected ISO-2 code.

    Strict allowlist: a free-text input that is not in the lookup map and
    is not already a valid ISO-2 code raises ``ACEMappingError`` rather
    than silently producing a wrong code (e.g. "Germany" -> "GE" which is
    Georgia, or "Internal Test" -> "IN" which is India). Federal jurisdiction
    routing depends on this; getting it wrong creates compliance gaps.

    Empty / missing values default to ``"US"`` because this project's customer
    base is overwhelmingly US-federal.
    """
    if not value:
        return "US"
    upper = value.strip().upper()
    if not upper:
        return "US"
    if upper in _COUNTRY_NAME_TO_ISO2:
        return _COUNTRY_NAME_TO_ISO2[upper]
    if len(upper) == 2 and upper in _VALID_ISO3166_ALPHA2:
        return upper
    raise ACEMappingError(
        f"Cannot map country {value!r} to an ISO-3166 alpha-2 code. "
        "Set the HubSpot company's Country/Region to a recognized value "
        "(e.g. 'United States', 'Canada', 'Germany') or a valid ISO-2 code."
    )


def _company_prop(company: dict[str, Any] | None, key: str) -> str | None:
    if not company:
        return None
    props = company.get("properties") or {}
    val = props.get(key) or company.get(key)
    return str(val) if val else None


def _customer_block(deal: dict[str, Any], company: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build the Customer.Account block.

    Reads customer fields from the deal's associated HubSpot Company when
    one is provided (read from the associated record rather than
    deal properties when available). Falls back to deal-
    level GovWin properties when no company is associated yet (still
    happens during early sync windows or when test fixtures pass minimal
    payloads).

    CountryCode lives under Customer.Account.Address (not flat on Account)
    per the AWS Partner Central Selling API shape. StateOrRegion's enum
    is enforced server-side ONLY when CountryCode is "US"; outside the US
    we pass whatever string comes in.
    """
    company_name = (
        _company_prop(company, "name")
        or _get(deal, "govwin_agency")
        or _get(deal, "dealname")
        or "Unknown Federal Agency"
    )
    industry_raw = _company_prop(company, "industry") or _get(deal, "govwin_industry")
    industry, other_industry = normalize_industry(industry_raw)
    website = (
        _company_prop(company, "website")
        or _company_prop(company, "domain")
        or _get(deal, "govwin_entity_url")
        or "https://www.usa.gov"
    )
    # Read full address from the associated company; fall back to GovWin
    # agency defaults only when the company record is empty.
    street = _company_prop(company, "address")
    city = _company_prop(company, "city")
    postal_code = (
        _company_prop(company, "zip") or _get(deal, "govwin_customer_postal_code") or "20001"
    )
    country_raw = _company_prop(company, "country") or _get(deal, "govwin_country") or "US"
    country_code = _normalize_country(country_raw)
    state_value = _company_prop(company, "state") or _get(deal, "govwin_customer_state")

    address: dict[str, Any] = {
        "CountryCode": country_code,
        "PostalCode": postal_code,
    }
    if street:
        address["StreetAddress"] = street[:255]
    if city:
        address["City"] = city[:50]
    if country_code == "US":
        address["StateOrRegion"] = _normalize_state(state_value)
    elif state_value:
        address["StateOrRegion"] = state_value

    account: dict[str, Any] = {
        "CompanyName": company_name,
        "Industry": industry,
        "WebsiteUrl": website,
        "Address": address,
    }
    if other_industry:
        account["OtherIndustry"] = other_industry
    aws_acct = _get(deal, "govwin_ace_aws_account_id")
    if aws_acct:
        account["AwsAccountId"] = str(aws_acct)[:12]
    return {"Account": account}


_PHONE_EXTENSION_MARKERS = (" x", " ext", " ext.", ",", ";")


def _strip_phone_extension(raw: str) -> str:
    """Drop everything from the first extension marker onward."""
    lower = raw.lower()
    cut = len(raw)
    for marker in _PHONE_EXTENSION_MARKERS:
        idx = lower.find(marker)
        if idx >= 0:
            cut = min(cut, idx)
    return raw[:cut]


def _is_implausible_phone(digits: str) -> bool:
    """Reject obviously-garbage phone digit strings."""
    if not digits:
        return True
    # All-same-digit (e.g. "0000000000", "1111111111"); never legitimate.
    if len(set(digits)) == 1:
        return True
    return False


def _normalize_phone(value: str | None) -> str | None:
    """Convert a HubSpot phone string to E.164 (e.g. ``+12025550100``).

    AWS Partner Central enforces ``\\+[1-9]\\d{1,14}`` on phone fields and
    rejects the entire CreateOpportunity submission when any contact phone
    fails. HubSpot accepts free-form phone strings; we tighten:

      * Strip extension markers (``x123``, ``ext 5``, ``,567``, ``;789``)
        before digit extraction so a US number with extension does not
        produce a 14-digit garbage E.164.
      * Reject phones whose digits are all the same character (``0000...``,
        ``1111...``); never legitimate, often planted by form-spam.
      * Require US 10-digit area codes to start ``[2-9]`` (NANP rule).
      * Cap international numbers to 8-15 digits inclusive of country
        code; require the leading digit to be ``[1-9]``.

    Returns ``None`` when the input cannot be salvaged so callers drop the
    Phone field rather than fail the whole submission.
    """
    if not value:
        return None
    raw = _strip_phone_extension(str(value).strip())
    if not raw:
        return None
    if raw.startswith("+"):
        digits = "".join(ch for ch in raw[1:] if ch.isdigit())
        if not (8 <= len(digits) <= 15):
            return None
        if digits[0] == "0" or _is_implausible_phone(digits):
            return None
        return f"+{digits}"
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits or _is_implausible_phone(digits):
        return None
    if len(digits) == 10:
        # NANP rule: area code starts with [2-9]; the same constraint
        # applies to the central-office code.
        if digits[0] in "01":
            return None
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        if digits[1] in "01":
            return None
        return f"+{digits}"
    if 8 <= len(digits) <= 15 and digits[0] != "0":
        return f"+{digits}"
    return None


# HubSpot lifecyclestage values whose contacts may be forwarded to AWS as
# customer-side participants. Other stages (subscriber, evangelist, other,
# internal partner staff misclassified by BD) are dropped to avoid leaking
# PII to AWS reviewers under GDPR/CCPA "purpose limitation" and SOC 2 CC6.7.
_FORWARDABLE_LIFECYCLESTAGES: frozenset[str] = frozenset(
    {
        "lead",
        "marketingqualifiedlead",
        "salesqualifiedlead",
        "opportunity",
        "customer",
    }
)


def _customer_contacts(
    contacts: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Build Customer.Contacts[] from associated HubSpot Contact records.

    AWS accepts up to 10 entries per opportunity; the HubSpotClient already
    caps at that. AWS requires at least FirstName, LastName, Email per
    contact; entries missing any of those are dropped silently rather than
    raising the whole submission.

    PII purpose-limitation: a contact is only forwarded to AWS when its
    HubSpot ``lifecyclestage`` indicates customer-side intent (lead /
    qualified lead / opportunity / customer). Internal partner staff or
    misclassified contacts ("subscriber", "other", blank stage) are
    dropped so PII never reaches AWS reviewers without a CRM-level
    consent signal. Hyperscaler-Contact records (created by the
    handle_ace_event Lambda) are also filtered out; they're AWS-side
    contacts, not customer-side.
    """
    out: list[dict[str, Any]] = []
    for c in contacts or []:
        props = c.get("properties") or {}
        first = props.get("firstname") or props.get("firstName")
        last = props.get("lastname") or props.get("lastName")
        email = props.get("email")
        if not (first and last and email):
            continue
        # Skip Hyperscaler-Contact (AWS-side) records that were associated
        # to the deal via handle_ace_event. They must not appear in
        # Customer.Contacts[].
        if str(props.get("hs_lead_status") or "").upper() == "HYPERSCALER_CONTACT":
            continue
        stage = str(props.get("lifecyclestage") or "").lower()
        if stage and stage not in _FORWARDABLE_LIFECYCLESTAGES:
            continue
        entry: dict[str, Any] = {
            "FirstName": str(first)[:80],
            "LastName": str(last)[:80],
            "Email": str(email)[:80],
        }
        title = props.get("jobtitle") or props.get("jobTitle")
        if title:
            entry["BusinessTitle"] = str(title)[:80]
        # AWS enforces E.164 on phones and rejects the whole submission on
        # mismatch. Normalize; drop the field when un-salvageable rather
        # than fail the whole opportunity for an optional value.
        phone = _normalize_phone(props.get("phone"))
        if phone:
            entry["Phone"] = phone
        out.append(entry)
    return out[:10]


def _opportunity_team(owner: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Build OpportunityTeam[] from the HubSpot deal owner.

    AWS attributes the engagement to this person on the partner side. We
    populate from the HubSpot deal owner so the partner contact follows
    whoever owns the deal. Returns an empty list when no owner is set so
    the caller can omit the field rather than emit a malformed payload.
    """
    if not owner:
        return []
    first = owner.get("firstName") or owner.get("first_name")
    last = owner.get("lastName") or owner.get("last_name")
    email = owner.get("email")
    if not (first and last and email):
        return []
    # AWS-side server enum (not in boto3 model): partnerOpportunityTeam[].member.businessTitle
    # must be one of [PartnerAccountManager, OpportunityOwner]. Confirmed via
    # 2026-05 ValidationException response from CreateOpportunity. Use
    # OpportunityOwner for the HubSpot deal owner; this maps to the partner-side
    # individual driving the engagement.
    return [
        {
            "FirstName": str(first)[:80],
            "LastName": str(last)[:80],
            "Email": str(email)[:80],
            "BusinessTitle": "OpportunityOwner",
        }
    ]


def _scrub_text(value: Any, limit: int = 255) -> str:
    """Strip control characters and truncate. Defense-in-depth against
    HubSpot-supplied free-text values that downstream consumers (AWS
    reviewer console) might render without escaping. We don't actively
    validate the charset; we just remove the bytes that are never
    legitimate in marketing copy.
    """
    if value is None:
        return ""
    text = str(value)
    cleaned = "".join(ch for ch in text if ch == "\n" or ch == "\t" or ch >= " ")
    return cleaned[:limit]


def _marketing_block(deal: dict[str, Any]) -> dict[str, Any] | None:
    """Build the Marketing block from BD-editable HubSpot properties.

    AWS makes the entire Marketing block optional; we emit it only when at
    least one BD-edited field is set, so opportunities with no marketing
    attribution don't carry empty defaults to AWS.
    """
    source = _get(deal, "govwin_ace_marketing_source")
    campaign = _get(deal, "govwin_ace_marketing_campaign_name")
    use_cases = _get(deal, "govwin_ace_marketing_use_cases")
    channel = _get(deal, "govwin_ace_marketing_channel")
    funded = _get(deal, "govwin_ace_marketing_dev_funded")
    # AWS validation: the Marketing block as a whole is only meaningful
    # when Source is "Marketing Activity". When Source is "None" or unset,
    # AWS rejects companion fields (CampaignName, AwsFundingUsed, etc.)
    # with ACTION_NOT_PERMITTED. Two outcomes:
    #   1. Source == "Marketing Activity" -> emit the full block
    #   2. Source is "None" / unset       -> emit nothing (skip block)
    is_marketing_sourced = (str(source) if source else "None") == "Marketing Activity"
    if not is_marketing_sourced:
        return None
    block: dict[str, Any] = {"Source": "Marketing Activity"}
    if campaign:
        block["CampaignName"] = _scrub_text(campaign, limit=255)
    if use_cases:
        block["UseCases"] = [_scrub_text(v, limit=80) for v in _split_csv(use_cases)]
    if channel:
        # HubSpot stores a multi-select checkbox enumeration as a single
        # ";"-joined string ("Email;Telemarketing"). AWS's marketing.channels
        # is a list of enum values; splitting here keeps both happy and
        # mirrors how delivery_models / use_cases are already handled above.
        block["Channels"] = [_scrub_text(v, limit=80) for v in _split_csv(channel)]
    if funded in ("Yes", "No"):
        block["AwsFundingUsed"] = funded
    return block


def _project_block(
    deal: dict[str, Any],
) -> dict[str, Any]:
    title = _get(deal, "dealname") or "GovWin Opportunity"
    description = _get(deal, "description") or _get(deal, "govwin_primary_requirement") or ""
    delivery_models = _split_csv(_get(deal, "govwin_ace_delivery_model"))
    if not delivery_models:
        raise ACEMappingError(
            f"govwin_ace_delivery_model is required (one of: {sorted(ALLOWED_DELIVERY_MODELS)})"
        )
    invalid = [m for m in delivery_models if m not in ALLOWED_DELIVERY_MODELS]
    if invalid:
        raise ACEMappingError(
            f"Invalid DeliveryModels: {len(invalid)} value(s) not in the AWS-published enum"
        )

    project: dict[str, Any] = {
        "Title": title[:255],
        "DeliveryModels": delivery_models,
    }
    if description:
        # CustomerBusinessProblem is free text; CustomerUseCase is an
        # AWS-published enum (different from a free-text description).
        # Server-side regex requires 20-2000 chars, so deals with very
        # short descriptions get padded with the deal title for context.
        text = description[:2000]
        if len(text) < 20:
            title_for_pad = title[:200]
            text = f"{title_for_pad}: {text}"[:2000]
        if len(text) >= 20:
            project["CustomerBusinessProblem"] = text

    # Resolve CustomerUseCase from a HubSpot custom property override or the
    # default. AWS treats CustomerUseCase as a closed enum (no companion
    # OtherCustomerUseCase free-text field), so an unknown value would be
    # rejected by AWS at CreateOpportunity time. Two cases need handling:
    #
    #   1. "Other": legacy BD-UX shorthand for "I don't have a strong
    #      opinion." Map to DEFAULT_CUSTOMER_USE_CASE silently and warn.
    #   2. Any other unknown value: a real BD mistake or AWS enum drift.
    #      Reject with a clear ACEMappingError so the SNS alert tells BD
    #      what to fix.
    use_case_raw = _get(deal, "govwin_ace_use_case") or DEFAULT_CUSTOMER_USE_CASE
    if use_case_raw == "Other":
        logger.warning(
            "ace.mapper: govwin_ace_use_case='Other' is not in the AWS enum; "
            "falling back to default %r. Update the deal in HubSpot to a "
            "specific use case for clearer downstream attribution.",
            DEFAULT_CUSTOMER_USE_CASE,
        )
        use_case = DEFAULT_CUSTOMER_USE_CASE
    elif use_case_raw not in ALLOWED_CUSTOMER_USE_CASES:
        raise ACEMappingError(
            f"Invalid CustomerUseCase {use_case_raw!r}: value not in the "
            f"AWS-published enum. Update govwin_ace_use_case on the deal to "
            f"one of: {', '.join(sorted(ALLOWED_CUSTOMER_USE_CASES))}"
        )
    else:
        use_case = use_case_raw
    project["CustomerUseCase"] = use_case

    # Seed SalesActivities so the AWS-side review flow can advance to
    # ReviewStatus=Submitted. AWS rejects the transition with
    # "project.salesActivities is required" when this is missing.
    project["SalesActivities"] = list(DEFAULT_SALES_ACTIVITIES)

    # AWS rejects the opportunity if neither a Solution association nor
    # OtherSolutionDescription is provided. Since AssociateOpportunity is
    # a separate post-create step (and may be skipped, e.g. in Sandbox),
    # we always populate OtherSolutionDescription here as a fallback.
    # Per-deal override via govwin_ace_other_solution_description wins;
    # otherwise we fall back to the deal title.
    other_solution = (
        _get(deal, "govwin_ace_other_solution_description") or title or "Partner solution"
    )
    project["OtherSolutionDescription"] = str(other_solution)[:255]

    amount = _get(deal, "amount")
    if amount:
        try:
            # HubSpot stores deal amount as the total (typically annual or
            # full contract value). AWS expects ExpectedCustomerSpend.Amount
            # to match the declared Frequency. We bill the deal monthly, so
            # divide by 12 to get the monthly equivalent. Without this,
            # AWS reviewers see 12x the real spend.
            total = float(amount)
            # AWS regex on Amount rejects strict-zero. RFI-stage opps often
            # carry amount=0 (no value disclosed yet); skip the spend entry
            # entirely rather than emit a value AWS will reject.
            if total > 0:
                monthly = total / MRR_MONTHS_PER_YEAR
                # TargetCompany is an AWS enum, not a free-text company
                # name: "AWS" tags the spend as the AWS MRR estimate,
                # "Self" is for partner TCV with ExpectedContractDuration.
                # With no ExpectedContractDuration on the project, AWS
                # accepts only "AWS" and silently coerces anything else,
                # so emit the literal to avoid relying on that coercion.
                project["ExpectedCustomerSpend"] = [
                    {
                        "Amount": f"{monthly:.2f}",
                        "CurrencyCode": "USD",
                        "Frequency": "Monthly",
                        "TargetCompany": "AWS",
                    }
                ]
        except (TypeError, ValueError):
            logger.warning("ace.mapper: invalid amount %r on deal %s", amount, deal.get("id"))

    # BD-editable extensions for richer AWS-side context.
    additional = _get(deal, "govwin_ace_additional_comments")
    if additional:
        project["AdditionalComments"] = str(additional)[:255]
    competitor = _get(deal, "govwin_ace_competitor_name")
    if competitor:
        project["CompetitorName"] = str(competitor)[:255]
    related = _get(deal, "govwin_ace_related_opportunity_id")
    if related:
        project["RelatedOpportunityIdentifier"] = str(related)
    return project


# HubSpot ``lifecyclestage`` -> AWS Partner Central ``LifeCycle.Stage`` map.
# AWS publishes seven valid Stage values; the table below routes every
# HubSpot lifecycle into one of them (or omits Stage entirely so AWS uses
# its default for new opportunities).
#
# Why an explicit table instead of derive-on-the-fly:
#   * AWS rejects unknown Stage values with ValidationException — silent
#     defaulting hides bugs that only surface in production.
#   * HubSpot's lifecyclestage is a free-list in custom pipelines; locking
#     the canonical lifecycle values here prevents a renamed pipeline stage
#     from quietly redirecting submissions.
#   * Adding a new HubSpot lifecycle becomes a one-line table addition with
#     a deliberate decision about which AWS Stage it represents.
#
# Source for the AWS Stage enum:
# docs/reference/aws-partner-central/api-contract-audit-2026-04.md
_LIFECYCLE_STAGE_TO_ACE_STAGE: dict[str, str] = {
    "subscriber": "Prospect",
    "lead": "Prospect",
    "marketingqualifiedlead": "Qualified",
    "salesqualifiedlead": "Qualified",
    "opportunity": "Technical Validation",
    "customer": "Committed",
    "evangelist": "Committed",
    "other": "Prospect",
}


def _map_lifecycle_to_ace_stage(lifecycle: str | None) -> str | None:
    """Look up an AWS Partner Central Stage from a HubSpot lifecyclestage.

    Returns ``None`` for unknown lifecycles so the caller can omit the
    Stage field entirely (AWS defaults new opportunities to ``Prospect``).
    Returning a defaulted value silently would mask data drift.
    """
    if not lifecycle:
        return None
    return _LIFECYCLE_STAGE_TO_ACE_STAGE.get(lifecycle.strip().lower())


def _life_cycle_block(deal: dict[str, Any]) -> dict[str, Any]:
    """Build the LifeCycle block.

    AWS requires LifeCycle.TargetCloseDate at CreateOpportunity time. GovWin
    opportunities at the RFI / pre-RFP stage sometimes have no known close
    date and the HubSpot deal carries closedate=null. Default to ~6 months
    from now in that case so AWS does not reject submission; BD can refine
    the date via UpdateOpportunity later.

    LifeCycle.Stage is mapped from HubSpot ``lifecyclestage`` via the
    explicit :data:`_LIFECYCLE_STAGE_TO_ACE_STAGE` table; unknown values
    produce no Stage field (AWS defaults to Prospect) rather than a silent
    misroute.
    """
    from datetime import UTC, datetime, timedelta

    closedate = _get(deal, "closedate")
    block: dict[str, Any] = {"ReviewStatus": "Pending Submission"}
    if closedate:
        # HubSpot delivers ISO-8601; ACE expects YYYY-MM-DD.
        try:
            block["TargetCloseDate"] = str(closedate)[:10]
        except (TypeError, AttributeError):
            pass
    if "TargetCloseDate" not in block:
        block["TargetCloseDate"] = (datetime.now(UTC) + timedelta(days=180)).strftime("%Y-%m-%d")
    next_steps = _get(deal, "govwin_ace_next_steps")
    if next_steps:
        block["NextSteps"] = str(next_steps)[:255]
    stage = _map_lifecycle_to_ace_stage(_get(deal, "lifecyclestage"))
    if stage:
        block["Stage"] = stage
    return block


def map_hubspot_deal_to_ace_create_payload(
    deal: dict[str, Any],
    config: AppConfig,
    *,
    client_token: str,
    company: dict[str, Any] | None = None,
    contacts: list[dict[str, Any]] | None = None,
    owner: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a ``CreateOpportunity`` payload from a HubSpot deal.

    :param deal: HubSpot deal record (either flat or nested ``{properties: {...}}``).
    :param config: app config (provides catalog and default origin/involvement).
    :param client_token: caller-supplied UUID; persist before calling ``CreateOpportunity``.
    :param company: associated HubSpot Company; if provided, Customer.Account.*
        is populated from it. Caller fetches via
        ``HubSpotClient.get_associated_company``.
    :param contacts: associated HubSpot Contacts; mapped into
        ``Customer.Contacts[]``. Caller fetches via
        ``HubSpotClient.get_associated_contacts``.
    :param owner: HubSpot user record for the deal owner; mapped into
        ``OpportunityTeam[]``. Caller fetches via ``HubSpotClient.get_owner``.
    :raises ACEMappingError: when required manual fields are missing or invalid.
    """
    primary_needs_raw = _split_csv(_get(deal, "govwin_ace_partner_need"))
    if not primary_needs_raw:
        raise ACEMappingError(
            f"govwin_ace_partner_need is required (one or more of: {sorted(ALLOWED_PRIMARY_NEEDS)})"
        )
    primary_needs = [_normalize_partner_need(n) for n in primary_needs_raw]
    invalid_needs = [n for n in primary_needs if n not in ALLOWED_PRIMARY_NEEDS]
    if invalid_needs:
        # Redact full values; report only count to keep deal text out of logs/DLQ.
        raise ACEMappingError(
            f"Invalid PrimaryNeedsFromAws: {len(invalid_needs)} value(s) not "
            "in the AWS-published enum"
        )

    govwin_id = _get(deal, "govwin_opp_id") or _get(deal, "govwin_iq_opp_id")

    customer: dict[str, Any] = _customer_block(deal, company)
    contact_list = _customer_contacts(contacts)
    if contact_list:
        customer["Contacts"] = contact_list

    payload: dict[str, Any] = {
        "Catalog": config.ace.catalog,
        "ClientToken": client_token,
        "Origin": config.ace.default_origin,
        "OpportunityType": "Net New Business",
        "PrimaryNeedsFromAws": primary_needs,
        "Project": _project_block(deal),
        "Customer": customer,
        "LifeCycle": _life_cycle_block(deal),
    }
    if govwin_id:
        payload["PartnerOpportunityIdentifier"] = str(govwin_id)

    team = _opportunity_team(owner)
    if team:
        payload["OpportunityTeam"] = team

    marketing = _marketing_block(deal)
    if marketing:
        payload["Marketing"] = marketing

    return payload


def map_update_form_to_ace_payload(
    *,
    form: Any,  # UpdateFormRequest; typed Any to avoid src.models import cycle
    current: dict[str, Any],
    config: AppConfig,
) -> dict[str, Any]:
    """Build an ``UpdateOpportunity`` body from an UpdateFormRequest + current AWS state.

    AWS UpdateOpportunity has PUT semantics: every field present is treated
    as the new value, every field absent is treated as cleared. To avoid
    silently nuking values BD never touched, we start from a scrubbed copy
    of the current AWS opportunity (``ACEClient.scrub_for_update``) and
    overlay only the form fields the BD operator actually set.

    The mapper does NOT touch:
      * ``PartnerOpportunityIdentifier``; the synthetic GovWin id stays
        bound to this opportunity for its full lifecycle. AWS enforces
        uniqueness; the Lambda also rejects any attempt to change it.
      * ``Origin``, ``OpportunityTeam``, ``Tags``; not present in the
        UpdateOpportunity input shape (verified against boto3 service
        model 2026-05-27). AWS hides them on update entirely.
      * ``LifeCycle.ReviewStatus`` / ``LifeCycle.ReviewComments`` /
        ``LifeCycle.ReviewStatusReason``; AWS reviewer owns these.
      * AWS Products / Solutions; managed via separate
        ``AssociateOpportunity`` / ``DisassociateOpportunity`` calls by the
        caller, not by ``UpdateOpportunity``.
    """
    from src.ace.client import ACEClient

    payload: dict[str, Any] = ACEClient.scrub_for_update(current)

    # Top-level mutables.
    if form.govwin_industry:
        cust = dict(payload.get("Customer") or {})
        account = dict(cust.get("Account") or {})
        # Reuse the create-path industry normalizer so "Government" -> the
        # AWS enum value (and OtherIndustry when applicable) stays consistent.
        industry_enum, other = normalize_industry(form.govwin_industry)
        account["Industry"] = industry_enum
        if other:
            account["OtherIndustry"] = other
        else:
            account.pop("OtherIndustry", None)
        cust["Account"] = account
        payload["Customer"] = cust

    if form.ace_aws_account_id:
        cust = dict(payload.get("Customer") or {})
        account = dict(cust.get("Account") or {})
        account["AwsAccountId"] = form.ace_aws_account_id
        cust["Account"] = account
        payload["Customer"] = cust

    if form.ace_national_security:
        payload["NationalSecurity"] = form.ace_national_security

    if form.ace_opportunity_type:
        payload["OpportunityType"] = form.ace_opportunity_type

    if form.ace_partner_need:
        payload["PrimaryNeedsFromAws"] = [_normalize_partner_need(n) for n in form.ace_partner_need]

    # Project block updates.
    project = dict(payload.get("Project") or {})
    if form.ace_delivery_model:
        project["DeliveryModels"] = list(form.ace_delivery_model)
    if form.ace_use_case:
        project["CustomerUseCase"] = form.ace_use_case
    if form.dealname:
        project["Title"] = _scrub_text(form.dealname, limit=255)
    if form.description is not None:
        text = _scrub_text(form.description, limit=2000)
        # CustomerBusinessProblem requires 20-2000 chars; if BD wrote
        # something short, pad with the title to stay above the floor.
        if len(text) and len(text) < 20:
            title = str(project.get("Title") or "")[:200]
            text = f"{title}: {text}"[:2000] if title else text
        if len(text) >= 20:
            project["CustomerBusinessProblem"] = text
    if form.amount is not None and form.amount > 0:
        # Same MRR convention as create-path: HubSpot annual / 12 = monthly.
        monthly = float(form.amount) / MRR_MONTHS_PER_YEAR
        # See _project_block: TargetCompany is the "AWS" | "Self" enum,
        # not a company name. Emit "AWS" (the AWS MRR estimate tag).
        project["ExpectedCustomerSpend"] = [
            {
                "Amount": f"{monthly:.2f}",
                "CurrencyCode": "USD",
                "Frequency": "Monthly",
                "TargetCompany": "AWS",
            }
        ]
    if form.ace_sales_activities:
        project["SalesActivities"] = list(form.ace_sales_activities)
    if form.ace_competitor_name:
        project["CompetitorName"] = form.ace_competitor_name
        if (
            form.ace_other_competitor_names
            and form.ace_competitor_name == COMPETITOR_OTHER_SENTINEL
        ):
            project["OtherCompetitorNames"] = form.ace_other_competitor_names
        else:
            project.pop("OtherCompetitorNames", None)
    if form.ace_additional_comments:
        project["AdditionalComments"] = _scrub_text(form.ace_additional_comments, limit=255)
    if form.ace_related_opportunity_id:
        project["RelatedOpportunityIdentifier"] = form.ace_related_opportunity_id
    payload["Project"] = project

    # LifeCycle.
    life_cycle = dict(payload.get("LifeCycle") or {})
    if form.lifecycle_stage:
        life_cycle["Stage"] = form.lifecycle_stage
    if form.lifecycle_stage == "Closed Lost":
        # AWS requires ClosedLostReason when Stage=Closed Lost. The form
        # enforces this client-side; _validate_update_enums enforces it
        # server-side. Belt-and-suspenders: if both checks are bypassed,
        # refuse to set the Stage rather than send a half-formed payload
        # AWS will reject with ValidationException.
        if form.lifecycle_closed_lost_reason:
            life_cycle["ClosedLostReason"] = form.lifecycle_closed_lost_reason
        else:
            life_cycle.pop("Stage", None)
    else:
        # Clear ClosedLostReason if BD walked the stage back from Closed Lost.
        life_cycle.pop("ClosedLostReason", None)
    if form.lifecycle_next_steps is not None:
        text = _scrub_text(form.lifecycle_next_steps, limit=255)
        if text:
            life_cycle["NextSteps"] = text
    if form.lifecycle_target_close_date:
        life_cycle["TargetCloseDate"] = form.lifecycle_target_close_date[:10]
    # AWS reviewer-owned fields: echo the current values back unchanged.
    # UpdateOpportunity has PUT semantics; popping these would clear them
    # on the AWS side (verified: an update on O13753317 with these popped
    # wiped ReviewStatus="Submitted" to null, 2026-05-27). The fields ARE
    # in the boto3 Update input shape; AWS accepts the same value we just
    # GET'd, which is a no-op for the reviewer's state machine.
    payload["LifeCycle"] = life_cycle

    # Marketing block. Mirror the create-path "Source=Marketing Activity or
    # omit the block" rule; AWS rejects companion fields when Source is None.
    if form.marketing is not None:
        source = (form.marketing.source or "").strip()
        if source == "Marketing Activity":
            mk: dict[str, Any] = {"Source": "Marketing Activity"}
            if form.marketing.campaign_name:
                mk["CampaignName"] = _scrub_text(form.marketing.campaign_name, limit=255)
            if form.marketing.channels:
                mk["Channels"] = list(form.marketing.channels)
            if form.marketing.use_cases:
                mk["UseCases"] = list(form.marketing.use_cases)
            if form.marketing.aws_funding_used in ("Yes", "No"):
                mk["AwsFundingUsed"] = form.marketing.aws_funding_used
            payload["Marketing"] = mk
        else:
            payload.pop("Marketing", None)

    return payload


def aws_products_for_deal(deal: dict[str, Any]) -> list[str]:
    """Return the list of AWS product Identifiers BD has tagged on the deal.

    Used by ``submit_to_ace`` to drive AssociateOpportunity calls per
    AwsProducts entry. Empty list means no per-deal AWS products specified.

    Filters out the local ``"Other"`` escape-hatch identifier and any
    duplicate entries that may slip past the HubSpot multi-select. Truncates
    at :data:`MAX_AWS_PRODUCTS_PER_OPPORTUNITY` to match AWS's per-opportunity
    association quota; any overage logs a warning so BD can see what got
    dropped rather than have AWS reject the 21st AssociateOpportunity call
    silently.
    """
    raw = _split_csv(_get(deal, "govwin_ace_aws_products"))
    # Drop the local escape-hatch and de-duplicate, preserving order.
    seen: set[str] = set()
    products: list[str] = []
    for identifier in raw:
        if identifier == "Other" or identifier in seen:
            continue
        seen.add(identifier)
        products.append(identifier)
    if len(products) > MAX_AWS_PRODUCTS_PER_OPPORTUNITY:
        logger.warning(
            "ace.mapper: aws_products_for_deal truncating %d entries to AWS quota of %d "
            "(dropped=%s)",
            len(products),
            MAX_AWS_PRODUCTS_PER_OPPORTUNITY,
            products[MAX_AWS_PRODUCTS_PER_OPPORTUNITY:],
        )
        products = products[:MAX_AWS_PRODUCTS_PER_OPPORTUNITY]
    return products


def resolve_solution_id(deal: dict[str, Any], config: AppConfig) -> str:
    """Return the AWS Solution ID to associate, or "" if none is configured.

    Lookup order:
      1. ``govwin_ace_solution_id`` per-deal override (preferred name)
      2. ``govwin_ace_solution`` per-deal legacy field name
      3. configured ``ace_default_solution_id`` (production catalog only)

    The configured default is intentionally ignored in the Sandbox catalog
    because production-catalog solutions do not exist in the Sandbox
    catalog. In Sandbox, callers fall back to OtherSolutionDescription on
    the create payload.

    Returns "" when nothing is set, so the caller can fall back to the
    OtherSolutionDescription path instead of failing the submission.
    """
    override = _get(deal, "govwin_ace_solution_id") or _get(deal, "govwin_ace_solution")
    # _NONE_REGISTERED_ is the placeholder setup_hubspot uses when ListSolutions
    # returns zero solutions for the catalog. The property must exist for
    # the form to PATCH but the placeholder is never a real Solution Id.
    if override and override != "_NONE_REGISTERED_":
        return str(override)
    if config.ace.catalog == "Sandbox":
        return ""
    return config.ace.default_solution_id or ""
