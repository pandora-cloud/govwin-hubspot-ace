"""DynamoDB state management for sync cursors and entity mappings."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

from botocore.exceptions import ClientError

from src.aws_clients import make_resource
from src.config import AppConfig

logger = logging.getLogger(__name__)


def _as_str(value: Any) -> str | None:
    """Narrow a DynamoDB attribute (Any) to ``str | None`` for the type checker."""
    return value if isinstance(value, str) else None


class SyncStateManager:
    """Manages sync state in DynamoDB."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._dynamodb = make_resource("dynamodb", config.aws.region)
        self._state_table = self._dynamodb.Table(config.aws.sync_state_table)
        self._mappings_table = self._dynamodb.Table(config.aws.entity_mappings_table)

    # -----------------------------------------------------------------------
    # Sync Cursor
    # -----------------------------------------------------------------------

    def get_last_sync_timestamp(self) -> str | None:
        """Get the timestamp of the last successful sync."""
        try:
            response = self._state_table.get_item(Key={"pk": "SYNC_CURSOR", "sk": "METADATA"})
            item = response.get("Item")
            return _as_str(item.get("last_sync_timestamp")) if item else None
        except ClientError:
            logger.warning("Failed to read sync cursor from DynamoDB")
            return None

    def set_last_sync_timestamp(self, timestamp: str | None = None) -> None:
        """Set the last successful sync timestamp.

        The cursor is consumed by ``govwin_orchestrator`` via the GovWin WSAPI
        ``oppSelectionDateFrom`` parameter, which only accepts ``MM/DD/YYYY``.
        Storing any other format silently breaks the next discovery pass: the
        WSAPI returns 400 / zero results and the orchestrator stops finding
        anything to sync. Pin the format here so that constraint cannot drift
        based on which caller wrote the row.
        """
        if timestamp is None:
            timestamp = datetime.now(UTC).strftime("%m/%d/%Y")
        else:
            # Validate the caller's input matches the WSAPI contract.
            datetime.strptime(timestamp, "%m/%d/%Y")

        self._state_table.put_item(
            Item={
                "pk": "SYNC_CURSOR",
                "sk": "METADATA",
                "last_sync_timestamp": timestamp,
                "updated_at": datetime.now(UTC).isoformat(),
            }
        )

    # -----------------------------------------------------------------------
    # Per-Opportunity State
    # -----------------------------------------------------------------------

    def get_opp_update_date(self, govwin_opp_id: str) -> str | None:
        """Get the stored updateDate for an opportunity."""
        try:
            response = self._state_table.get_item(
                Key={"pk": f"OPP#{govwin_opp_id}", "sk": "METADATA"}
            )
            item = response.get("Item")
            return _as_str(item.get("govwin_update_date")) if item else None
        except ClientError:
            return None

    def set_opp_state(
        self,
        govwin_opp_id: str,
        govwin_update_date: str,
        hubspot_deal_id: str | None = None,
    ) -> None:
        """Store the sync state for an opportunity."""
        item: dict[str, Any] = {
            "pk": f"OPP#{govwin_opp_id}",
            "sk": "METADATA",
            "govwin_update_date": govwin_update_date,
            "last_synced": datetime.now(UTC).isoformat(),
            "ttl": int(time.time()) + 180 * 86400,
        }
        if hubspot_deal_id:
            item["hubspot_deal_id"] = hubspot_deal_id

        self._state_table.put_item(Item=item)

    def get_opp_hubspot_id(self, govwin_opp_id: str) -> str | None:
        """Get the HubSpot deal ID for a GovWin opportunity."""
        try:
            response = self._state_table.get_item(
                Key={"pk": f"OPP#{govwin_opp_id}", "sk": "METADATA"}
            )
            item = response.get("Item")
            return _as_str(item.get("hubspot_deal_id")) if item else None
        except ClientError:
            return None

    def batch_get_opp_update_dates(self, govwin_opp_ids: list[str]) -> dict[str, str]:
        """Get stored updateDates for multiple opportunities at once."""
        result: dict[str, str] = {}

        # DynamoDB batch_get_item supports max 100 keys per request
        for i in range(0, len(govwin_opp_ids), 100):
            batch = govwin_opp_ids[i : i + 100]
            request_items: dict[str, Any] = {
                self._config.aws.sync_state_table: {
                    "Keys": [{"pk": f"OPP#{opp_id}", "sk": "METADATA"} for opp_id in batch]
                }
            }

            try:
                while request_items:
                    response = self._dynamodb.batch_get_item(RequestItems=request_items)
                    items = response.get("Responses", {}).get(self._config.aws.sync_state_table, [])
                    for item in items:
                        pk_value = item["pk"]
                        opp_id = pk_value.replace("OPP#", "") if isinstance(pk_value, str) else None
                        update_date = _as_str(item.get("govwin_update_date"))
                        if opp_id and update_date:
                            result[opp_id] = update_date

                    # Retry any unprocessed keys
                    request_items = response.get("UnprocessedKeys", {})
                    if request_items:
                        table = self._config.aws.sync_state_table
                        table_entry: Any = request_items.get(table, {})
                        unprocessed = table_entry.get("Keys", []) if table_entry else []
                        logger.warning("Retrying %d unprocessed keys", len(unprocessed))
            except ClientError:
                logger.warning("Failed to batch read opp update dates")

        return result

    # -----------------------------------------------------------------------
    # Entity Mappings
    # -----------------------------------------------------------------------

    def get_entity_hubspot_id(self, govwin_type: str, govwin_id: str) -> str | None:
        """Get the HubSpot ID for a GovWin entity."""
        try:
            response = self._mappings_table.get_item(
                Key={
                    "pk": f"{govwin_type}#{govwin_id}",
                    "sk": "HUBSPOT_MAPPING",
                }
            )
            item = response.get("Item")
            return _as_str(item.get("hubspot_id")) if item else None
        except ClientError:
            return None

    def set_entity_mapping(
        self,
        govwin_type: str,
        govwin_id: str,
        hubspot_id: str,
    ) -> None:
        """Store a mapping between a GovWin entity and HubSpot object."""
        self._mappings_table.put_item(
            Item={
                "pk": f"{govwin_type}#{govwin_id}",
                "sk": "HUBSPOT_MAPPING",
                "hubspot_id": hubspot_id,
                "last_synced": datetime.now(UTC).isoformat(),
                "ttl": int(time.time()) + 180 * 86400,
            }
        )

    def batch_set_entity_mappings(
        self,
        mappings: list[tuple[str, str, str]],
    ) -> None:
        """Batch write entity mappings. Each tuple is (govwin_type, govwin_id, hubspot_id)."""
        with self._mappings_table.batch_writer() as writer:
            for govwin_type, govwin_id, hubspot_id in mappings:
                writer.put_item(
                    Item={
                        "pk": f"{govwin_type}#{govwin_id}",
                        "sk": "HUBSPOT_MAPPING",
                        "hubspot_id": hubspot_id,
                        "last_synced": datetime.now(UTC).isoformat(),
                        "ttl": int(time.time()) + 180 * 86400,
                    }
                )

    # -----------------------------------------------------------------------
    # ACE (AWS Partner Central) Mappings
    # -----------------------------------------------------------------------

    def get_ace_mapping(self, govwin_id: str) -> dict[str, Any] | None:
        """Return the ACE record for a GovWin opportunity, or None if not submitted.

        :param govwin_id: GovWin global opportunity id.
        :returns: A dict copy of the DynamoDB item, or None if the row
            does not exist or DynamoDB returned an error (logged).
        """
        try:
            response = self._mappings_table.get_item(
                Key={"pk": f"ACE#{govwin_id}", "sk": "MAPPING"}
            )
            item = response.get("Item")
            return dict(item) if item else None
        except ClientError:
            logger.warning("Failed to read ACE mapping for %s", govwin_id)
            return None

    def update_ace_mapping(
        self,
        govwin_id: str,
        *,
        ace_opportunity_id: str | None = None,
        last_modified_date: str | None = None,
        ace_engagement_invitation_id: str | None = None,
        ace_task_id: str | None = None,
        ace_task_client_token: str | None = None,
        client_token: str | None = None,
        hubspot_deal_id: str | None = None,
    ) -> None:
        """Merge the supplied ACE fields into the mapping for ``govwin_id``.

        Uses ``UpdateItem`` with SET expressions so that fields written
        by an earlier step (CreateOpportunity, AssociateOpportunity) are
        preserved when a later step (StartEngagement) writes its result.

        :param govwin_id: GovWin global opportunity id.
        :param ace_opportunity_id: AWS opportunity id from
            CreateOpportunity.
        :param last_modified_date: ``LastModifiedDate`` echo for
            optimistic locking on the next UpdateOpportunity.
        :param ace_engagement_invitation_id: Invitation id from the
            StartEngagement response.
        :param ace_task_id: Task arn from the StartEngagement response.
        :param ace_task_client_token: Idempotency token persisted for
            StartEngagement retries.
        :param client_token: CreateOpportunity idempotency token.
        :param hubspot_deal_id: Originating HubSpot deal id; used for
            reverse-lookup from inbound HubSpot webhook events.
        :returns: None. ``None``-valued args are skipped.
        """
        updates: dict[str, Any] = {
            "updated_at": datetime.now(UTC).isoformat(),
            "ttl": int(time.time()) + 365 * 86400,
        }
        if ace_opportunity_id is not None:
            updates["ace_opportunity_id"] = ace_opportunity_id
        if last_modified_date is not None:
            updates["last_modified_date"] = last_modified_date
        if ace_engagement_invitation_id is not None:
            updates["ace_engagement_invitation_id"] = ace_engagement_invitation_id
        if ace_task_id is not None:
            updates["ace_task_id"] = ace_task_id
        if ace_task_client_token is not None:
            updates["ace_task_client_token"] = ace_task_client_token
        if client_token is not None:
            updates["client_token"] = client_token
        if hubspot_deal_id is not None:
            updates["hubspot_deal_id"] = hubspot_deal_id

        names = {f"#k{i}": k for i, k in enumerate(updates)}
        values = {f":v{i}": v for i, (_, v) in enumerate(updates.items())}
        set_expr = ", ".join(f"{name} = :v{i}" for i, name in enumerate(names))
        self._mappings_table.update_item(
            Key={"pk": f"ACE#{govwin_id}", "sk": "MAPPING"},
            UpdateExpression=f"SET {set_expr}",
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )

        # Maintain reverse-lookup records so find_govwin_by_invitation_id
        # and find_govwin_by_hubspot_deal_id can use O(1) GetItem instead
        # of an expensive table Scan. Refresh BOTH reverse-index rows on
        # every update regardless of which fields the caller passed --
        # otherwise the reverse rows decay on a separate TTL clock from
        # the forward row and can expire while the mapping is still live.
        existing = self.get_ace_mapping(govwin_id) or {}
        inv_id = ace_engagement_invitation_id or str(
            existing.get("ace_engagement_invitation_id") or ""
        )
        deal_id = hubspot_deal_id or str(existing.get("hubspot_deal_id") or "")
        if inv_id:
            self._put_reverse_index(f"INV#{inv_id}", govwin_id)
        if deal_id:
            self._put_reverse_index(f"DEAL#{deal_id}", govwin_id)

    def add_pending_reconcile_props(self, govwin_id: str, props: set[str]) -> None:
        """Record HubSpot properties whose ACE update was deferred during AWS review.

        AWS rejects ``UpdateOpportunity`` while the opportunity's review
        status is Submitted/In review/Rejected. Rather than drop the edit,
        ``update_in_ace`` parks the changed property names here; they are
        replayed once the opportunity becomes editable (see
        :mod:`src.ace.reconcile`).

        Uses ``ADD`` on a DynamoDB String Set so concurrent webhook
        fan-out writes union atomically instead of clobbering one another.
        The mapping ``ttl`` is refreshed in the same call to keep the row
        alive through the (hours-to-days) review window.

        :param govwin_id: GovWin global opportunity id.
        :param props: HubSpot property names to defer; empty sets are
            ignored (a String Set cannot be empty).
        :returns: None.
        """
        if not props:
            return
        self._mappings_table.update_item(
            Key={"pk": f"ACE#{govwin_id}", "sk": "MAPPING"},
            UpdateExpression="ADD pending_reconcile_props :p SET #ttl = :ttl",
            ExpressionAttributeNames={"#ttl": "ttl"},
            ExpressionAttributeValues={
                ":p": set(props),
                ":ttl": int(time.time()) + 365 * 86400,
            },
        )

    def clear_pending_reconcile_props(self, govwin_id: str) -> None:
        """Remove the deferred-property set after a successful reconcile.

        Removes the attribute entirely (a DynamoDB String Set cannot be
        stored empty), making a redelivered reconcile event a no-op.

        :param govwin_id: GovWin global opportunity id.
        :returns: None.
        """
        self._mappings_table.update_item(
            Key={"pk": f"ACE#{govwin_id}", "sk": "MAPPING"},
            UpdateExpression="REMOVE pending_reconcile_props",
        )

    def scan_pending_reconcile(self) -> list[dict[str, Any]]:
        """Return every ACE mapping row that still has deferred properties.

        Backstop for the best-effort EventBridge trigger: the scheduled
        sweep uses this to find opportunities whose deferred edits were
        never replayed because the ``Opportunity Updated`` event was not
        delivered. A ``Scan`` is adequate at current table scale; a sparse
        GSI on ``pending_reconcile_props`` is the documented scale-up path.

        :returns: A list of mapping items (sk == ``MAPPING``) that carry a
            non-empty ``pending_reconcile_props`` set. Empty on error
            (logged).
        """
        items: list[dict[str, Any]] = []
        try:
            kwargs: dict[str, Any] = {
                "FilterExpression": ("sk = :mapping AND attribute_exists(pending_reconcile_props)"),
                "ExpressionAttributeValues": {":mapping": "MAPPING"},
            }
            while True:
                response = self._mappings_table.scan(**kwargs)
                items.extend(dict(i) for i in response.get("Items", []))
                last_key = response.get("LastEvaluatedKey")
                if not last_key:
                    break
                kwargs["ExclusiveStartKey"] = last_key
        except ClientError:
            logger.exception("scan_pending_reconcile failed")
            return []
        return items

    def reserve_client_token(self, govwin_id: str, client_token: str) -> str:
        """Atomically reserve a ClientToken for a pending CreateOpportunity.

        Uses a conditional ``put_item`` so two concurrent SQS deliveries
        for the same deal cannot both reserve different tokens (which
        would otherwise mint two ACE opportunities for one GovWin opp).
        On contention, falls back to reading the winning token.

        :param govwin_id: GovWin global opportunity id.
        :param client_token: Caller-generated UUID-style idempotency
            token; only persisted when no token has been reserved yet.
        :returns: The token now persisted for ``govwin_id``: either the
            supplied ``client_token`` (first-write wins) or the token an
            earlier concurrent caller already reserved.
        :raises botocore.exceptions.ClientError: For any DynamoDB
            failure other than ``ConditionalCheckFailedException``.
        """
        try:
            self._mappings_table.put_item(
                Item={
                    "pk": f"ACE#{govwin_id}",
                    "sk": "MAPPING",
                    "client_token": client_token,
                    "ace_opportunity_id": "",
                    "reserved_at": datetime.now(UTC).isoformat(),
                    "ttl": int(time.time()) + 365 * 86400,
                },
                ConditionExpression=(
                    "attribute_not_exists(pk) OR attribute_not_exists(client_token)"
                ),
            )
            return client_token
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code != "ConditionalCheckFailedException":
                raise
            existing = self.get_ace_mapping(govwin_id) or {}
            return str(existing.get("client_token") or client_token)

    def reserve_task_client_token(self, govwin_id: str, client_token: str) -> str:
        """Reserve a ClientToken for the StartEngagementFromOpportunityTask call.

        Same idempotency guarantee as :meth:`reserve_client_token` but
        scoped to the engagement-task token so retries reuse it instead
        of regenerating.

        :param govwin_id: GovWin global opportunity id.
        :param client_token: Caller-generated idempotency token.
        :returns: The persisted task ClientToken (existing value wins
            on contention).
        :raises botocore.exceptions.ClientError: For any DynamoDB
            failure other than ``ConditionalCheckFailedException``.
        """
        existing = self.get_ace_mapping(govwin_id) or {}
        token = existing.get("ace_task_client_token")
        if token:
            return str(token)
        try:
            self._mappings_table.update_item(
                Key={"pk": f"ACE#{govwin_id}", "sk": "MAPPING"},
                UpdateExpression="SET ace_task_client_token = :t",
                ConditionExpression="attribute_not_exists(ace_task_client_token)",
                ExpressionAttributeValues={":t": client_token},
            )
            return client_token
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code != "ConditionalCheckFailedException":
                raise
            refreshed = self.get_ace_mapping(govwin_id) or {}
            return str(refreshed.get("ace_task_client_token") or client_token)

    def find_govwin_by_invitation_id(self, invitation_id: str) -> str | None:
        """Locate the GovWin id whose ACE mapping holds this engagement invitation.

        Uses an O(1) GetItem against a reverse-index record written by
        :meth:`update_ace_mapping` whenever
        ``ace_engagement_invitation_id`` is set. The reverse record's
        pk is ``INV#<invitation_id>`` and its body carries
        ``govwin_id``.

        :param invitation_id: AWS Engagement Invitation id.
        :returns: The mapped GovWin id, or None when no reverse-index
            row exists or the DynamoDB read fails (logged).
        """
        try:
            response = self._mappings_table.get_item(
                Key={"pk": f"INV#{invitation_id}", "sk": "REVERSE"}
            )
        except ClientError:
            logger.exception("get reverse-index for invitation %s failed", invitation_id)
            return None
        item = response.get("Item")
        return _as_str(item.get("govwin_id")) if item else None

    def find_govwin_by_hubspot_deal_id(self, hubspot_deal_id: str) -> str | None:
        """Locate the GovWin id whose ACE mapping points at this HubSpot deal.

        O(1) GetItem against a reverse-index record (pk
        ``DEAL#<hubspot_deal_id>``) written by
        :meth:`update_ace_mapping`.

        :param hubspot_deal_id: HubSpot deal object id.
        :returns: The mapped GovWin id, or None when no reverse-index
            row exists or the DynamoDB read fails (logged).
        """
        try:
            response = self._mappings_table.get_item(
                Key={"pk": f"DEAL#{hubspot_deal_id}", "sk": "REVERSE"}
            )
        except ClientError:
            logger.exception("get reverse-index for hubspot deal %s failed", hubspot_deal_id)
            return None
        item = response.get("Item")
        return _as_str(item.get("govwin_id")) if item else None

    def _put_reverse_index(self, pk: str, govwin_id: str) -> None:
        """Write a reverse-lookup record. Uses the same TTL as the forward record.

        Unconditional ``put_item`` is correct here: ``INV#<id>`` ->
        ``govwin_id`` and ``DEAL#<id>`` -> ``govwin_id`` are 1:1 by
        contract (AWS Engagement Invitation ids and HubSpot deal ids
        never re-bind to a different opportunity). Two concurrent writes
        for the same key carry the same govwin_id, so the put is
        idempotent and a ``ConditionExpression`` would add no safety.
        """
        self._mappings_table.put_item(
            Item={
                "pk": pk,
                "sk": "REVERSE",
                "govwin_id": govwin_id,
                "updated_at": datetime.now(UTC).isoformat(),
                "ttl": int(time.time()) + 365 * 86400,
            }
        )

    def is_event_seen(self, event_id: str) -> bool:
        """Return True if we have already processed this EventBridge event id."""
        try:
            response = self._mappings_table.get_item(Key={"pk": f"EVT#{event_id}", "sk": "SEEN"})
            return response.get("Item") is not None
        except ClientError:
            return False

    def mark_event_seen_atomic(self, event_id: str, ttl_seconds: int = 86400) -> bool:
        """Atomically mark an EventBridge event id as seen.

        Combines the prior is_event_seen + mark_event_seen pair into a
        single conditional write to eliminate the TOCTOU window.

        :param event_id: EventBridge event id (must be globally unique
            for the dedup window).
        :param ttl_seconds: Row TTL; defaults to 24 hours, which matches
            AWS' redelivery guarantee.
        :returns: True on first sighting (caller should process the
            event); False if the event was already marked (caller
            should skip).
        :raises botocore.exceptions.ClientError: For any DynamoDB
            failure other than ``ConditionalCheckFailedException``.
        """
        try:
            self._mappings_table.put_item(
                Item={
                    "pk": f"EVT#{event_id}",
                    "sk": "SEEN",
                    "seen_at": datetime.now(UTC).isoformat(),
                    "ttl": int(time.time()) + ttl_seconds,
                },
                ConditionExpression="attribute_not_exists(pk)",
            )
            return True
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code == "ConditionalCheckFailedException":
                return False
            raise

    def reserve_webhook_signature(self, signature_fingerprint: str, ttl_seconds: int = 600) -> bool:
        """Atomically reserve a webhook signature to defeat replay attacks.

        :param signature_fingerprint: Hash of the
            ``X-HubSpot-Signature-v3`` header. Do NOT pass the raw
            signature: it has the same length and entropy as the
            secret-derived MAC and might leak through logs. 32-byte
            SHA-256 hex is appropriate.
        :param ttl_seconds: Row TTL. Must be at least the receiver's
            accepted signature age window so replays inside the window
            are caught even if the original delivery has already been
            processed.
        :returns: True on first sighting (caller should accept the
            delivery); False if the same signature has been seen within
            the TTL window (caller should reject as a replay).
        :raises botocore.exceptions.ClientError: For any DynamoDB
            failure other than ``ConditionalCheckFailedException``.
        """
        try:
            self._mappings_table.put_item(
                Item={
                    "pk": f"WHK#{signature_fingerprint}",
                    "sk": "SEEN",
                    "seen_at": datetime.now(UTC).isoformat(),
                    "ttl": int(time.time()) + ttl_seconds,
                },
                ConditionExpression="attribute_not_exists(pk)",
            )
            return True
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code == "ConditionalCheckFailedException":
                return False
            raise
