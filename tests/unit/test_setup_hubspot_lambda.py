"""Fail-loud coverage for setup_hubspot's govwin_ace_solution_id seeding.

The production cutover (ACE_CATALOG Sandbox -> AWS) surfaced a silent
failure: when setup_hubspot could not enumerate the production solution
set, it swallowed the error and seeded the ``_NONE_REGISTERED_``
placeholder, so every card submit then 400'd on INVALID_OPTION. These
tests pin the corrected behavior:

* Sandbox: an empty / errored ListSolutions is normal -> seed the
  placeholder, never raise.
* AWS (production): an empty / errored ListSolutions is a
  misconfiguration -> raise so the Lambda returns FunctionError and the
  terraform_data.setup_hubspot provisioner aborts the apply.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from src.ace.client import ACEAPIError
from src.config import AppConfig
from src.lambdas import setup_hubspot as setup_mod


def _config(app_config: AppConfig, catalog: str) -> AppConfig:
    """Clone the shared app_config with the requested ACE catalog."""
    return dataclasses.replace(app_config, ace=dataclasses.replace(app_config.ace, catalog=catalog))


class _ExplodingACE:
    def __init__(self, *_: Any, **__: Any) -> None:
        pass

    def list_active_solutions(self) -> list:
        raise ACEAPIError("ListSolutions denied", code="AccessDeniedException")


class _ActiveACE:
    def __init__(self, *_: Any, **__: Any) -> None:
        pass

    def list_active_solutions(self) -> list:
        return [{"Id": "S-0051246", "Name": "Pandora Cloud", "Category": "Migration"}]


# --- _solution_options ----------------------------------------------------


def test_solution_options_reraises_listsolutions_error_in_production(
    app_config: AppConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(setup_mod, "ACEClient", _ExplodingACE)
    with pytest.raises(ACEAPIError):
        setup_mod._solution_options(_config(app_config, "AWS"))


def test_solution_options_swallows_listsolutions_error_in_sandbox(
    app_config: AppConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(setup_mod, "ACEClient", _ExplodingACE)
    assert setup_mod._solution_options(_config(app_config, "Sandbox")) == []


def test_solution_options_maps_active_solutions(
    app_config: AppConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(setup_mod, "ACEClient", _ActiveACE)
    options = setup_mod._solution_options(_config(app_config, "AWS"))
    assert options == [{"label": "Pandora Cloud (Migration)", "value": "S-0051246"}]


# --- handler ---------------------------------------------------------------


def test_handler_raises_on_empty_production_solutions(
    app_config: AppConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AWS catalog + empty solution set must abort setup, not seed the
    placeholder. The RuntimeError precedes any HubSpot write."""
    monkeypatch.setattr(setup_mod, "load_config", lambda: _config(app_config, "AWS"))
    monkeypatch.setattr(setup_mod, "_aws_product_options", lambda: [])
    monkeypatch.setattr(setup_mod, "_patch_property_options", lambda *a, **k: None)
    monkeypatch.setattr(setup_mod, "_solution_options", lambda _config: [])

    def _no_hubspot(*_: Any, **__: Any) -> None:
        raise AssertionError("HubSpot setup must not run when production solutions are empty")

    monkeypatch.setattr(setup_mod, "HubSpotClient", _no_hubspot)

    with pytest.raises(RuntimeError, match="no Active solutions"):
        setup_mod.handler({}, None)


def test_handler_seeds_placeholder_in_sandbox_when_empty(
    app_config: AppConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sandbox + empty solution set seeds the _NONE_REGISTERED_ placeholder
    so the enum property can still be created, and setup proceeds."""
    monkeypatch.setattr(setup_mod, "load_config", lambda: _config(app_config, "Sandbox"))
    monkeypatch.setattr(setup_mod, "_aws_product_options", lambda: [])
    monkeypatch.setattr(setup_mod, "_solution_options", lambda _config: [])

    seeded: dict[str, list[dict[str, str]]] = {}

    def _capture(_props: Any, name: str, options: list[dict[str, str]]) -> None:
        seeded[name] = options

    monkeypatch.setattr(setup_mod, "_patch_property_options", _capture)

    class _StubHubSpot:
        def __init__(self, *_: Any, **__: Any) -> None:
            pass

        def __enter__(self) -> _StubHubSpot:
            return self

        def __exit__(self, *_: Any) -> None:
            return None

        def setup(self) -> dict[str, Any]:
            return {
                "pipeline_id": "default",
                "deal_properties": 1,
                "company_properties": 0,
                "contact_properties": 0,
            }

    monkeypatch.setattr(setup_mod, "HubSpotClient", _StubHubSpot)

    result = setup_mod.handler({}, None)

    assert result["pipeline_id"] == "default"
    assert seeded["govwin_ace_solution_id"] == [
        {"label": "(no AWS solutions registered in this catalog)", "value": "_NONE_REGISTERED_"}
    ]
