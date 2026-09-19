"""Read-only route and campus imprint visuals for the published workbench.

No prototype assets, example routes, model callers or planning-state writes.
MovementBlock owns names, mode, distance and time. The existing local router
supplies geometry only when it agrees with that exact published block.
"""
import html
import math
from dataclasses import dataclass

from src.p3_map_schema import MapDataProvenance, RouteStatus
from src.p3_route_provider import plan_route, _build_adjacency


@dataclass(frozen=True)
class RouteVisual:
    campus_id: str
    campus_name: str
    block: object
    origin_label: str
    destination_label: str
    path: tuple
    points: tuple
    network: tuple


def next_movement(turn):
    now = turn.result.updated_state.now
    return next(iter(sorted(
        (block for block in getattr(turn, 'movement_blocks', ()) if block.end_time > now),
        key=lambda block: block.window_start,
    )), None)


def _coordinates(node):
    return (node.longitude, node.latitude)


def _has_coordinates(node):
    return node is not None and all(
        isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        for value in _coordinates(node)
    )


def _projector(nodes, selected, width, height, margin):
    latitude = sum(node.latitude for node in nodes) / len(nodes)
    longitude = sum(node.longitude for node in nodes) / len(nodes)
    factor = math.cos(math.radians(latitude))

    def xy(node):
        return ((node.longitude - longitude) * factor, latitude - node.latitude)

    points = [xy(node) for node in selected]
    left, right = min(p[0] for p in points), max(p[0] for p in points)
    top, bottom = min(p[1] for p in points), max(p[1] for p in points)
    scale = min((width - 2 * margin) / max(right - left, .00001),
                (height - 2 * margin) / max(bottom - top, .00001))
    center = ((left + right) / 2, (top + bottom) / 2)
    return lambda node: ((xy(node)[0] - center[0]) * scale + width / 2,
                         (xy(node)[1] - center[1]) * scale + height / 2)


def _route_label(map_data, node_id, published_name):
    from src.workspace_ui import place_display_name
    display_name = place_display_name(map_data.campus_id, node_id, published_name)
    if display_name != published_name:
        return display_name
    # Remove only a redundant campus prefix whose remainder resolves to the
    # same real POI. A convenient-looking nickname is never invented.
    if published_name.startswith(map_data.campus):
        short = published_name[len(map_data.campus):].strip()
        if short and map_data.resolve_node_id(short) == node_id:
            return short
    return published_name


def build_next_route(turn, map_data):
    """Return geometry for the next active/future block, or no visual.

    Never skip a failed next leg to show a later route. Never derive a route
    from task titles, current settings, a saved example or mutable draft keys.
    """
    block = next_movement(turn)
    if block is None or map_data is None:
        return None
    if map_data.provenance is not MapDataProvenance.REAL_MAP or not map_data.campus_id:
        return None
    context = getattr(turn, 'execution_context', None)
    locations = [getattr(getattr(context, 'current_location', None), 'location', None)]
    locations += [getattr(binding, 'execution_location', None)
                  for binding in getattr(context, 'bindings', ())]
    if any(location.campus_id != map_data.campus_id for location in locations if location):
        return None
    origin, destination = getattr(block, 'origin_node_id', None), getattr(block, 'destination_node_id', None)
    if not origin or not destination or origin == destination:
        return None
    if map_data.resolve_node_id(origin) != origin or map_data.resolve_node_id(destination) != destination:
        return None
    distance, minutes = getattr(block, 'distance_m', 0), getattr(block, 'estimated_minutes', 0)
    if not distance or distance <= 0 or not minutes or minutes <= 0:
        return None
    try:
        route = plan_route(map_data, origin, destination, block.mode)
    except (TypeError, ValueError):
        return None
    if (route.status is not RouteStatus.COMPUTED or route.provenance is not MapDataProvenance.REAL_MAP
            or route.total_distance_m != distance or len(route.path) < 2):
        return None
    by_id = {node.id: node for node in map_data.nodes}
    if not all(_has_coordinates(by_id.get(node_id)) for node_id in route.path):
        return None
    usable = [node for node in map_data.nodes if _has_coordinates(node)]
    project = _projector(usable, [by_id[node_id] for node_id in route.path], 480, 330, 68)
    adjacency = _build_adjacency(map_data, route.mode)
    edges = sorted({tuple(sorted((a, b))) for a, neighbours in adjacency.items() for b, _ in neighbours
                    if a != b and _has_coordinates(by_id.get(a)) and _has_coordinates(by_id.get(b))})
    from src.p3_campus_registry import DEFAULT_CAMPUS_REGISTRY
    registration = next((item for item in DEFAULT_CAMPUS_REGISTRY.list_campuses() if item.campus_id == map_data.campus_id), None)
    campus_name = registration.display_name if registration else map_data.campus
    return RouteVisual(map_data.campus_id, campus_name, block,
                       _route_label(map_data, origin, block.origin_name),
                       _route_label(map_data, destination, block.destination_name), tuple(route.path),
                       tuple(project(by_id[node_id]) for node_id in route.path),
                       tuple((project(by_id[a]), project(by_id[b])) for a, b in edges))


