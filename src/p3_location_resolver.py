"""P3c 地点解析：程序 exact alias 优先，Qwen 兜底（Python 3.8 兼容）。

原则：
- 先做纯程序 exact name / alias 查找（大小写不敏感），命中不调用任何模型；
- 未命中时调用可注入的 Qwen Location Resolver（mock 阶段为 fake callable）；
- 模型输出必须经过程序校验：matched_node_id 必须是地图真实存在的 node id；
- 更细粒度地点而地图只有粗粒度时，不得假装精确命中：
  返回 APPROXIMATE（保留匹配 + 待确认问题）或 UNRESOLVED；
- 不做 Levenshtein / 中文归一化 / 相似度阈值等复杂 fuzzy 规则。
"""

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional, Tuple

from src.p2_agentic_parser import AgenticParseError, extract_json_object
from src.p3_map_schema import CampusMapData

LOCATION_RESOLVER_SCHEMA_VERSION = "p3.location-resolution.v1"
MAX_CATALOG_NODES = 60
MAX_QUESTION_LENGTH = 200

AgentCaller = Callable[[str, str], str]


class LocationResolutionStatus(str, Enum):
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    AMBIGUOUS = "ambiguous"
    APPROXIMATE = "approximate"


@dataclass(frozen=True)
class LocationResolution:
    """一次地点解析的只读结果。node_id 非空时必为地图真实节点。"""

    status: LocationResolutionStatus
    raw_text: str
    node_id: Optional[str] = None
    display_name: Optional[str] = None
    matched_by: Optional[str] = None  # exact_alias / qwen
    question: Optional[str] = None
    # Scope is carried with resolved references so a node id can never be
    # silently handed to another campus map.
    campus_id: Optional[str] = None

    @property
    def usable(self) -> bool:
        """resolved / approximate 均可用于规划（approximate 会附带待确认问题）。"""
        return self.node_id is not None and self.status in (
            LocationResolutionStatus.RESOLVED,
            LocationResolutionStatus.APPROXIMATE,
        )


@dataclass(frozen=True)
class LocationProposal:
    """Qwen Location Resolver 的解析后提案。"""

    schema_version: str
    status: str
    matched_node_id: Optional[str]
    display_name: Optional[str]
    question: Optional[str]

    def __post_init__(self):
        if self.schema_version != LOCATION_RESOLVER_SCHEMA_VERSION:
            raise ValueError("schema_version 不匹配")
        allowed = {status.value for status in LocationResolutionStatus}
        if self.status not in allowed:
            raise ValueError("status 必须是 {}".format(sorted(allowed)))
        if self.matched_node_id is not None and not self.matched_node_id.strip():
            raise ValueError("matched_node_id 不能是空字符串")
        if self.display_name is not None and not self.display_name.strip():
            raise ValueError("display_name 不能是空字符串")
        if self.question is not None and not self.question.strip():
            raise ValueError("question 不能是空字符串")
        if self.question is not None and len(self.question) > MAX_QUESTION_LENGTH:
            raise ValueError("question 过长")


def build_location_resolver_prompt(
    map_data: CampusMapData, user_location_text: str
) -> Tuple[str, str]:
    """构造 Qwen 地点解析 prompt：只发简化地点目录，不发 repr / 异常 / Key。"""
    lines = []
    # Road waypoints participate in Dijkstra only.  They are not destinations
    # and must never be offered to the model as a normal user location.
    resolvable_nodes = [node for node in map_data.nodes if node.node_kind == "poi"]
    for node in resolvable_nodes[:MAX_CATALOG_NODES]:
        aliases = "、".join(node.aliases[:8])
        lines.append(
            "- id={}；名称={}；别名={}".format(node.id, node.name, aliases or "无")
        )
    catalog = "\n".join(lines)
    system = (
        "你是校园地点解析器。用户会用自然语言提到校园地点（可能是简称、口语或细粒度说法）。\n"
        "你只能从给定地点目录中选择最匹配的节点，输出 JSON，不要输出其他内容。\n"
        "规则：\n"
        "1. 如果用户说法能确定对应某个节点，输出 status=resolved 与 matched_node_id。\n"
        "2. 如果用户说的是更细粒度的地点，而目录里只有粗粒度节点（例如用户说“三问园30斋”，目录只有“三问园”），"
        "不要假装精确命中：输出 status=approximate、matched_node_id=该粗粒度节点，并用 question 询问是否按此处理。\n"
        "3. 如果存在多个候选无法确定（例如“实验”可能指多个楼），输出 status=ambiguous、matched_node_id=null、"
        "question 用中文说明需要确认什么。\n"
        "4. 如果目录里没有任何可对应节点，输出 status=unresolved、matched_node_id=null、"
        "question 用中文说明地图上暂无可对应地点。\n"
        "5. matched_node_id 只能填写目录中真实存在的 id；不确定时填 null，绝不编造。\n"
        "6. 不要添加 explanation / evidence / confidence 等多余字段。\n"
        '输出格式：{"schema_version": "' + LOCATION_RESOLVER_SCHEMA_VERSION + '", '
        '"status": "resolved|approximate|ambiguous|unresolved", '
        '"matched_node_id": "节点id或null", "display_name": "显示名称或null", '
        '"question": "待确认问题或null"}\n'
        "示例：用户说“31教”，目录有 id=building_31 名称=31教 别名=31楼、31教学楼，"
        '输出 {"schema_version": "' + LOCATION_RESOLVER_SCHEMA_VERSION + '", "status": "resolved", '
        '"matched_node_id": "building_31", "display_name": "31教", "question": null}\n'
    )
    user = "地点目录：\n{}\n\n用户地点文本：{}".format(catalog, user_location_text)
    return system, user


