"""Minimal, framework-independent selected-campus session state."""

from typing import MutableMapping, Optional

from src.p3_campus_registry import CampusMapRegistry, UnknownCampusError

SELECTED_CAMPUS_ID_KEY = "p3_selected_campus_id"

# These values either contain a map object, resolved node ids, movement routes,
# or planning results built from them.  A campus switch must invalidate them.
CAMPUS_BOUND_STATE_KEYS = (
    "p2_live_map", "p2_live_session", "p2_live_intake_cache", "p2_movement_data",
    # P2SessionController persists these snapshots directly in the shared
    # session store.  They can embed movement blocks / map node references.
    "p2_state", "p2_result", "p2_last_turn", "p2_last_cache_key",
    "p4_execution_plan_context", "p4_live_final_turn", "p4_live_final_turn_sequence",
    "p4_feedback_decision",
    # P5 values summarize or preview the same campus-bound task/location refs.
    # Day preferences are session-scoped and may contain task-specific refs,
    # so a campus switch starts a clean planning day rather than retaining a
    # partially stale profile.
    "p5_agent_intelligence", "p5_agent_call_trace", "p5_what_if_preview",
    "p5_day_preference_profile", "p5_latest_user_text", "p5_latest_feedback_text",
    "p3_current_location", "p3_resolved_destination", "p3_movement_route", "p3_map_node_refs",
)


class CampusSelectionRequiredError(ValueError):
    pass


def selected_campus_id(store: MutableMapping) -> Optional[str]:
    value = store.get(SELECTED_CAMPUS_ID_KEY)
    return value if isinstance(value, str) and value.strip() else None


def require_selected_campus(store: MutableMapping) -> str:
    campus_id = selected_campus_id(store)
    if campus_id is None:
        raise CampusSelectionRequiredError("请先选择校区")
    return campus_id


def select_campus(store: MutableMapping, campus_id: str, registry: CampusMapRegistry) -> bool:
    """Persist explicit choice and invalidate all campus-bound state on change."""
    registry.get_registration(campus_id)  # raises UnknownCampusError without fallback
    changed = selected_campus_id(store) != campus_id
    if changed:
        for key in CAMPUS_BOUND_STATE_KEYS:
            store.pop(key, None)
    store[SELECTED_CAMPUS_ID_KEY] = campus_id
    return changed
