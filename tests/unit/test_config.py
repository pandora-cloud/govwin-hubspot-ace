"""``load_config`` rejects missing required env vars.

Defaulting table / secret names to LocalStack-era literals silently
masked production misconfiguration: a script running outside the
Makefile (which threads the production prefix via SCRIPT_ENV) would
point at nonexistent tables, return empty results, and look like the
data was missing. Each required env var must raise ``KeyError`` at
``load_config`` time so the misconfiguration is loud.
"""

from __future__ import annotations

import pytest

from src.config import load_config

REQUIRED_ENV: tuple[str, ...] = (
    "SYNC_STATE_TABLE",
    "ENTITY_MAPPINGS_TABLE",
)

# Only the two DynamoDB table-name vars are required. A typo would
# otherwise silently target a nonexistent table and the
# ClientError-swallowing read paths in src/sync/state.py would return
# None as if the row didn't exist. Secret-name vars are NOT required:
# a wrong value fails loud at the next ``get_secret_value`` call
# (ResourceNotFoundException), and several Lambdas legitimately don't
# have all four secret names in their Terraform env block (the ACE
# Lambdas never read GovWin secrets; setup_hubspot / orchestrator /
# worker don't read the webhook secret).


@pytest.fixture
def populated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in REQUIRED_ENV:
        monkeypatch.setenv(name, f"test-{name.lower()}")


def test_load_config_succeeds_with_all_required_env(populated_env: None) -> None:
    config = load_config()
    assert config.aws.sync_state_table == "test-sync_state_table"
    assert config.aws.entity_mappings_table == "test-entity_mappings_table"


@pytest.mark.parametrize("missing", REQUIRED_ENV)
def test_load_config_raises_when_required_var_missing(
    populated_env: None, monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    monkeypatch.delenv(missing, raising=False)
    with pytest.raises(KeyError) as exc:
        load_config()
    assert missing in str(exc.value)
