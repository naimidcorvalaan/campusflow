"""P3c 地点解析测试：exact alias 优先、Qwen 兜底、node 校验、不编造粒度（Python 3.8 兼容，全 mock）。"""
from pathlib import Path

import pytest

from src.p2_agentic_parser import AgenticParseError
from src.p3_location_resolver import (
    LOCATION_RESOLVER_SCHEMA_VERSION,
    LocationResolutionStatus,
    build_location_resolver_prompt,
    parse_location_resolution,
    resolve_location,
)
from src.p3_map_loader import load_campus_map_data

REAL_MAP_PATH = Path(__file__).parent.parent / "data" / "beiyangyuan_map.json"


@pytest.fixture(scope="module")
def real_map():
    return load_campus_map_data(REAL_MAP_PATH)


def loc_json(status, node_id=None, display=None, question=None):
    node = "null" if node_id is None else '"' + node_id + '"'
    disp = "null" if display is None else '"' + display + '"'
    q = "null" if question is None else '"' + question + '"'
    return (
        '{"schema_version": "' + LOCATION_RESOLVER_SCHEMA_VERSION + '", "status": "' + status + '", '
        '"matched_node_id": ' + node + ', "display_name": ' + disp + ', "question": ' + q + '}'
    )


class FakeCaller:
    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.calls = []

    def __call__(self, system, user):
        self.calls.append((system, user))
        if not self.outputs:
            return "{}"
        return self.outputs.pop(0)


def test_exact_alias_31jiao_no_model_call(real_map):
    caller = FakeCaller()
    result = resolve_location(real_map, "31教", caller)
    assert result.status is LocationResolutionStatus.RESOLVED
    assert result.node_id == "building_31"
    assert result.matched_by == "exact_alias"
    assert result.usable
    assert caller.calls == []


def test_exact_name_and_aliases_resolve(real_map):
    result = resolve_location(real_map, "齐园31教学楼")
    assert result.status is LocationResolutionStatus.UNRESOLVED
    assert result.node_id is None
    assert not result.usable
    assert resolve_location(real_map, "北区菜鸟").node_id == "beiyangyuan_north_cainiao"
    assert resolve_location(real_map, "郑东图书馆").node_id == "zhengdong_library"
    assert resolve_location(real_map, "第四食堂").node_id == "xuesi_cafeteria"


def test_qwen_resolves_unknown_text_to_real_node(real_map):
    caller = FakeCaller(loc_json("resolved", "building_31", "31教"))
    result = resolve_location(real_map, "去31号教学楼", caller)
    assert result.status is LocationResolutionStatus.RESOLVED
    assert result.node_id == "building_31"
    assert result.matched_by == "qwen"
    assert len(caller.calls) == 1


def test_qwen_nonexistent_node_id_rejected(real_map):
    caller = FakeCaller(loc_json("resolved", "ghost_node", "幽灵楼"))
    result = resolve_location(real_map, "任意地点", caller)
    assert result.status is LocationResolutionStatus.UNRESOLVED
    assert result.node_id is None
    assert not result.usable
    assert result.question
    assert len(caller.calls) == 1


def test_jiu_zhai_exact_alias_no_model_call(real_map):
    # P3d 官方图补充后：9 斋 = 正园9斋 独立节点，精确别名命中，不调用模型
    caller = FakeCaller()
    result = resolve_location(real_map, "9斋", caller)
    assert result.status is LocationResolutionStatus.RESOLVED
    assert result.node_id == "zhengyuan_9zhai"
    assert result.matched_by == "exact_alias"
    assert result.usable
    assert caller.calls == []


def test_finer_granularity_approximate_keeps_question(real_map):
    # 更细粒度（9斋甲座）未命中任何节点：Qwen 近似映射必须保留待确认问题
    caller = FakeCaller(
        loc_json(
            "approximate",
            "zhengyuan_9zhai",
            "天津大学北洋园校区正园9斋",
            "正园9斋甲座更细，地图上只有正园9斋，是否按正园9斋处理？",
        )
    )
    result = resolve_location(real_map, "正园9斋甲座", caller)
    assert result.status is LocationResolutionStatus.APPROXIMATE
    assert result.node_id == "zhengyuan_9zhai"
    assert result.usable
    assert result.question


