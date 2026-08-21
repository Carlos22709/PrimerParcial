"""Emergency Control — UCS agent planner.

Implements the state model, transition rules and UCS search described in design.md
and emits only operations accepted by CONTRATO.md.
"""

from __future__ import annotations

from dataclasses import dataclass
import heapq
from collections import Counter
from typing import Any, Optional


@dataclass(frozen=True)
class WorldSignature:
    zone: str
    inv_keys: tuple[str, ...]
    inv_tools: tuple[str, ...]
    inv_materials: tuple[tuple[str, int], ...]
    ground_keys: tuple[tuple[str, str], ...]
    ground_tools: tuple[tuple[str, str], ...]
    ground_materials: tuple[tuple[str, str, int], ...]
    open_doors: frozenset[str]
    repaired_panels: frozenset[str]
    online_stations: frozenset[str]


@dataclass(frozen=True)
class State:
    zone: str
    battery: int
    inv_keys: tuple[str, ...]
    inv_tools: tuple[str, ...]
    inv_materials: tuple[tuple[str, int], ...]
    ground_keys: tuple[tuple[str, str], ...]
    ground_tools: tuple[tuple[str, str], ...]
    ground_materials: tuple[tuple[str, str, int], ...]
    open_doors: frozenset[str]
    repaired_panels: frozenset[str]
    online_stations: frozenset[str]

    @property
    def signature(self) -> WorldSignature:
        """Same physical world, ignoring battery for dominance checks."""
        return WorldSignature(
            zone=self.zone,
            inv_keys=self.inv_keys,
            inv_tools=self.inv_tools,
            inv_materials=self.inv_materials,
            ground_keys=self.ground_keys,
            ground_tools=self.ground_tools,
            ground_materials=self.ground_materials,
            open_doors=self.open_doors,
            repaired_panels=self.repaired_panels,
            online_stations=self.online_stations,
        )


@dataclass
class SearchNode:
    state: State
    g: int
    parent: Optional["SearchNode"] = None
    step: Optional[dict[str, Any]] = None


class DominanceTracker:
    """Non-dominated (battery, g) pairs for each battery-free world signature."""

    def __init__(self) -> None:
        self.frontiers: dict[WorldSignature, list[tuple[int, int]]] = {}

    def is_dominated(self, signature: WorldSignature, battery: int, g: int) -> bool:
        for old_battery, old_g in self.frontiers.get(signature, []):
            if old_battery >= battery and old_g <= g:
                return True
        return False

    def add(self, signature: WorldSignature, battery: int, g: int) -> None:
        frontier = self.frontiers.setdefault(signature, [])
        frontier[:] = [
            (old_battery, old_g)
            for old_battery, old_g in frontier
            if not (battery >= old_battery and g <= old_g)
        ]
        frontier.append((battery, g))

    def is_current(self, signature: WorldSignature, battery: int, g: int) -> bool:
        """False when this heap entry was later dominated by a better arrival."""
        return (battery, g) in self.frontiers.get(signature, [])


