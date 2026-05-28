"""Coverage for M1: ACEClient.get_opportunity catalog echo check.

Defense-in-depth on top of the IAM Catalog condition. If a regressed
AWS response ever returned a cross-catalog opportunity, the
update_in_ace scrub-and-update path would silently echo it back to the
wrong catalog. The IAM Catalog condition denies the write so no data
goes anywhere wrong, but the error path would be confusing. The echo
check makes the mismatch a hard error at read time.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.ace.client import ACEAPIError, ACEClient
from src.config import AppConfig


@pytest.fixture
def client(app_config: AppConfig) -> ACEClient:
    return ACEClient(app_config, boto3_client=MagicMock())


def test_matching_catalog_returns_response(client: ACEClient) -> None:
    client._client.get_opportunity.return_value = {  # type: ignore[attr-defined]
        "Id": "O1",
        "Catalog": "Sandbox",
        "LastModifiedDate": "2026-05-28T00:00:00Z",
    }
    out = client.get_opportunity("O1")
    assert out["Id"] == "O1"
    assert out["Catalog"] == "Sandbox"


def test_missing_catalog_field_is_accepted(client: ACEClient) -> None:
    """An AWS response without the Catalog field (older API revision)
    must NOT trigger the mismatch error -- it's not a cross-catalog
    bleed, just incomplete data."""
    client._client.get_opportunity.return_value = {  # type: ignore[attr-defined]
        "Id": "O1",
        "LastModifiedDate": "2026-05-28T00:00:00Z",
    }
    out = client.get_opportunity("O1")
    assert out["Id"] == "O1"


def test_cross_catalog_response_raises(client: ACEClient) -> None:
    """A Sandbox client receiving an AWS-catalog opp must raise."""
    client._client.get_opportunity.return_value = {  # type: ignore[attr-defined]
        "Id": "O-PROD-1",
        "Catalog": "AWS",
    }
    with pytest.raises(ACEAPIError) as exc:
        client.get_opportunity("O-PROD-1")
    assert exc.value.code == "CrossCatalogResponse"
    assert "Sandbox" in str(exc.value)
    assert "AWS" in str(exc.value)
