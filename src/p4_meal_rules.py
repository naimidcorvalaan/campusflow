"""Deterministic campus-scoped defaults for an unspecified meal."""

from dataclasses import dataclass

from src.p3_map_schema import CampusMapData, TransportMode, parse_transport_mode
from src.p3_route_provider import plan_route

DEFAULT_MEAL_DURATION_MINUTES = 40
AUTO_CANTEEN_LABEL = "【已为您选择就近食堂】"


@dataclass(frozen=True)
class CanteenChoice:
    node_id: str
    name: str
    distance_m: int


def canteen_nodes(map_data):
    """Use map facts, with a small legacy-name adapter for the old map."""
    return tuple(
        node for node in map_data.nodes
        if node.node_kind == "poi" and node.category in ("canteen", "dining")
    )


def choose_nearest_canteen(map_data: CampusMapData, anchor_text: str, mode=TransportMode.WALK):
    """Choose only among the selected map's route-reachable canteens."""
    parsed_mode = parse_transport_mode(mode)
    choices = []
    for node in canteen_nodes(map_data):
        route = plan_route(map_data, anchor_text, node.id, parsed_mode)
        if route.total_distance_m is not None:
            choices.append(CanteenChoice(node.id, node.name, route.total_distance_m))
    return min(choices, key=lambda item: (item.distance_m, item.node_id)) if choices else None


def choose_canteen_nearest_to_destination(map_data: CampusMapData, destination_text: str, mode=TransportMode.WALK):
    """Choose using real canteen -> destination routes for a future anchor."""
    parsed_mode = parse_transport_mode(mode)
    choices = []
    for node in canteen_nodes(map_data):
        route = plan_route(map_data, node.id, destination_text, parsed_mode)
        if route.total_distance_m is not None:
            choices.append(CanteenChoice(node.id, node.name, route.total_distance_m))
    return min(choices, key=lambda item: (item.distance_m, item.node_id)) if choices else None