class ScenarioProblem:
    def __init__(self, scenario: dict[str, Any]) -> None:
        self.scenario = scenario
        self.robot = scenario["robot"]

        self.battery_max = int(self.robot["battery_max"])
        self.cargo_capacity = int(self.robot["cargo_capacity"])

        costs = scenario.get("action_costs", {})
        self.pickup_cost = int(costs["pickup"])
        self.drop_cost = int(costs["drop"])
        self.interact_cost = int(costs["interact"])
        self.recharge_cost = int(costs["recharge"])

        self.keys = scenario.get("keys", [])
        self.tools = scenario.get("tools", [])
        self.materials = scenario.get("materials", [])
        self.doors = scenario.get("doors", [])
        self.panels = scenario.get("panels", [])
        self.stations = scenario.get("stations", [])
        self.chargers = scenario.get("chargers", [])
        self.zones = scenario.get("zones", [])

        self.goal_stations = frozenset(
            scenario.get("goal", {}).get("stations_online", [])
        )

        self.key_weights = {
            item["id"]: int(item.get("weight", 1)) for item in self.keys
        }
        self.tool_weights = {
            item["id"]: int(item.get("weight", 1)) for item in self.tools
        }
        self.material_weights = {
            item["type"]: int(item.get("weight", 1)) for item in self.materials
        }

        self.corridors_by_from: dict[str, list[dict[str, Any]]] = {}
        for corridor in scenario.get("corridors", []):
            self.corridors_by_from.setdefault(corridor["from"], []).append(corridor)

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------

    def initial_state(self) -> State:
        ground_keys = tuple(
            sorted((item["id"], item["zone"]) for item in self.keys)
        )
        ground_tools = tuple(
            sorted((item["id"], item["zone"]) for item in self.tools)
        )

        # Aggregate equivalent materials by (zone, type).
        material_counts: Counter[tuple[str, str]] = Counter()
        for item in self.materials:
            count = int(item.get("count", 0))
            if count > 0:
                material_counts[(item["zone"], item["type"])] += count

        ground_materials = tuple(
            sorted(
                (zone, material_type, count)
                for (zone, material_type), count in material_counts.items()
                if count > 0
            )
        )

        state = State(
            zone=self.robot["start"],
            battery=int(self.robot["battery_start"]),
            inv_keys=(),
            inv_tools=(),
            inv_materials=(),
            ground_keys=ground_keys,
            ground_tools=ground_tools,
            ground_materials=ground_materials,
            open_doors=frozenset(
                door["id"]
                for door in self.doors
                if door.get("state") == "OPEN"
            ),
            repaired_panels=frozenset(
                panel["id"]
                for panel in self.panels
                if panel.get("state") in {"OK", "REPAIRED"}
            ),
            online_stations=frozenset(
                station["id"]
                for station in self.stations
                if station.get("state") == "ONLINE"
            ),
        )
        return self.canonicalize(state)

    def is_goal(self, state: State) -> bool:
        return self.goal_stations.issubset(state.online_stations)

    def item_weight(self, item: str) -> int:
        if item in self.key_weights:
            return self.key_weights[item]
        if item in self.tool_weights:
            return self.tool_weights[item]
        return self.material_weights.get(item, 1)

    def payload_weight(self, state: State) -> int:
        total = sum(self.key_weights.get(item, 1) for item in state.inv_keys)
        total += sum(self.tool_weights.get(item, 1) for item in state.inv_tools)
        total += sum(
            self.material_weights.get(material_type, 1) * count
            for material_type, count in state.inv_materials
        )
        return total

    def pending_material_demand(self, state: State) -> Counter[str]:
        return Counter(
            panel["requires"]["material"]
            for panel in self.panels
            if panel["id"] not in state.repaired_panels
        )

    def key_is_relevant(self, key_id: str, state: State) -> bool:
        return any(
            door["key"] == key_id and door["id"] not in state.open_doors
            for door in self.doors
        )

    def tool_is_relevant(self, tool_id: str, state: State) -> bool:
        return any(
            panel["requires"]["tool"] == tool_id
            and panel["id"] not in state.repaired_panels
            for panel in self.panels
        )

    def material_pickup_is_relevant(self, material_type: str, state: State) -> bool:
        needed = self.pending_material_demand(state)[material_type]
        carried = dict(state.inv_materials).get(material_type, 0)
        return carried < needed

    def canonicalize(self, state: State) -> State:
        """Remove ground information that can no longer affect the future.

        Objects still carried are kept because they continue to occupy capacity.
        Once an object is both irrelevant and outside the payload, its exact
        ground position is intentionally ignored.
        """

        ground_keys = tuple(
            sorted(
                (item, zone)
                for item, zone in state.ground_keys
                if self.key_is_relevant(item, state)
            )
        )

        ground_tools = tuple(
            sorted(
                (item, zone)
                for item, zone in state.ground_tools
                if self.tool_is_relevant(item, state)
            )
        )

        pending_materials = set(self.pending_material_demand(state))
        material_counts: Counter[tuple[str, str]] = Counter()
        for zone, material_type, count in state.ground_materials:
            if material_type in pending_materials and count > 0:
                material_counts[(zone, material_type)] += count

        ground_materials = tuple(
            sorted(
                (zone, material_type, count)
                for (zone, material_type), count in material_counts.items()
                if count > 0
            )
        )

        return State(
            zone=state.zone,
            battery=state.battery,
            inv_keys=tuple(sorted(state.inv_keys)),
            inv_tools=tuple(sorted(state.inv_tools)),
            inv_materials=tuple(
                sorted((material_type, count)
                       for material_type, count in state.inv_materials
                       if count > 0)
            ),
            ground_keys=ground_keys,
            ground_tools=ground_tools,
            ground_materials=ground_materials,
            open_doors=frozenset(state.open_doors),
            repaired_panels=frozenset(state.repaired_panels),
            online_stations=frozenset(state.online_stations),
        )

    # ------------------------------------------------------------------
    # DROP pruning
    # ------------------------------------------------------------------

    def capacity_blocks_relevant_pickup(self, state: State) -> bool:
        current_weight = self.payload_weight(state)

        for item, zone in state.ground_keys:
            if (
                zone == state.zone
                and self.key_is_relevant(item, state)
                and current_weight + self.item_weight(item) > self.cargo_capacity
            ):
                return True

        for item, zone in state.ground_tools:
            if (
                zone == state.zone
                and self.tool_is_relevant(item, state)
                and current_weight + self.item_weight(item) > self.cargo_capacity
            ):
                return True

        for zone, material_type, count in state.ground_materials:
            if (
                zone == state.zone
                and count > 0
                and self.material_pickup_is_relevant(material_type, state)
                and current_weight + self.item_weight(material_type)
                > self.cargo_capacity
            ):
                return True

        return False

    def dead_carried_items(self, state: State) -> set[str]:
        """Items that no longer enable any pending future operation."""
        dead: set[str] = set()

        for item in state.inv_keys:
            if not self.key_is_relevant(item, state):
                dead.add(item)

        for item in state.inv_tools:
            if not self.tool_is_relevant(item, state):
                dead.add(item)

        pending = self.pending_material_demand(state)
        for material_type, _count in state.inv_materials:
            if pending[material_type] == 0:
                dead.add(material_type)

        return dead

    # ------------------------------------------------------------------
    # Successors
    # ------------------------------------------------------------------

    def successors(
        self,
        state: State,
        *,
        drop_mode: str = "full",
    ) -> list[tuple[State, int, dict[str, Any]]]:
        successors: list[tuple[State, int, dict[str, Any]]] = []
        current_weight = self.payload_weight(state)

        def add(next_state: State, cost: int, step: dict[str, Any]) -> None:
            successors.append((self.canonicalize(next_state), cost, step))

        # RECHARGE
        if state.battery < self.battery_max and state.battery >= self.recharge_cost:
            for charger in self.chargers:
                if charger.get("zone") != state.zone:
                    continue

                add(
                    State(
                        zone=state.zone,
                        battery=self.battery_max,
                        inv_keys=state.inv_keys,
                        inv_tools=state.inv_tools,
                        inv_materials=state.inv_materials,
                        ground_keys=state.ground_keys,
                        ground_tools=state.ground_tools,
                        ground_materials=state.ground_materials,
                        open_doors=state.open_doors,
                        repaired_panels=state.repaired_panels,
                        online_stations=state.online_stations,
                    ),
                    self.recharge_cost,
                    {
                        "op": "INTERACT",
                        "target": charger["id"],
                        "action": "RECHARGE",
                        "cost": self.recharge_cost,
                    },
                )

        # OPEN_DOOR
        if state.battery >= self.interact_cost:
            for door in self.doors:
                if (
                    door["id"] not in state.open_doors
                    and state.zone in door.get("between", [])
                    and door["key"] in state.inv_keys
                ):
                    add(
                        State(
                            zone=state.zone,
                            battery=state.battery - self.interact_cost,
                            inv_keys=state.inv_keys,
                            inv_tools=state.inv_tools,
                            inv_materials=state.inv_materials,
                            ground_keys=state.ground_keys,
                            ground_tools=state.ground_tools,
                            ground_materials=state.ground_materials,
                            open_doors=state.open_doors | {door["id"]},
                            repaired_panels=state.repaired_panels,
                            online_stations=state.online_stations,
                        ),
                        self.interact_cost,
                        {
                            "op": "INTERACT",
                            "target": door["id"],
                            "action": "OPEN_DOOR",
                            "cost": self.interact_cost,
                        },
                    )

        # REPAIR
        if state.battery >= self.interact_cost:
            inv_materials = dict(state.inv_materials)

            for panel in self.panels:
                if (
                    panel["id"] in state.repaired_panels
                    or panel["zone"] != state.zone
                ):
                    continue

                tool = panel["requires"]["tool"]
                material = panel["requires"]["material"]

                if tool not in state.inv_tools or inv_materials.get(material, 0) <= 0:
                    continue

                new_materials = dict(inv_materials)
                new_materials[material] -= 1
                if new_materials[material] == 0:
                    del new_materials[material]

                add(
                    State(
                        zone=state.zone,
                        battery=state.battery - self.interact_cost,
                        inv_keys=state.inv_keys,
                        inv_tools=state.inv_tools,
                        inv_materials=tuple(sorted(new_materials.items())),
                        ground_keys=state.ground_keys,
                        ground_tools=state.ground_tools,
                        ground_materials=state.ground_materials,
                        open_doors=state.open_doors,
                        repaired_panels=state.repaired_panels | {panel["id"]},
                        online_stations=state.online_stations,
                    ),
                    self.interact_cost,
                    {
                        "op": "INTERACT",
                        "target": panel["id"],
                        "action": "REPAIR",
                        "consumes": material,
                        "cost": self.interact_cost,
                    },
                )

        # ACTIVATE
        if state.battery >= self.interact_cost:
            for station in self.stations:
                if (
                    station["id"] in state.online_stations
                    or station["zone"] != state.zone
                ):
                    continue

                requires = station.get("requires", {})
                panels_ok = all(
                    panel in state.repaired_panels
                    for panel in requires.get("panels_ok", [])
                )
                stations_ok = all(
                    other in state.online_stations
                    for other in requires.get("stations_online", [])
                )

                if not (panels_ok and stations_ok):
                    continue

                add(
                    State(
                        zone=state.zone,
                        battery=state.battery - self.interact_cost,
                        inv_keys=state.inv_keys,
                        inv_tools=state.inv_tools,
                        inv_materials=state.inv_materials,
                        ground_keys=state.ground_keys,
                        ground_tools=state.ground_tools,
                        ground_materials=state.ground_materials,
                        open_doors=state.open_doors,
                        repaired_panels=state.repaired_panels,
                        online_stations=state.online_stations | {station["id"]},
                    ),
                    self.interact_cost,
                    {
                        "op": "INTERACT",
                        "target": station["id"],
                        "action": "ACTIVATE",
                        "cost": self.interact_cost,
                    },
                )

        # PICKUP keys
        if state.battery >= self.pickup_cost:
            for item, zone in state.ground_keys:
                if (
                    zone == state.zone
                    and self.key_is_relevant(item, state)
                    and current_weight + self.item_weight(item) <= self.cargo_capacity
                ):
                    add(
                        State(
                            zone=state.zone,
                            battery=state.battery - self.pickup_cost,
                            inv_keys=tuple(sorted(state.inv_keys + (item,))),
                            inv_tools=state.inv_tools,
                            inv_materials=state.inv_materials,
                            ground_keys=tuple(
                                pair for pair in state.ground_keys if pair[0] != item
                            ),
                            ground_tools=state.ground_tools,
                            ground_materials=state.ground_materials,
                            open_doors=state.open_doors,
                            repaired_panels=state.repaired_panels,
                            online_stations=state.online_stations,
                        ),
                        self.pickup_cost,
                        {"op": "PICKUP", "item": item, "cost": self.pickup_cost},
                    )

            # PICKUP tools
            for item, zone in state.ground_tools:
                if (
                    zone == state.zone
                    and self.tool_is_relevant(item, state)
                    and current_weight + self.item_weight(item) <= self.cargo_capacity
                ):
                    add(
                        State(
                            zone=state.zone,
                            battery=state.battery - self.pickup_cost,
                            inv_keys=state.inv_keys,
                            inv_tools=tuple(sorted(state.inv_tools + (item,))),
                            inv_materials=state.inv_materials,
                            ground_keys=state.ground_keys,
                            ground_tools=tuple(
                                pair for pair in state.ground_tools if pair[0] != item
                            ),
                            ground_materials=state.ground_materials,
                            open_doors=state.open_doors,
                            repaired_panels=state.repaired_panels,
                            online_stations=state.online_stations,
                        ),
                        self.pickup_cost,
                        {"op": "PICKUP", "item": item, "cost": self.pickup_cost},
                    )

            # PICKUP materials
            for zone, material_type, count in state.ground_materials:
                if (
                    zone != state.zone
                    or count <= 0
                    or not self.material_pickup_is_relevant(material_type, state)
                    or current_weight + self.item_weight(material_type)
                    > self.cargo_capacity
                ):
                    continue

                inventory = dict(state.inv_materials)
                inventory[material_type] = inventory.get(material_type, 0) + 1

                new_ground: list[tuple[str, str, int]] = []
                for ground_zone, ground_type, ground_count in state.ground_materials:
                    if ground_zone == zone and ground_type == material_type:
                        if ground_count > 1:
                            new_ground.append(
                                (ground_zone, ground_type, ground_count - 1)
                            )
                    else:
                        new_ground.append(
                            (ground_zone, ground_type, ground_count)
                        )

                add(
                    State(
                        zone=state.zone,
                        battery=state.battery - self.pickup_cost,
                        inv_keys=state.inv_keys,
                        inv_tools=state.inv_tools,
                        inv_materials=tuple(sorted(inventory.items())),
                        ground_keys=state.ground_keys,
                        ground_tools=state.ground_tools,
                        ground_materials=tuple(sorted(new_ground)),
                        open_doors=state.open_doors,
                        repaired_panels=state.repaired_panels,
                        online_stations=state.online_stations,
                    ),
                    self.pickup_cost,
                    {
                        "op": "PICKUP",
                        "item": material_type,
                        "cost": self.pickup_cost,
                    },
                )

        # DROP: only when capacity blocks a relevant pickup.
        if (
            state.battery >= self.drop_cost
            and self.capacity_blocks_relevant_pickup(state)
        ):
            allowed_items: Optional[set[str]] = None

            if drop_mode == "dead_only":
                allowed_items = self.dead_carried_items(state)
                if not allowed_items:
                    # Restricted phase intentionally does not branch into live-item
                    # placements. The general UCS fallback handles those cases.
                    allowed_items = set()

            # Keys
            for item in state.inv_keys:
                if allowed_items is not None and item not in allowed_items:
                    continue

                add(
                    State(
                        zone=state.zone,
                        battery=state.battery - self.drop_cost,
                        inv_keys=tuple(k for k in state.inv_keys if k != item),
                        inv_tools=state.inv_tools,
                        inv_materials=state.inv_materials,
                        ground_keys=state.ground_keys + ((item, state.zone),),
                        ground_tools=state.ground_tools,
                        ground_materials=state.ground_materials,
                        open_doors=state.open_doors,
                        repaired_panels=state.repaired_panels,
                        online_stations=state.online_stations,
                    ),
                    self.drop_cost,
                    {"op": "DROP", "item": item, "cost": self.drop_cost},
                )

            # Tools
            for item in state.inv_tools:
                if allowed_items is not None and item not in allowed_items:
                    continue

                add(
                    State(
                        zone=state.zone,
                        battery=state.battery - self.drop_cost,
                        inv_keys=state.inv_keys,
                        inv_tools=tuple(t for t in state.inv_tools if t != item),
                        inv_materials=state.inv_materials,
                        ground_keys=state.ground_keys,
                        ground_tools=state.ground_tools + ((item, state.zone),),
                        ground_materials=state.ground_materials,
                        open_doors=state.open_doors,
                        repaired_panels=state.repaired_panels,
                        online_stations=state.online_stations,
                    ),
                    self.drop_cost,
                    {"op": "DROP", "item": item, "cost": self.drop_cost},
                )

            # Materials
            for material_type, count in state.inv_materials:
                if allowed_items is not None and material_type not in allowed_items:
                    continue

                inventory = dict(state.inv_materials)
                if count == 1:
                    del inventory[material_type]
                else:
                    inventory[material_type] = count - 1

                material_ground: Counter[tuple[str, str]] = Counter(
                    {
                        (zone, mat_type): ground_count
                        for zone, mat_type, ground_count in state.ground_materials
                    }
                )
                material_ground[(state.zone, material_type)] += 1

                new_ground = tuple(
                    sorted(
                        (zone, mat_type, ground_count)
                        for (zone, mat_type), ground_count in material_ground.items()
                        if ground_count > 0
                    )
                )

                add(
                    State(
                        zone=state.zone,
                        battery=state.battery - self.drop_cost,
                        inv_keys=state.inv_keys,
                        inv_tools=state.inv_tools,
                        inv_materials=tuple(sorted(inventory.items())),
                        ground_keys=state.ground_keys,
                        ground_tools=state.ground_tools,
                        ground_materials=new_ground,
                        open_doors=state.open_doors,
                        repaired_panels=state.repaired_panels,
                        online_stations=state.online_stations,
                    ),
                    self.drop_cost,
                    {
                        "op": "DROP",
                        "item": material_type,
                        "cost": self.drop_cost,
                    },
                )

        # MOVE
        for corridor in self.corridors_by_from.get(state.zone, []):
            door = corridor.get("door")
            cost = int(corridor["cost"])

            if door is not None and door not in state.open_doors:
                continue
            if state.battery < cost:
                continue

            add(
                State(
                    zone=corridor["to"],
                    battery=state.battery - cost,
                    inv_keys=state.inv_keys,
                    inv_tools=state.inv_tools,
                    inv_materials=state.inv_materials,
                    ground_keys=state.ground_keys,
                    ground_tools=state.ground_tools,
                    ground_materials=state.ground_materials,
                    open_doors=state.open_doors,
                    repaired_panels=state.repaired_panels,
                    online_stations=state.online_stations,
                ),
                cost,
                {
                    "op": "MOVE",
                    "from": state.zone,
                    "to": corridor["to"],
                    "cost": cost,
                },
            )

        return successors


