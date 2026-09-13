"""Finite three-scene competition rehearsal; lowest model seam only."""
import json
from scripts.offline_product_model import OfflineProductModel, encoded
from scripts.material_demo_model import MaterialDemoModel

FEEDBACK_TEXT = '作业还剩40分钟，我现在在图书馆。'


class ReleaseDemoModel(OfflineProductModel):
    def _response(self, system, user):
        if 'campusflow.material-text.v2' in system:
            raw,role = MaterialDemoModel('dirty_a')._response(system,user)
            value = json.loads(raw)
            for item in value.get('items',[]):
                if item.get('kind') == 'task' and item.get('minutes') is None:
                    item['estimate'] = dict(min_focus_minutes=45,max_focus_minutes=70,
                        recommended_minutes=60,basis='按题目范围、过程要求和检查步骤粗略估算',
                        assumptions=['只估算材料中可见或明确说明的范围'],clarification_question=None)
            return json.dumps(value,ensure_ascii=False),role
        if 'Feedback Interpreter' in system and FEEDBACK_TEXT in user:
            raw,role = super()._response(system,user)
            value = json.loads(raw)
            value['intent_type'] = 'no_change'
            return json.dumps(value,ensure_ascii=False),role
        if 'p3.unified-feedback.v1' in system:
            # This finite story starts with explicit total90, completed0.
            # User reports remaining40, so the fake semantic delta is50.
            return encoded('p3.unified-feedback.v1',task_updates=[dict(
                target_task_ref='day_task_001',new_task_title=None,progress_delta_minutes=50,
                set_total_minutes=None,set_total_source=None,lifecycle_action='none',
                is_splittable=None,minimum_slice_minutes=None)],commitment_updates=[],
                movement=dict(has_movement=False,origin_text=None,destination_text=None,
                    mode=None,depart_at=None,arrive_by=None),current_location='图书馆',
                questions=[],reason=None),'progress'
        return super()._response(system,user)
