"""AWS Partner Central Selling API client.

Wraps boto3 ``partnercentral-selling`` calls with:

* per-call rate limiting (1 write/sec, 10 reads/sec)
* tenacity-driven retries on ThrottlingException and InternalServerException
* an optimistic-locking helper that fetches LastModifiedDate before update
  and retries on ConflictException

The catalog defaults to ``Sandbox`` from config. The IAM policy condition
``partnercentral:Catalog: Sandbox`` should enforce this for dev environments.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, NoReturn, cast

from botocore.exceptions import ClientError
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from src.ace.rate_limiter import ACERateLimiter
from src.aws_clients import make_client
from src.config import AppConfig

logger = logging.getLogger(__name__)


class ACEAPIError(Exception):
    """Raised for AWS Partner Central API errors that we cannot recover from."""

    def __init__(self, message: str, code: str | None = None) -> None:
        self.code = code
        super().__init__(message)


def _is_retryable(exc: BaseException) -> bool:
    """Return True if a ClientError represents a transient failure."""
    if not isinstance(exc, ClientError):
        return False
    code = exc.response.get("Error", {}).get("Code", "")
    return code in {"ThrottlingException", "InternalServerException", "ServiceUnavailableException"}


# Top-level fields of the AWS UpdateOpportunity input shape that the
# scrub_for_update echo path preserves. Any field present here must also
# be present in the boto3 model's UpdateOpportunityRequest shape, and
# any field present in that shape but NOT here would be silently dropped
# (= cleared on AWS, given PUT semantics). The CI test in
# tests/unit/test_scrub_for_update_drift.py diffs this set against the
# live model so a new AWS field surfaces immediately rather than after
# a silent data-loss incident.
#
# Catalog, Identifier, and LastModifiedDate are passed by the caller and
# do not need to be echoed from GetOpportunity output.
UPDATE_OPPORTUNITY_ALLOWED_FIELDS: frozenset[str] = frozenset(
    {
        "PrimaryNeedsFromAws",
        "NationalSecurity",
        "Customer",
        "Project",
        "OpportunityType",
        "Marketing",
        "SoftwareRevenue",
        "LifeCycle",
        "PartnerOpportunityIdentifier",
    }
)

# Fields the caller injects per-call rather than echoing from
# GetOpportunity output; the drift test excludes them from the diff.
UPDATE_OPPORTUNITY_CALLER_FIELDS: frozenset[str] = frozenset(
    {
        "Catalog",
        "Identifier",
        "LastModifiedDate",
    }
)


class ACEClient:
    """Client for the AWS Partner Central Selling API."""

    def __init__(self, config: AppConfig, boto3_client: Any | None = None) -> None:
        self._config = config
        self._catalog = config.ace.catalog
        # partnercentral-selling is exposed only in us-east-1; FIPS endpoint
        # is selected automatically. make_client enforces both.
        self._client = boto3_client or make_client("partnercentral-selling", config.aws.region)
        self._rate_limiter = ACERateLimiter(
            reads_per_sec=config.ace.rate_limit_reads_per_sec,
            writes_per_sec=config.ace.rate_limit_writes_per_sec,
        )

    @property
    def catalog(self) -> str:
        return self._catalog

    def __enter__(self) -> ACEClient:
        return self

    def __exit__(self, *args: Any) -> None:
        # boto3 clients do not require explicit close, but match the
        # HubSpotClient context-manager pattern so callers can use both
        # consistently.
        return None

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        reraise=True,
    )
    def _call_write(self, op: str, **kwargs: Any) -> dict[str, Any]:
        self._rate_limiter.acquire_write()
        method = getattr(self._client, op)
        return cast(dict[str, Any], method(**kwargs))

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        reraise=True,
    )
    def _call_read(self, op: str, **kwargs: Any) -> dict[str, Any]:
        self._rate_limiter.acquire_read()
        method = getattr(self._client, op)
        return cast(dict[str, Any], method(**kwargs))

    # ------------------------------------------------------------------
    # Opportunity lifecycle
    # ------------------------------------------------------------------

    def create_opportunity(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Create a new opportunity in AWS Partner Central.

        :param payload: The CreateOpportunity request body. Must already
            include ``Catalog`` and ``ClientToken``; the client injects
            defaults for both if missing, but callers SHOULD generate
            the ClientToken via :meth:`new_client_token` and persist it
            to DynamoDB before this call so SQS-driven retries can reuse
            it (idempotent creates).
        :returns: The CreateOpportunity response, including ``Id`` (the
            assigned AWS opportunity id) and ``LastModifiedDate``.
        :raises ACEAPIError: On any non-retryable AWS error.
        """
        if "Catalog" not in payload:
            payload = {**payload, "Catalog": self._catalog}
        if "ClientToken" not in payload:
            payload = {**payload, "ClientToken": self.new_client_token()}
        logger.info("ace.create_opportunity catalog=%s", payload["Catalog"])
        try:
            return self._call_write("create_opportunity", **payload)
        except ClientError as exc:
            self._raise_api_error("CreateOpportunity", exc)

    def get_opportunity(self, identifier: str) -> dict[str, Any]:
        """Fetch the full opportunity payload by AWS opportunity id.

        :param identifier: The AWS opportunity id (e.g. ``O13753208``).
        :returns: The full opportunity dict, including
            ``LastModifiedDate`` needed for optimistic locking on
            subsequent updates.
        :raises ACEAPIError: On any non-retryable AWS error. The
            ``code`` attribute carries the underlying boto3 error code,
            or ``"CrossCatalogResponse"`` when the response ``Catalog``
            field disagrees with the client's configured catalog. The
            IAM Catalog condition blocks cross-catalog writes; this
            additional check makes the mismatch a hard error at read
            time rather than letting it flow through scrub-and-update
            silently.
        """
        try:
            response = self._call_read(
                "get_opportunity", Catalog=self._catalog, Identifier=identifier
            )
        except ClientError as exc:
            self._raise_api_error("GetOpportunity", exc)
        echoed = response.get("Catalog")
        if echoed and echoed != self._catalog:
            raise ACEAPIError(
                (
                    f"GetOpportunity({identifier}) returned Catalog={echoed!r} "
                    f"but client is configured for {self._catalog!r}"
                ),
                code="CrossCatalogResponse",
            )
        return response

    def list_opportunities(self, **filters: Any) -> dict[str, Any]:
        try:
            return self._call_read("list_opportunities", Catalog=self._catalog, **filters)
        except ClientError as exc:
            self._raise_api_error("ListOpportunities", exc)

    def update_opportunity(
        self,
        identifier: str,
        last_modified_date: Any,
        updates: dict[str, Any],
    ) -> dict[str, Any]:
        """Update an opportunity in AWS Partner Central.

        Uses optimistic locking via ``LastModifiedDate``; a stale value
        produces ``ConflictException``. Prefer :meth:`update_with_retry`
        which fetches the current ``LastModifiedDate`` before retrying.

        :param identifier: The AWS opportunity id.
        :param last_modified_date: ``LastModifiedDate`` from a recent
            GetOpportunity or UpdateOpportunity response.
        :param updates: Top-level fields to update. AWS treats omitted
            fields as cleared under PUT semantics; the caller should
            pass the full scrubbed echo from a current GetOpportunity
            plus any deltas. See :func:`scrub_for_update`.
        :returns: The UpdateOpportunity response, including the new
            ``LastModifiedDate``.
        :raises ACEAPIError: On any non-retryable AWS error.
        """
        params = {
            "Catalog": self._catalog,
            "Identifier": identifier,
            "LastModifiedDate": last_modified_date,
            **updates,
        }
        try:
            return self._call_write("update_opportunity", **params)
        except ClientError as exc:
            self._raise_api_error("UpdateOpportunity", exc)

    def update_with_retry(
        self,
        identifier: str,
        updates: dict[str, Any],
        max_attempts: int = 3,
        known_last_modified_date: Any | None = None,
    ) -> dict[str, Any]:
        """Update an opportunity, refreshing LastModifiedDate on ConflictException.

        :param identifier: The AWS opportunity id.
        :param updates: Top-level body fields to write. See
            :meth:`update_opportunity` for the PUT-semantics caveat.
        :param max_attempts: Maximum total attempts (initial + retries).
        :param known_last_modified_date: Caller-provided
            ``LastModifiedDate`` (e.g. persisted in DynamoDB after the
            last successful write). When supplied the first attempt
            skips the GetOpportunity round-trip; subsequent attempts
            always refetch the current value.
        :returns: The successful UpdateOpportunity response.
        :raises ACEAPIError: On non-retryable errors, or on
            ``ConflictException`` after exhausting ``max_attempts``.
        """
        last_error: Exception | None = None
        last_modified = known_last_modified_date
        for attempt in range(max_attempts):
            if last_modified is None:
                current = self.get_opportunity(identifier)
                last_modified = current.get("LastModifiedDate")
                if last_modified is None:
                    raise ACEAPIError(
                        f"GetOpportunity returned no LastModifiedDate for {identifier}",
                        code="MissingLastModifiedDate",
                    )
            try:
                return self.update_opportunity(
                    identifier=identifier,
                    last_modified_date=last_modified,
                    updates=updates,
                )
            except ACEAPIError as exc:
                if exc.code != "ConflictException":
                    raise
                last_error = exc
                last_modified = None  # force refetch on next attempt
                logger.warning(
                    "ConflictException on update %s attempt %d/%d; refetching",
                    identifier,
                    attempt + 1,
                    max_attempts,
                )
        assert last_error is not None
        raise last_error

    # ------------------------------------------------------------------
    # Solutions and associations
    # ------------------------------------------------------------------

    def list_solutions(self, **filters: Any) -> dict[str, Any]:
        """Raw paginated ``ListSolutions`` call.

        :param filters: Forwarded to boto3 ``list_solutions``. Common
            keys are ``Status`` (list of ``Active`` / ``Inactive`` /
            ``Draft``), ``MaxResults``, and ``NextToken``.
        :returns: The raw boto3 response with ``SolutionSummaries`` and
            ``NextToken`` keys. Use :meth:`list_active_solutions` for the
            paginated, projected view that the UI Extension consumes.
        :raises ACEAPIError: On any non-retryable AWS error.
        """
        try:
            return self._call_read("list_solutions", Catalog=self._catalog, **filters)
        except ClientError as exc:
            self._raise_api_error("ListSolutions", exc)

    def list_active_solutions(self) -> list[dict[str, str]]:
        """Return all Active solutions in the configured catalog, paginated.

        The SolutionPicker in the HubSpot UI Extension form needs a clean
        ``{Id, Name, Category, Status}`` list so it can render a dropdown.
        boto3's ``list_solutions`` returns a paginated response with verbose
        SolutionSummary objects; this method handles pagination, filters
        server-side to ``Status=Active`` so retired solutions never reach
        the form, and projects each entry down to the four fields the UI
        needs.

        Returns an empty list when no solutions are registered (notably the
        Sandbox catalog), so callers can fall back to the
        OtherSolutionDescription path in the create payload without
        branching on a None.

        :returns: list of dicts with keys ``Id`` (e.g. ``"S-0051246"``),
            ``Name`` (display name), ``Category`` (e.g. ``"Professional Service"``),
            and ``Status`` (always ``"Active"``).
        """
        solutions: list[dict[str, str]] = []
        next_token: str | None = None
        while True:
            params: dict[str, Any] = {"Status": ["Active"], "MaxResults": 100}
            if next_token:
                params["NextToken"] = next_token
            response = self.list_solutions(**params)
            for summary in response.get("SolutionSummaries", []):
                solutions.append(
                    {
                        "Id": str(summary.get("Id", "")),
                        "Name": str(summary.get("Name", "")),
                        "Category": str(summary.get("Category", "")),
                        "Status": str(summary.get("Status", "")),
                    }
                )
            next_token = response.get("NextToken")
            if not next_token:
                break
        return solutions

    def associate_opportunity(
        self,
        opportunity_identifier: str,
        related_entity_identifier: str,
        related_entity_type: str = "Solutions",
    ) -> dict[str, Any]:
        """Associate a related entity (typically a Solution) with an opportunity.

        :param opportunity_identifier: The AWS opportunity id.
        :param related_entity_identifier: The related entity id, e.g.
            ``S-0051246`` for a Solution.
        :param related_entity_type: Boto3 enum value; defaults to
            ``"Solutions"``.
        :returns: The raw boto3 response (usually empty on success).
        :raises ACEAPIError: On any non-retryable AWS error.
        """
        try:
            return self._call_write(
                "associate_opportunity",
                Catalog=self._catalog,
                OpportunityIdentifier=opportunity_identifier,
                RelatedEntityIdentifier=related_entity_identifier,
                RelatedEntityType=related_entity_type,
            )
        except ClientError as exc:
            self._raise_api_error("AssociateOpportunity", exc)

    def disassociate_opportunity(
        self,
        opportunity_identifier: str,
        related_entity_identifier: str,
        related_entity_type: str = "Solutions",
    ) -> dict[str, Any]:
        """Remove an association previously added by :meth:`associate_opportunity`.

        :param opportunity_identifier: The AWS opportunity id.
        :param related_entity_identifier: The related entity id to detach.
        :param related_entity_type: Boto3 enum value; defaults to
            ``"Solutions"``.
        :returns: The raw boto3 response.
        :raises ACEAPIError: On any non-retryable AWS error.
        """
        try:
            return self._call_write(
                "disassociate_opportunity",
                Catalog=self._catalog,
                OpportunityIdentifier=opportunity_identifier,
                RelatedEntityIdentifier=related_entity_identifier,
                RelatedEntityType=related_entity_type,
            )
        except ClientError as exc:
            self._raise_api_error("DisassociateOpportunity", exc)

    # ------------------------------------------------------------------
    # Engagement (the "submit" call)
    # ------------------------------------------------------------------

    def start_engagement_from_opportunity_task(
        self,
        opportunity_identifier: str,
        client_token: str,
        aws_submission: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Submit an opportunity to AWS for review.

        :param opportunity_identifier: The AWS opportunity id returned by
            :meth:`create_opportunity`.
        :param client_token: Idempotency token; persist via
            :meth:`src.sync.state.SyncStateManager.reserve_task_client_token`
            so SQS retries reuse the same value.
        :param aws_submission: ``{"InvolvementType": ..., "Visibility": ...}``.
            Defaults to the configured ACE submission defaults when None.
        :returns: The boto3 response including the engagement
            ``TaskArn`` and ``OpportunityIdentifier``.
        :raises ACEAPIError: On any non-retryable AWS error.
        """
        if aws_submission is None:
            aws_submission = {
                "InvolvementType": self._config.ace.default_involvement_type,
                "Visibility": self._config.ace.default_visibility,
            }
        try:
            return self._call_write(
                "start_engagement_from_opportunity_task",
                Catalog=self._catalog,
                ClientToken=client_token,
                Identifier=opportunity_identifier,
                AwsSubmission=aws_submission,
            )
        except ClientError as exc:
            self._raise_api_error("StartEngagementFromOpportunityTask", exc)

    def start_engagement_by_accepting_invitation_task(
        self,
        invitation_identifier: str,
        client_token: str,
    ) -> dict[str, Any]:
        try:
            return self._call_write(
                "start_engagement_by_accepting_invitation_task",
                Catalog=self._catalog,
                ClientToken=client_token,
                Identifier=invitation_identifier,
            )
        except ClientError as exc:
            self._raise_api_error("StartEngagementByAcceptingInvitationTask", exc)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def new_client_token() -> str:
        """Return a fresh idempotency token. Persist before the call so retries reuse it."""
        return str(uuid.uuid4())

    @staticmethod
    def scrub_for_update(current: dict[str, Any]) -> dict[str, Any]:
        """Reduce a GetOpportunity response to fields UpdateOpportunity accepts.

        UpdateOpportunity has PUT semantics: omitted fields are treated
        as cleared. The valid Update params per the boto3 service model
        are narrower than what GetOpportunity returns, so this helper
        whitelists. ``Catalog``, ``Identifier``, and ``LastModifiedDate``
        are passed by the caller and are not part of the body fields the
        scrub touches.

        :param current: The response dict from
            :meth:`get_opportunity`.
        :returns: A new dict containing only fields the boto3
            ``UpdateOpportunity`` shape accepts, with sub-fields cleaned
            so AWS' client-side validator does not reject stub entries
            (empty ``ExpectedCustomerSpend`` rows, contact entries
            missing ``FirstName`` / ``LastName`` / ``Email``, empty
            ``SalesActivities`` lists, and Marketing companions that
            require ``Source == "Marketing Activity"``).
        """
        # AWS UpdateOpportunity has PUT semantics: any field omitted from
        # the request is treated as null. We must echo every field that
        # AWS could have on the opportunity, including fields a BD
        # operator may have set directly in Partner Central UI
        # (Marketing.CampaignName, SoftwareRevenue, NationalSecurity).
        # Dropping them would silently clear them on every webhook.
        #
        # The whitelist matches the boto3 input shape for UpdateOpportunity
        # exactly. PartnerOpportunityIdentifier MUST be echoed too: it
        # carries the GovWin cross-reference and AWS clears it without it.
        # See tests/unit/test_scrub_for_update_drift.py for the CI guard
        # that diffs this set against the live boto3 service model so a
        # new AWS-added field doesn't silently get cleared on update.
        scrubbed = {k: v for k, v in current.items() if k in UPDATE_OPPORTUNITY_ALLOWED_FIELDS}

        # AWS sometimes returns stub fields the boto3 client-side validator
        # rejects on UpdateOpportunity. Specifically:
        #
        #   * ExpectedCustomerSpend may include an entry with only
        #     CurrencyCode populated; Amount / Frequency / TargetCompany
        #     are all required when the entry is present.
        #   * Customer.Contacts[] may contain entries missing FirstName /
        #     LastName / Email when AWS auto-populated from invitations.
        #   * Project.SalesActivities may come back as [] which AWS rejects
        #     when present (must be non-empty if the key exists).
        #   * Marketing may come back as {Source: "None"} which AWS rejects
        #     when paired with companion fields. Drop the whole block in
        #     that case to mirror the create-path logic.
        project = scrubbed.get("Project")
        if isinstance(project, dict):
            spend = project.get("ExpectedCustomerSpend")
            if isinstance(spend, list):
                cleaned = [
                    e
                    for e in spend
                    if isinstance(e, dict)
                    and e.get("Amount")
                    and e.get("Frequency")
                    and e.get("TargetCompany")
                ]
                if cleaned:
                    project["ExpectedCustomerSpend"] = cleaned
                else:
                    project.pop("ExpectedCustomerSpend", None)
            activities = project.get("SalesActivities")
            if isinstance(activities, list) and not activities:
                project.pop("SalesActivities", None)

        customer = scrubbed.get("Customer")
        if isinstance(customer, dict):
            contacts = customer.get("Contacts")
            if isinstance(contacts, list):
                cleaned_contacts = [
                    c
                    for c in contacts
                    if isinstance(c, dict)
                    and c.get("FirstName")
                    and c.get("LastName")
                    and c.get("Email")
                ]
                if cleaned_contacts:
                    customer["Contacts"] = cleaned_contacts
                else:
                    customer.pop("Contacts", None)

        # Marketing handling. Two AWS-side rules collide here:
        #
        # 1. UpdateOpportunity REQUIRES Marketing.Source on every call
        #    (introduced 2026-05; surfaced in sandbox smoke as
        #    REQUIRED_FIELD_MISSING marketing.source). The Marketing
        #    block can no longer be dropped from the payload.
        #
        # 2. AWS still REJECTS companion Marketing fields (UseCases,
        #    AwsFundingUsed, CampaignName, Channel) when Source is
        #    anything other than "Marketing Activity".
        #
        # The reconciliation: always emit Marketing.Source. When Source
        # is empty or "None" (no marketing context), strip companion
        # fields and emit just {Source: "None"} so AWS sees a valid
        # block without rejected companions.
        marketing = scrubbed.get("Marketing")
        if not isinstance(marketing, dict):
            marketing = {}
        source = marketing.get("Source")
        if source in (None, ""):
            source = "None"
        if source != "Marketing Activity":
            # Strip companion fields that AWS rejects when Source is not
            # "Marketing Activity". Keeping Source itself is required.
            marketing = {"Source": source}
        else:
            marketing = {**marketing, "Source": source}
        scrubbed["Marketing"] = marketing

        return scrubbed

    @staticmethod
    def _raise_api_error(op: str, exc: ClientError) -> NoReturn:
        code = exc.response.get("Error", {}).get("Code", "")
        message = exc.response.get("Error", {}).get("Message", str(exc))
        raise ACEAPIError(f"{op} failed [{code}]: {message}", code=code) from exc
