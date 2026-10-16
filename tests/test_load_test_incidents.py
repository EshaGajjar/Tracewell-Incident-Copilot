"""Offline checks for the incident load-test scenario catalogue."""
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).with_name("load_test_incidents.py")
SPEC = importlib.util.spec_from_file_location("load_test_incidents", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_scenario_catalogue_has_varied_incident_categories():
    categories = {item["category"] for item in module.SCENARIOS}
    assert len(module.SCENARIOS) >= 8
    assert len(categories) >= 6


def test_every_scenario_has_actionable_resolution_content():
    for scenario in module.SCENARIOS:
        assert len(scenario["title"]) >= 10
        assert len(scenario["description"]) >= 20
        assert len(scenario["root_cause"]) >= 10
        assert len(scenario["resolution"]) >= 30
        assert any(word in scenario["resolution"].lower()
                   for word in ("verified", "confirm", "check", "test", "monitor"))


def test_service_catalogue_has_multiple_services():
    assert len(set(module.SERVICES)) >= 6