def _path(points):
    return ''.join(('M' if index == 0 else 'L') + '{:.2f},{:.2f}'.format(*point)
                   for index, point in enumerate(points))


def _label(point, name, other, occupied):
    # Keep complete names; wrap labels rather than clipping long campus POIs.
    rows, row, units = [], '', 0
    for character in str(name):
        advance = 1 if ord(character) > 255 else .58
        if units + advance > 11:
            rows.append(row)
            row, units = '', 0
        row += character
        units += advance
    rows.append(row)
    width = max(sum(16 if ord(ch) > 255 else 9.3 for ch in row) for row in rows) + 20
    height = 22 * len(rows) + 12
    x, y = point
    preferred = x - width - 17 if other[0] >= x else x + 17
    options = [(preferred, y - height / 2), (x + 17 if preferred < x else x - width - 17, y - height / 2),
               (x - width / 2, y - height - 18), (x - width / 2, y + 18)]
    boxes = occupied + [(x - 12, y - 12, 24, 24), (other[0] - 12, other[1] - 12, 24, 24)]
    def hits(a, b):
        return a[0] < b[0] + b[2] + 4 and a[0] + a[2] + 4 > b[0] and a[1] < b[1] + b[3] + 4 and a[1] + a[3] + 4 > b[1]
    for candidate in options:
        box = (max(10, min(470 - width, candidate[0])), max(10, min(304 - height, candidate[1])), width, height)
        if not any(hits(box, item) for item in boxes):
            break
    occupied.append(box)
    spans = ''.join('<tspan x="{:.2f}" y="{:.2f}">{}</tspan>'.format(box[0] + 10, box[1] + 23 + index * 22, html.escape(row)) for index, row in enumerate(rows))
    return '<g class="cf-map-label"><rect x="{:.2f}" y="{:.2f}" width="{:.2f}" height="{:.2f}" rx="4"/><text>{}</text></g>'.format(*box, spans)


def route_map_html(visual):
    block = visual.block
    escape = lambda value: html.escape(str(value), quote=True)
    mode = '骑行' if getattr(block.mode, 'value', block.mode) == 'bike' else '步行'
    origin, destination = escape(block.origin_name), escape(block.destination_name)
    start, end = visual.points[0], visual.points[-1]
    labels = []
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 480 330" role="img" aria-label="{}到{}，{}米，{}约{}分钟" data-campus-id="{}" data-path-nodes="{}">'.format(origin, destination, block.distance_m, mode, block.estimated_minutes, escape(visual.campus_id), escape(','.join(visual.path)))
    svg += '<path class="cf-map-underlay" d="{}"/><path class="cf-map-route" d="{}"/>'.format(_path(visual.points), _path(visual.points))
    svg += ''.join('<circle class="cf-map-via" cx="{:.2f}" cy="{:.2f}" r="3"/>'.format(*point) for point in visual.points[1:-1])
    svg += '<circle class="cf-map-origin" cx="{:.2f}" cy="{:.2f}" r="7"/>'.format(*start)
    svg += '<rect class="cf-map-destination" x="{:.2f}" y="{:.2f}" width="13" height="13" rx="1"/>'.format(end[0] - 6.5, end[1] - 6.5)
    svg += _label(start, visual.origin_label, end, labels) + _label(end, visual.destination_label, start, labels)
    svg += '<text x="463" y="23" text-anchor="end" class="cf-map-north">N ↑</text></svg>'
    depart, arrive = block.window_start.strftime('%H:%M'), block.end_time.strftime('%H:%M')
    pack = '<span><b>{}</b> 收拾</span>'.format(block.transition_start.strftime('%H:%M')) if block.transition_start else ''
    return ('<section class="cf-mini-route" aria-label="下一段校园移动" data-campus-id="{}" data-distance-m="{}" data-minutes="{}" data-mode="{}" data-origin-name="{}" data-destination-name="{}">'
            '<div class="cf-mini-heading">下一段校园移动<span>{}</span></div><div class="cf-mini-map">{}</div>'
            '<div class="cf-mini-stops"><span>{}</span><span aria-hidden="true">→</span><strong>{}</strong></div>'
            '<div class="cf-mini-facts"><strong>{}<small>m</small></strong><span>{}约 {} 分钟</span></div>'
            '<div class="cf-mini-times">{}<span class="cf-mini-depart"><b>{}</b> 出发</span><span><b>{}</b> 预计抵达</span></div></section>').format(
                escape(visual.campus_id), block.distance_m, block.estimated_minutes, escape(getattr(block.mode, 'value', block.mode)), origin, destination,
                escape(visual.campus_name), svg, escape(visual.origin_label), escape(visual.destination_label), block.distance_m, mode, block.estimated_minutes, pack, depart, arrive)
