"""Finite synthetic responses for rehearsal, not a language understanding engine.

Only imported by the development preview. Unknown roles fail closed. No client,
environment credentials, network or real model configuration is accessed.
"""
import json


STORY_TEXT = "在图书馆写90分钟作业，然后背60分钟单词，可以拆开。19:00去9教上课，上一小时半，上课前吃晚饭。"
ESTIMATE_TEXT = "完成高数作业第3—6题，保留步骤并检查；前两题已经做完。"
WHAT_IF_TEXT = "如果改成先背单词，再写作业，会怎么安排？先给我看看，暂时不要改。"
TIMETABLE_TEXT = "大学英语，周二10:00–11:30，第1–16周，卫津路校区，第九教学楼"


def encoded(schema, **values):
    return json.dumps(dict(schema_version=schema, **values), ensure_ascii=False)


class OfflineProductModel:
    # Development-only synthetic adapter. It exercises the same image caller
    # seam without network access and never retains image bytes.
    supports_image_inputs = True

    def __init__(self):
        self.calls = []  # role labels only; never retains prompts/materials
        self.failure_mode = None

    def agent_caller(self, system, user):
        if self.failure_mode == "network":
            self.calls.append("synthetic_network_failure")
            raise RuntimeError("offline simulated unavailable service")
        if self.failure_mode == "format":
            self.calls.append("synthetic_format_failure")
            return "synthetic invalid response"
        value, role = self._response(system, user)
        self.calls.append(role)
        return value

    def task_estimation_caller(self, system, user, image_mime=None, image_bytes=None):
        if image_bytes is None:
            return self.agent_caller(system,user)
        if self.failure_mode == "network":
            self.calls.append("synthetic_image_failure")
            raise RuntimeError("offline simulated unavailable image service")
        value, role = self._response(system,user)
        self.calls.append(role + "_image")
        return value

    def _response(self, system, user):
        if "的 Raw Event Extractor。" in system:
            events = []
            for index, (ref, kind, title, minutes, location, start) in enumerate((
                ("homework", "task", "写作业", 90, "图书馆", None),
                ("vocab", "task", "背单词", 60, None, None),
                ("dinner", "meal", "吃晚饭", None, None, None),
                ("course", "fixed_commitment", "上课", 90, "9教", "19:00"),
            ), 1):
                events.append(dict(local_event_id=ref, event_type=kind, title=title,
                    order_in_utterance=index, starts_at=start, ends_at=None,
                    explicit_duration_minutes=minutes, location_text=location,
                    commitment_kind="class" if kind == "fixed_commitment" else None,
                    raw_evidence=title))
            return encoded("p4.raw-event-extraction.v1", day_end="22:00",
                current_location=None, transport_mode=None, events=events, questions=[]), "extract"
        if "Semantic Linker" in system:
            return encoded("p4.event-semantics.v1", duration_of=[dict(event_id="course", minutes=90)],
                commitment_relations=[dict(event_id="dinner",commitment_event_id="course",relation="before")],
                meal_period_by_event=[dict(event_id="dinner",meal_period="dinner")],
                explicit_sequence=[dict(before_event_id="homework",after_event_id="vocab")],
                execution_profiles=[dict(event_id=ref,splittable=True,minimum_chunk_minutes=15,
                    preferred_chunk_minutes=45,requires_single_session=False,source="user_explicit")
                    for ref in ("homework","vocab")], conflicts=[]), "link"
        if "Initial Intake Semantic Auditor" in system:
            return encoded("p4.initial-intake-audit.v1",decision="approve",issues=[],repaired_proposal=None), "audit"
        if "campusflow.timetable-import.v1" in system or "课表文字识别器" in system:
            return encoded("campusflow.timetable-import.v1",courses=[dict(title="大学英语",weekday=2,
                start_time="10:00",end_time="11:30",start_week=1,end_week=16,week_pattern="every",
                campus_id="weijinlu",location_text="第九教学楼")]), "timetable"
        if "campusflow.task-estimate.v1" in system:
            return encoded("campusflow.task-estimate.v1",understood=True,task_name="高数作业第3—6题",
                scope_summary="完成第3—6题，保留计算步骤并检查",completion_criteria="四题均作答并检查一次",
                min_focus_minutes=25,max_focus_minutes=45,recommended_minutes=35,
                basis="按已确认范围估算专注工作，不包含已完成的前两题",
                assumptions=["知道大概方法，遇到不确定步骤可能查笔记"],
                clarification_needed=False,clarification_question=None,
                adjustment_basis=None,location_text=None,deadline_time=None,
                is_splittable=True,minimum_chunk_minutes=15,preferred_chunk_minutes=30,
                requires_single_session=False), "estimate"
        if "What-if Interpreter" in system:
            return encoded("p5.what-if-intent.v1",changes=[dict(kind="ordering",task_ref="day_task_002",
                related_task_ref="day_task_001")],clarification_needed=False,clarification_question=None), "what_if"
        if "Feedback Interpreter" in system:
            return encoded("p4.feedback-decision.v1",intent_type="what_if",target_task_refs=[],
                preferred_next_task_ref=None,priority_changes=[],ordering_constraints=[],
                cancelled_task_refs=[],restored_task_refs=[],completed_task_refs=[],postponed_task_refs=[],
                concurrency_changes=[],day_preference_candidate=False,
                location_correction=None,explicit_user_preference=True,confidence=0.95,
                clarification_needed=False,clarification_question=None), "feedback"
        if "Decision Critic" in system:
            return encoded("p4.feedback-decision-review.v1",decision="approve",reason=None,repaired_decision=None), "feedback_critic"
        if "day-plan-intent" in system:
            return encoded("p2.day-plan-intent.v1",task_order=[],include_low_attention=False,
                task_estimates=[],rationale=None), "plan"
        if "day-review" in system:
            return encoded("p2.day-review.v1",decision="accept",reason=None,
                suggested_task_order=None,include_low_attention=None), "review"
        if "task-reconciliation" in system:
            return encoded("p2.task-reconciliation.v1",updates=[],questions=[]), "task"
        if "commitment-reconciliation" in system:
            return encoded("p2.commitment-reconciliation.v1",updates=[],questions=[]), "commitment"
        if "Situation Analyst" in system:
            return encoded("p5.situation-analysis.v1",primary_goal="推进当天任务并按时上课",
                important_constraints=["保留课程及移动"],user_priority_signals=[],task_priority_assessment=[],
                timing_pressure=[],fragmentation_concerns=[],meal_context=None,movement_context=None,
                flexibility_opportunities=[],risk_flags=[],recommended_strategy_focus=["earliest_feasible"],
                clarification_value="none"), "situation"
        if "Plan Strategist" in system:
            return encoded("p5.plan-strategies.v1",strategies=[dict(strategy_id="demo_early",priority_order=[],
                protected_preferences=[],fragmentation_level="medium",slack_preference="balanced",
                meal_preference="neutral",concurrency_pairs=[],rationale_summary="按明确要求安排",
                constraints_to_preserve=["固定课程"])]), "strategy"
        if "Plan Judge" in system:
            return encoded("p5.plan-judge.v1",selected_candidate_id="candidate_01",
                concise_selection_reason="采用可行候选",concern=None,confidence="high"), "judge"
        if "Intent Compliance Reviewer" in system:
            return encoded("p5.intent-compliance.v2",decision="approve",reason="保留明确要求",repair_directives=[]), "compliance"
        if "Proactive Suggestion" in system:
            return encoded("p5.proactive-suggestion.v1",should_show=False,suggestion_type="none",text="",
                task_ref=None,commitment_ref=None,suggested_minutes=None), "suggestion"
        if "Grounded Narrator" in system:
            return encoded("p5.grounded-narrator.v1",opening="接下来的安排在这里，先看当前这一项。",
                why_this_plan="学习、吃饭和路上的时间分开显示；未完成的工作仍会保留。",
                closing="有变化时告诉我，再一起调整。",proactive_suggestion=None,risk_note=None), "narrator"
        if "Copy Fact Checker" in system:
            return encoded("p5.copy-fact-check.v1",safe=True,reason=None,corrected_copy=None), "copy_check"
        if "p3.plan-critic" in system:
            return encoded("p3.plan-critic.v1",approved=True,issues=[],revision_needed=False), "legacy_critic"
        if "p3.lifestyle-review" in system:
            return encoded("p3.lifestyle-review.v1",notes=[],user_facing_hint=None), "lifestyle"
        if "p3.plan-narrator" in system:
            return encoded("p3.plan-narrator.v1",opening="先看当前这一项，再顺着时间线往下走。"), "legacy_narrator"
        if "p3.warm-companion" in system:
            return encoded("p3.warm-companion.v1",change_summary=None,closing="有变化时告诉我。"), "companion"
        if "p3.copy-review" in system:
            return encoded("p3.copy-review.v1",approved=True,revised=None,reason=None), "legacy_copy"
        # Optional P5 stages may safely use their existing fallback. Unknown
        # semantics are never guessed by this finite demo responder.
        return "{}", "unsupported_offline_role"
