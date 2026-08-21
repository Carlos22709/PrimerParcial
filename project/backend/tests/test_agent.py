"""Tests for the UCS Emergency Control agent."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from agent import initial_state, solve  # noqa: E402
from simulator import goal_satisfied, load_scenario, simulate  # noqa: E402


def test_ucs_plan_is_legal_and_reaches_goal() -> None:
    scenario = load_scenario()
    result = solve(scenario)

    assert result["solution_found"] is True
    assert result["total_cost"] == sum(step["cost"] for step in result["steps"])

    final = simulate(scenario, result["steps"])
    assert goal_satisfied(scenario, final)
    assert final["energy_spent"] == result["total_cost"]


def test_response_uses_only_contract_operations() -> None:
    scenario = load_scenario()
    result = solve(scenario)

    assert {step["op"] for step in result["steps"]} <= {
        "MOVE", "PICKUP", "DROP", "INTERACT"
    }

    allowed_interactions = {"OPEN_DOOR", "REPAIR", "ACTIVATE", "RECHARGE"}
    assert {
        step["action"]
        for step in result["steps"]
        if step["op"] == "INTERACT"
    } <= allowed_interactions


def test_initial_state_is_hashable_and_canonical() -> None:
    scenario = load_scenario()
    state = initial_state(scenario)

    assert hash(state)
    assert state.keys == tuple(sorted(state.keys))
    assert state.tools == tuple(sorted(state.tools))
    assert state.ground_keys == tuple(sorted(state.ground_keys))
    assert state.ground_tools == tuple(sorted(state.ground_tools))


def test_failure_when_goal_is_impossible() -> None:
    scenario = load_scenario()
    impossible = json.loads(json.dumps(scenario))

    # Remove the only tool needed for PANEL_C. The agent must not fabricate it.
    impossible["tools"] = [
        tool for tool in impossible["tools"] if tool["id"] != "WIRE_CUTTER"
    ]

    result = solve(impossible)

    assert result["solution_found"] is False
    assert result["steps"] == []
    assert result["total_cost"] == 0