def _reconstruct(node: SearchNode) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    current: Optional[SearchNode] = node

    while current is not None and current.step is not None:
        steps.append(current.step)
        current = current.parent

    steps.reverse()
    return steps


def _ucs(
    problem: ScenarioProblem,
    *,
    drop_mode: str,
    expansion_limit: Optional[int] = None,
) -> Optional[SearchNode]:
    initial = problem.initial_state()

    if problem.is_goal(initial):
        return SearchNode(initial, 0)

    tracker = DominanceTracker()
    closed: set[State] = set()

    frontier: list[tuple[int, int, SearchNode]] = []
    counter = 0

    root = SearchNode(initial, 0)
    tracker.add(initial.signature, initial.battery, 0)
    heapq.heappush(frontier, (0, counter, root))

    expanded = 0

    while frontier:
        g, _, node = heapq.heappop(frontier)

        # Heap entries can become stale after a dominating path is discovered.
        if not tracker.is_current(node.state.signature, node.state.battery, g):
            continue

        if node.state in closed:
            continue

        # UCS goal test is performed when the node is extracted.
        if problem.is_goal(node.state):
            return node

        closed.add(node.state)
        expanded += 1

        if expansion_limit is not None and expanded >= expansion_limit:
            return None

        for next_state, action_cost, step in problem.successors(
            node.state,
            drop_mode=drop_mode,
        ):
            if next_state in closed:
                continue

            next_g = g + action_cost

            if tracker.is_dominated(
                next_state.signature,
                next_state.battery,
                next_g,
            ):
                continue

            tracker.add(
                next_state.signature,
                next_state.battery,
                next_g,
            )

            counter += 1
            heapq.heappush(
                frontier,
                (
                    next_g,
                    counter,
                    SearchNode(
                        state=next_state,
                        g=next_g,
                        parent=node,
                        step=step,
                    ),
                ),
            )

    return None


