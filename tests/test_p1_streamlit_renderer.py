from src.p1_streamlit_renderer import render_view
from src.p1_view_models import (NoticeView, P1ResultView, PlanCardView, QuickOptionView,
                                QuestionView, TaskStepView, DiagnosticView)


class FakeSt(object):
    def __init__(self, clicked=()): self.text = []; self.infos=[]; self.warnings=[]; self.keys=[]; self.clicked=set(clicked)
    def subheader(self, x): self.text.append(x)
    def write(self, x): self.text.append(x)
    def info(self, x): self.infos.append(x); self.text.append(x)
    def warning(self, x): self.warnings.append(x); self.text.append(x)
    def markdown(self, x): self.text.append(x)
    def button(self, *args, **kwargs): self.keys.append(kwargs.get("key")); return kwargs.get("key") in self.clicked
    class _Expander(object):
        def __enter__(self): return self
        def __exit__(self, *args): return False
    def expander(self, x): return self._Expander()


def test_renderer_consumes_view_only_and_does_not_show_internal_refs():
    view = P1ResultView("当前建议", "建议先背单词 30 分钟。", PlanCardView("当前方案", (TaskStepView("背单词",30,"当前空档"),),30,30,0,None,(),None,(),(),False), None, NoticeView("提示","info"), None, None, QuestionView("确认吗？", (QuickOptionView("确认","confirm"),)), (), (), (), True)
    st = FakeSt(); render_view(st, view)
    assert any("背单词" in str(x) for x in st.text)
    assert not any("task_ref" in str(x) for x in st.text)


def make_view():
    return P1ResultView("当前建议", "建议", PlanCardView("当前方案", (TaskStepView("背单词",30,"当前空档"),),30,20,8,"2026-05-02 00:05",(),"理由",("假设",),("警告",),False), None, NoticeView("提升","info"), NoticeView("温和","gentle"), NoticeView("路线","warning"), QuestionView("确认？", (QuickOptionView("相同","a","1"),QuickOptionView("相同","b","2"))), ("其他问题",), (QuickOptionView("换任务","change_task"),), ("根据你的描述",), True)


def test_renderer_renders_complete_card_and_details_and_notice_levels():
    st=FakeSt(); render_view(st, make_view())
    output=" ".join(map(str,st.text))
    assert "总任务：30 分钟" in output and "当前空档任务：20 分钟" in output and "步行：8 分钟" in output
    assert "理由" in output and "假设" in output and "警告" in output and "其他问题" in output
    assert "路线" in st.warnings and "温和" in st.infos and "提升" in st.infos


def test_question_and_action_clicks_call_once_with_unique_keys():
    view=make_view(); st=FakeSt(("p1_option_0_a","p1_action_0_change_task")); got=[]
    render_view(st, view, got.append)
    assert [x.action for x in got] == ["a","change_task"]
    assert len(st.keys) == len(set(st.keys)) and got[0].value == "1" and got[0].label == "相同"


def test_no_click_does_not_call_callback_and_empty_details_do_not_render_none():
    view=make_view(); view = P1ResultView(view.status_title,view.headline,PlanCardView("当前",(),None,None,None,None,(),None,(),(),False),None,None,None,None,None,(),(),(),False)
    st=FakeSt(); called=[]; render_view(st,view,called.append)
    assert not called and "None" not in " ".join(map(str,st.text))


def test_failure_view_renders_safe_diagnostics_without_internal_values():
    view = P1ResultView(
        "暂时无法完成规划", "这次规划暂时没有完成，请稍后再试。", None,
        None, None, None, None, None, (), (), (), False,
        (DiagnosticView("任务理解", "解析失败", 2, 2, "返回格式"),
         DiagnosticView("Agent方案生成", "调用失败", 1, 5, "网络")), 5)
    st = FakeSt()
    render_view(st, view)
    output = " ".join(map(str, st.text))
    assert "任务理解：解析失败，尝试2次，安全错误类别：返回格式" in output
    assert "Agent方案生成：调用失败，尝试1次，安全错误类别：网络" in output
    assert "本次模型调用：5/10" in output
    assert "Authorization" not in output and "https://" not in output


def test_unverified_walking_omits_zero_but_keeps_free_window_minutes():
    view = P1ResultView(
        "暂定建议", "先背单词", PlanCardView(
            "当前", (TaskStepView("背单词", 30, "当前空档"),),
            30, 30, None, None, (), None, (), (), True),
        None, None, NoticeView("路线尚未验证", "gentle"), None, None,
        (), (), (), False)
    st = FakeSt()
    render_view(st, view)
    output = " ".join(map(str, st.text))
    assert "当前空档任务：30 分钟" in output
    assert "步行：0 分钟" not in output


def make_current_plan_view():
    return P1ResultView(
        "当前方案", "建议先背单词 30 分钟。",
        PlanCardView("当前方案", (TaskStepView("背单词", 30, "当前空档"),),
                     30, 30, 8, "2026-05-02 00:05", (), "理由", (), (), True),
        None, None, NoticeView("温和提示", "gentle"), NoticeView("路线", "warning"),
        QuestionView("确认？", (QuickOptionView("确认", "confirm"),)), (), (), (), True)


def test_success_shows_single_current_plan_area_only():
    st = FakeSt()
    render_view(st, make_current_plan_view())
    output = " ".join(map(str, st.text))
    assert output.count("当前方案") == 1
    assert "AI 暂定建议" not in output and "AI 暂定方案" not in output


def test_tentative_plan_still_shows_single_current_plan_title():
    view = make_current_plan_view()
    st = FakeSt()
    render_view(st, view)
    output = " ".join(map(str, st.text))
    assert output.count("当前方案") == 1
    assert "AI 暂定建议" not in output


def test_plan_precedes_route_notice_and_confirmation_question():
    st = FakeSt()
    render_view(st, make_current_plan_view())
    text = list(map(str, st.text))
    plan_index = text.index("当前方案")
    headline_index = text.index("建议先背单词 30 分钟。")
    walk_index = text.index("步行：8 分钟")
    route_index = text.index("路线")
    question_index = text.index("确认？")
    assert plan_index < headline_index < walk_index < route_index < question_index


def test_local_ai_estimate_question_is_rendered_after_plan():
    view = P1ResultView(
        "当前方案", "建议先推进计组实验3 60 分钟。",
        PlanCardView("当前方案", (TaskStepView("推进计组实验3", 60, "当前空档"),),
                     60, 60, None, None, (), "理由", (), (), True),
        None, None, None, None,
        QuestionView("“计组实验3”完整时长目前按 120 分钟估计（AI 暂估）。是否按这个估计继续？",
                     (QuickOptionView("就按这个", "confirm_estimate", "120"),
                      QuickOptionView("调整时长", "adjust_duration", "120"))),
        (), (), (), False)
    st = FakeSt()
    render_view(st, view)
    text = list(map(str, st.text))
    def contains(items, needle):
        return next(index for index, item in enumerate(items) if needle in item)
    assert contains(text, "推进计组实验3：60 分钟（当前空档）") < contains(text, "AI 暂估")