def test_qwen_ambiguous_no_node(real_map):
    caller = FakeCaller(loc_json("ambiguous", None, None, "你指的是哪个实验楼？"))
    result = resolve_location(real_map, "实验楼", caller)
    assert result.status is LocationResolutionStatus.AMBIGUOUS
    assert result.node_id is None
    assert not result.usable
    assert result.question


def test_repair_recovers_from_malformed(real_map):
    caller = FakeCaller("不是 JSON 内容", loc_json("resolved", "zhengdong_library", "郑东图书馆"))
    result = resolve_location(real_map, "去图书馆", caller, repair_caller=caller)
    assert result.status is LocationResolutionStatus.RESOLVED
    assert result.node_id == "zhengdong_library"
    assert len(caller.calls) == 2


def test_repair_failure_returns_unresolved(real_map):
    caller = FakeCaller("坏输出", "还是坏输出")
    result = resolve_location(real_map, "去图书馆", caller, repair_caller=caller)
    assert result.status is LocationResolutionStatus.UNRESOLVED
    assert result.node_id is None
    assert len(caller.calls) == 2


def test_parse_rejects_wrong_schema():
    with pytest.raises(AgenticParseError):
        parse_location_resolution(
            '{"schema_version": "p3.campus-map.v1", "status": "resolved", '
            '"matched_node_id": "x", "display_name": null, "question": null}'
        )


def test_parse_rejects_bad_status():
    with pytest.raises(AgenticParseError):
        parse_location_resolution(loc_json("everywhere"))


def test_parse_rejects_empty_node_id():
    text = (
        '{"schema_version": "' + LOCATION_RESOLVER_SCHEMA_VERSION + '", "status": "resolved", '
        '"matched_node_id": "  ", "display_name": null, "question": null}'
    )
    with pytest.raises(AgenticParseError):
        parse_location_resolution(text)


def test_parse_tolerates_code_fence():
    text = "```json\n" + loc_json("resolved", "building_31", "31教") + "\n```"
    proposal = parse_location_resolution(text)
    assert proposal.matched_node_id == "building_31"


def test_empty_text_unresolved(real_map):
    result = resolve_location(real_map, "   ")
    assert result.status is LocationResolutionStatus.UNRESOLVED
    assert result.node_id is None


def test_prompt_contains_catalog_no_repr(real_map):
    system, user = build_location_resolver_prompt(real_map, "31教")
    assert "地点解析器" in system
    assert "31教" in user
    assert "齐园31教学楼" not in user
    for token in ("CampusNode", "dataclass", "LocationResolution(", "raw_text"):
        assert token not in user


def test_17_zhai_exact_alias_no_model_call(real_map):
    # P3d 名称审计补全：17斋 = 治园17斋 独立节点，精确别名命中，不调用模型
    caller = FakeCaller()
    result = resolve_location(real_map, "17斋", caller)
    assert result.status is LocationResolutionStatus.RESOLVED
    assert result.node_id == "zhiyuan_garden_17zhai"
    assert result.matched_by == "exact_alias"
    assert result.usable
    assert caller.calls == []


def test_32_jiao_exact_alias_no_model_call(real_map):
    # 高德地址核验：理学院=32教；32教 精确别名命中，不调用模型
    caller = FakeCaller()
    result = resolve_location(real_map, "32教", caller)
    assert result.status is LocationResolutionStatus.RESOLVED
    assert result.node_id == "college_of_science"
    assert result.matched_by == "exact_alias"
    assert caller.calls == []


def test_boxue_resolves_independent_and_long_shu_not_resolvable(real_map):
    # 用户确认：博学园（25/26斋）与三问园（27~30斋）是两个独立园区，互不为 alias
    caller = FakeCaller()
    result = resolve_location(real_map, "博学园", caller)
    assert result.status is LocationResolutionStatus.RESOLVED
    assert result.node_id == "boxueyuan_dorm"
    assert result.matched_by == "exact_alias"
    assert caller.calls == []
    # 官方图误标“博学园30斋”不得解析到三问园
    result = resolve_location(real_map, "博学园30斋", FakeCaller())
    assert result.node_id is None
    # 龙园/书园确认不存在 → 不解析到任何节点
    for text in ("龙园", "书园"):
        result = resolve_location(real_map, text, FakeCaller())
        assert result.node_id is None