def parse_location_resolution(text: str) -> LocationProposal:
    """解析模型输出；结构/枚举/数值非法时抛 AgenticParseError。"""
    if not isinstance(text, str) or not text.strip():
        raise AgenticParseError("location resolution 输出为空")
    try:
        payload = extract_json_object(text)
    except AgenticParseError as exc:
        raise exc
    if not isinstance(payload, dict):
        raise AgenticParseError("location resolution 输出不是 JSON 对象")
    try:
        return LocationProposal(
            schema_version=payload.get("schema_version"),
            status=payload.get("status"),
            matched_node_id=payload.get("matched_node_id"),
            display_name=payload.get("display_name"),
            question=payload.get("question"),
        )
    except (TypeError, ValueError) as exc:
        raise AgenticParseError(str(exc)) from None


def resolve_location(
    map_data: CampusMapData,
    text: str,
    caller: Optional[AgentCaller] = None,
    repair_caller: Optional[AgentCaller] = None,
) -> LocationResolution:
    """解析用户地点文本。

    1. 程序 exact name / alias 查找（大小写不敏感）；命中直接返回，不调用模型；
    2. 未命中且无 caller：返回 UNRESOLVED（不编造节点）；
    3. 未命中且有 caller：调用 Qwen，格式失败最多一次 repair；
       模型给出的 matched_node_id 必须真实存在于地图，否则拒绝。
    """
    if not isinstance(text, str) or not text.strip():
        return LocationResolution(
            status=LocationResolutionStatus.UNRESOLVED,
            raw_text=text if isinstance(text, str) else "",
            question="没有识别到地点描述，请补充地点。",
            campus_id=map_data.campus_id,
        )
    raw = text.strip()

    exact = map_data.resolve_node_id(raw)
    if exact is not None:
        return _resolved_from_node(map_data, exact, raw, "exact_alias")

    if caller is None:
        return LocationResolution(
            status=LocationResolutionStatus.UNRESOLVED,
            raw_text=raw,
            question="暂未识别地点“{}”，请换一种说法。".format(_truncate(raw, 40)),
            campus_id=map_data.campus_id,
        )

    proposal = _call_location_proposal(map_data, raw, caller, repair_caller)
    if proposal is None:
        return LocationResolution(
            status=LocationResolutionStatus.UNRESOLVED,
            raw_text=raw,
            question="地点识别暂未完成，请再说明一下要去哪里。",
            campus_id=map_data.campus_id,
        )

    node_ids = {node.id for node in map_data.nodes}
    if proposal.matched_node_id is not None and proposal.matched_node_id not in node_ids:
        return LocationResolution(
            status=LocationResolutionStatus.UNRESOLVED,
            raw_text=raw,
            question="地点识别结果无法对应到已知地点，请再说明一下。",
            campus_id=map_data.campus_id,
        )

    if proposal.status in (LocationResolutionStatus.RESOLVED.value, LocationResolutionStatus.APPROXIMATE.value):
        if proposal.matched_node_id is None:
            return LocationResolution(
                status=LocationResolutionStatus.UNRESOLVED,
                raw_text=raw,
                question="没有匹配到可用的地点，请再说明一下。",
            )
        status = LocationResolutionStatus(proposal.status)
        return _resolved_from_node(
            map_data,
            proposal.matched_node_id,
            raw,
            "qwen",
            status=status,
            question=_clean_question(proposal.question),
        )

    return LocationResolution(
        status=LocationResolutionStatus(proposal.status),
        raw_text=raw,
        node_id=None,
        question=_clean_question(proposal.question) or "地点暂时无法确定，请补充说明。",
        campus_id=map_data.campus_id,
    )


def _call_location_proposal(
    map_data: CampusMapData, raw: str, caller: AgentCaller, repair_caller: Optional[AgentCaller]
) -> Optional[LocationProposal]:
    system, user = build_location_resolver_prompt(map_data, raw)
    try:
        text = caller(system, user)
        return parse_location_resolution(text)
    except AgenticParseError:
        pass
    if repair_caller is None:
        return None
    from src.p2_agentic_prompt_builder import build_repair_prompt

    repair_system, repair_user = build_repair_prompt(
        "location_resolution", "", LOCATION_RESOLVER_SCHEMA_VERSION
    )
    try:
        repaired = repair_caller(repair_system, repair_user)
        return parse_location_resolution(repaired)
    except AgenticParseError:
        return None


def _resolved_from_node(
    map_data: CampusMapData,
    node_id: str,
    raw_text: str,
    matched_by: str,
    status: LocationResolutionStatus = LocationResolutionStatus.RESOLVED,
    question: Optional[str] = None,
) -> LocationResolution:
    node = _find_node(map_data, node_id)
    if node is None:
        return LocationResolution(
            status=LocationResolutionStatus.UNRESOLVED,
            raw_text=raw_text,
            question="地点识别结果无法对应到已知地点，请再说明一下。",
            campus_id=map_data.campus_id,
        )
    return LocationResolution(
        status=status,
        raw_text=raw_text,
        node_id=node.id,
        display_name=node.name,
        matched_by=matched_by,
        question=question,
        campus_id=map_data.campus_id,
    )


def _find_node(map_data: CampusMapData, node_id: str):
    for node in map_data.nodes:
        if node.id == node_id:
            return node
    return None


def _clean_question(question: Optional[str]) -> Optional[str]:
    if question is None:
        return None
    stripped = question.strip()
    return stripped or None


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "…"
