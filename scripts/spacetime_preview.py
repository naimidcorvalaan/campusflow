"""Offline visual verification of the real live entry; never imported by src.

Run: python -m streamlit run scripts/spacetime_preview.py --server.port 8583
Query: campus=beiyangyuan|weijinlu, scene=moving|stationary
Only synthetic language responses are injected; the formal pipeline publishes
all tasks, movement blocks and timelines used by the production renderer.
"""
import html
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import dotenv
dotenv.load_dotenv = lambda *args, **kwargs: False
from scripts.offline_product_model import OfflineProductModel, encoded


class SpacetimePreviewModel(OfflineProductModel):
    def __init__(self, campus='weijinlu', scene='moving'):
        super().__init__()
        self.campus = campus
        self.scene = scene

    @property
    def library(self):
        return '郑东图书馆' if self.campus == 'beiyangyuan' else '图书馆'

    @property
    def classroom(self):
        return '45教' if self.campus == 'beiyangyuan' else '9教'

    @property
    def story(self):
        base = '我现在在{}，写90分钟作业，然后背60分钟单词，可以拆开。'.format(self.library)
        return base + ('19:00去{}上课，上一小时半，上课前吃晚饭。'.format(self.classroom) if self.scene == 'moving' else '')

    def _response(self, system, user):
        if 'Grounded Narrator' in system:
            return encoded('p5.grounded-narrator.v1', opening='先完成手边的作业，再接着下一项。',
                why_this_plan='按任务所需时间与当前可用时段安排。', closing='有变化时告诉我，再一起调整。',
                proactive_suggestion=None, risk_note=None), 'narrator'
        raw, role = super()._response(system, user)
        if role == 'extract':
            value = json.loads(raw)
            value['current_location'] = self.library
            for event in value['events']:
                if event['local_event_id'] == 'homework':
                    event['location_text'] = self.library
                if event['local_event_id'] == 'course':
                    event['location_text'] = self.classroom
            if self.scene == 'stationary':
                value['events'] = [event for event in value['events'] if event['local_event_id'] in ('homework', 'vocab')]
            return json.dumps(value, ensure_ascii=False), role
        if role == 'link' and self.scene == 'stationary':
            value = json.loads(raw)
            for key in ('duration_of', 'commitment_relations', 'meal_period_by_event'):
                value[key] = []
            return json.dumps(value, ensure_ascii=False), role
        return raw, role


def main():
    import hashlib
    import requests
    import streamlit as st
    from src.p2_live_main import main as live_main, LIVE_INTAKE_KEY, LIVE_CAMPUS_SELECT_KEY
    from src.p2_session import load_live_final_turn
    from src.p3_campus_registry import DEFAULT_CAMPUS_REGISTRY
    from src.spacetime_ui import build_next_route

    def deny_network(*args, **kwargs):
        raise RuntimeError('Offline review blocks external requests')
    requests.sessions.Session.request = deny_network
    os.environ['CAMPUSFLOW_DATA_DIR'] = str(ROOT / 'artifacts/spacetime-migration/isolated-profile')
    st.set_page_config(page_title='CampusFlow', layout='wide')
    campus = st.query_params.get('campus', 'weijinlu')
    if campus not in ('weijinlu', 'beiyangyuan'):
        campus = 'weijinlu'
    scene = 'stationary' if st.query_params.get('scene') == 'stationary' else 'moving'
    model = st.session_state.setdefault('_spacetime_review_model', SpacetimePreviewModel(campus, scene))
    if '_spacetime_review_seeded' not in st.session_state:
        st.session_state['_spacetime_review_seeded'] = True
        st.session_state[LIVE_CAMPUS_SELECT_KEY] = DEFAULT_CAMPUS_REGISTRY.get_registration(campus).display_name
        st.session_state[LIVE_INTAKE_KEY] = model.story
    live_main(st=st, adapter_factory=lambda: model, configuration_loader=lambda: (),
              persistence_factory=None, now_provider=lambda: datetime(2026, 9, 14, 14), configure_page=False)
    bundle = load_live_final_turn(st.session_state)
    facts = {'calls': len(model.calls), 'published': hashlib.sha256(repr(bundle).encode()).hexdigest() if bundle else None}
    if bundle:
        map_data = st.session_state.get('p2_live_map')
        visual = build_next_route(bundle.turn, map_data)
        facts['movement_count'] = len(bundle.movement_blocks)
        if visual:
            block = visual.block
            facts['route'] = dict(campus=visual.campus_id, origin=block.origin_name, destination=block.destination_name,
                originLabel=visual.origin_label, destinationLabel=visual.destination_label,
                distance=block.distance_m, minutes=block.estimated_minutes, mode=block.mode.value,
                depart=block.window_start.strftime('%H:%M'), arrive=block.end_time.strftime('%H:%M'), path=visual.path)
    st.markdown('<span id="cf-review-probe" hidden data-facts="{}"></span>'.format(html.escape(json.dumps(facts, ensure_ascii=False), quote=True)), unsafe_allow_html=True)


if __name__ == '__main__':
    main()