def solve_scenario(scenario: dict[str, Any]) -> dict[str, Any]:
    """Solve a scenario and return the exact API contract shape.

    A relevance-pruned UCS is attempted first because DROP is the dominant
    branching source. If that restricted search cannot find a plan, the solver
    falls back to the complete DROP generator.
    """

    problem = ScenarioProblem(scenario)

    if problem.is_goal(problem.initial_state()):
        return {
            "solution_found": True,
            "total_cost": 0,
            "steps": [],
            "message": "Initial state already satisfies goal.",
        }

    # Fast search: only discard payload objects whose future role is already over.
    # This is enough for the provided demo and avoids the DROP explosion.
    node = _ucs(
        problem,
        drop_mode="dead_only",
        expansion_limit=120_000,
    )

    used_fallback = False

    # General fallback for instances that really require dropping a still-relevant
    # item to continue.
    if node is None:
        used_fallback = True
        node = _ucs(
            problem,
            drop_mode="full",
            expansion_limit=None,
        )

    if node is None:
        return {
            "solution_found": False,
            "total_cost": 0,
            "steps": [],
            "message": "FAILURE: No valid plan exists to reach the goal.",
        }

    steps = _reconstruct(node)
    total_cost = sum(int(step["cost"]) for step in steps)

    return {
        "solution_found": True,
        "total_cost": total_cost,
        "steps": steps,
        "message": (
            "Plan found by UCS."
            if used_fallback
            else "Plan found by UCS with relevance-pruned DROP."
        ),
    }

solve = solve_scenario