"""User-facing copy derived only from persisted execution facts.

This is intentionally a presentation adapter, not another planner: all route,
meal, campus, and current-location decisions have already been made upstream.
"""

from src.p4_execution_context import (
    CurrentLocationSource,
    ExecutionConfirmationKind,
    ExecutionLocationSource,
    ExecutionPlanContext,
)


def assumed_location_line(context):
    """Return the transparent current-location assumption, if one exists."""
    if not isinstance(context, ExecutionPlanContext):
        return None
    current = context.current_location
    if current.source is not CurrentLocationSource.ASSUMED or current.location is None:
        return None
    return "你没有填写当前位置，我先按你在{}来安排。".format(
        current.location.display_name
    )


def execution_confirmation_questions(context):
    """Translate structured, pending execution facts into short user questions."""
    if not isinstance(context, ExecutionPlanContext):
        return ()
    questions = []
    for confirmation in context.confirmations:
        if confirmation.kind is ExecutionConfirmationKind.CURRENT_LOCATION_ASSUMED:
            current = context.current_location
            if current.source is CurrentLocationSource.ASSUMED and current.location is not None:
                questions.append(
                    "你现在是在{}吗？如果不是，告诉我你现在的位置，我会重新调整路上的时间。".format(
                        current.location.display_name
                    )
                )
        elif confirmation.kind is ExecutionConfirmationKind.MEAL_LOCATION_CONTEXT_REQUIRED:
            questions.append("你现在大概在哪里？我可以帮你选一个顺路的食堂。")
    return tuple(dict.fromkeys(questions))


def is_auto_selected_meal_destination(block, context):
    """Whether a movement reaches a meal chosen by the structured meal rule."""
    if not isinstance(context, ExecutionPlanContext):
        return False
    ref = getattr(block, "destination_activity_ref", None)
    binding = context.binding_for(ref) if ref else None
    location = binding.execution_location if binding is not None else None
    return bool(
        location is not None
        and location.source is ExecutionLocationSource.AUTO_SELECTED_MEAL
        and location.node_id == getattr(block, "destination_node_id", None)
    )
