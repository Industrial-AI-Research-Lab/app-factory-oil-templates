import json
from io import BytesIO
from pathlib import Path

import pytest
from pypdf import PdfReader


def knowledge_data():
    data = json.loads((Path(__file__).parent / "fixtures" / "small.json").read_text())
    data["graph"] = {
        "nodes": [
            {
                "id": "depth",
                "label": "Глубина <опасно>",
                "type": "measurement",
                "source_ids": ["s1"],
            },
            {
                "id": "well",
                "label": "Скважина",
                "type": "asset",
                "source_ids": [],
            },
        ],
        "edges": [
            {
                "id": "measured-at",
                "source": "depth",
                "target": "well",
                "label": "измерено в",
                "source_ids": ["s1"],
            }
        ],
    }
    return data


def render(data, family="knowledge", version="1.2.0"):
    from app.rendering.render import render_bytes

    result = render_bytes(data, family, version, ["html"])
    return result.formats["html"].payload.decode("utf-8")


def test_knowledge_html_contains_self_contained_graph_and_equivalent_table():
    html = render(knowledge_data())

    assert '<svg class="knowledge-graph"' in html
    assert "Глубина &lt;опасно&gt;" in html
    assert "depth → well" in html
    assert "измерено в" in html
    assert "measurement" in html
    assert "Версия результата:</strong> 1" in html
    assert "Версия схемы:</strong> 1" in html
    assert "Версия коллекции:</strong> 2" in html
    assert "<script" not in html
    assert "@import" not in html
    assert "url(http" not in html


def test_knowledge_graph_renders_to_pdf_with_cyrillic_text():
    from app.rendering.render import render_bytes

    result = render_bytes(knowledge_data(), "knowledge", "1.2.0", ["pdf"])

    assert result.formats["pdf"].status == "generated"
    pages = PdfReader(BytesIO(result.formats["pdf"].payload)).pages
    text = "".join(page.extract_text() for page in pages)
    assert "Глубина" in text
    assert "Скважина" in text


def test_directed_edge_stops_outside_target_node():
    import math

    from app.rendering.graph import NODE_RADIUS, graph_view

    view = graph_view(knowledge_data()["graph"])
    nodes = {node["id"]: node for node in view["nodes"]}
    edge = view["edges"][0]

    assert (
        math.dist(
            (edge["start_x"], edge["start_y"]),
            (nodes[edge["source"]]["x"], nodes[edge["source"]]["y"]),
        )
        >= NODE_RADIUS
    )
    assert (
        math.dist(
            (edge["end_x"], edge["end_y"]),
            (nodes[edge["target"]]["x"], nodes[edge["target"]]["y"]),
        )
        >= NODE_RADIUS
    )


def test_self_loop_arrow_stops_outside_node_stroke():
    import math

    from app.rendering.graph import NODE_RADIUS, graph_view

    graph = knowledge_data()["graph"]
    graph["edges"][0].update(source="depth", target="depth")
    view = graph_view(graph)
    node = view["nodes"][0]
    edge = view["edges"][0]

    assert (
        math.dist((edge["start_x"], edge["start_y"]), (node["x"], node["y"]))
        > NODE_RADIUS
    )
    assert (
        math.dist((edge["end_x"], edge["end_y"]), (node["x"], node["y"])) > NODE_RADIUS
    )


def test_single_node_self_loop_stays_inside_viewport():
    import re

    from app.rendering.graph import HEIGHT, WIDTH, graph_view

    graph = knowledge_data()["graph"]
    graph["nodes"] = graph["nodes"][:1]
    graph["edges"][0].update(source="depth", target="depth")
    edge = graph_view(graph)["edges"][0]
    coordinates = [
        float(value) for value in re.findall(r"-?\d+(?:\.\d+)?", edge["path"])
    ]

    assert all(0 <= value <= WIDTH for value in coordinates[0::2])
    assert all(0 <= value <= HEIGHT for value in coordinates[1::2])
    assert 0 <= edge["label_y"] <= HEIGHT


@pytest.mark.parametrize("graph", [None, {"nodes": [], "edges": []}])
def test_missing_or_empty_graph_has_explicit_empty_state(graph):
    data = knowledge_data()
    data["graph"] = graph

    html = render(data)

    assert '<section id="subgraph">' in html
    assert "Нет данных" in html
    assert '<svg class="knowledge-graph"' not in html


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(
            lambda graph: graph["nodes"].append(dict(graph["nodes"][0])),
            id="duplicate-node-id",
        ),
        pytest.param(
            lambda graph: graph["edges"].append(dict(graph["edges"][0])),
            id="duplicate-edge-id",
        ),
        pytest.param(
            lambda graph: graph["edges"][0].update(target="missing"),
            id="dangling-edge",
        ),
        pytest.param(
            lambda graph: graph["nodes"][0]["source_ids"].append("missing"),
            id="unknown-node-source",
        ),
        pytest.param(
            lambda graph: graph["edges"][0]["source_ids"].append("missing"),
            id="unknown-edge-source",
        ),
    ],
)
def test_invalid_graph_references_are_rejected(mutate):
    from app.errors import ReportError

    data = knowledge_data()
    mutate(data["graph"])

    with pytest.raises(ReportError) as captured:
        render(data)

    assert captured.value.code == "INVALID_INPUT"
    assert captured.value.stage == "validate"


def test_graph_limits_accept_boundary_and_reject_larger_input(monkeypatch):
    from app.errors import ReportError

    monkeypatch.setenv("REPORT_GRAPH_MAX_NODES", "2")
    monkeypatch.setenv("REPORT_GRAPH_MAX_EDGES", "1")
    assert "Глубина" in render(knowledge_data())

    data = knowledge_data()
    data["graph"]["nodes"].append(
        {"id": "third", "label": "Третий", "type": "entity", "source_ids": []}
    )
    with pytest.raises(ReportError) as captured:
        render(data)

    assert captured.value.code == "GRAPH_TOO_LARGE"
    assert captured.value.details == {
        "max_nodes": 2,
        "actual_nodes": 3,
        "max_edges": 1,
        "actual_edges": 1,
    }

    data = knowledge_data()
    data["graph"]["edges"].append(
        {
            "id": "reverse",
            "source": "well",
            "target": "depth",
            "label": "обратно",
            "source_ids": [],
        }
    )
    with pytest.raises(ReportError) as captured:
        render(data)

    assert captured.value.code == "GRAPH_TOO_LARGE"
    assert captured.value.details["actual_edges"] == 2


@pytest.mark.parametrize(
    "graph",
    [
        {},
        {"nodes": None, "edges": []},
        {"nodes": [], "edges": None},
        {
            "nodes": [{"id": 1, "label": "Узел", "type": "entity", "source_ids": []}],
            "edges": [],
        },
        {
            "nodes": [
                {"id": "n1", "label": "Узел", "type": "entity", "source_ids": None}
            ],
            "edges": [],
        },
    ],
)
def test_malformed_graph_shapes_are_rejected(graph):
    from app.errors import ReportError

    data = knowledge_data()
    data["graph"] = graph

    with pytest.raises(ReportError) as captured:
        render(data)

    assert captured.value.code == "INVALID_INPUT"
    assert captured.value.stage == "validate"


@pytest.mark.parametrize(
    "name,value",
    [
        ("REPORT_GRAPH_MAX_NODES", "0"),
        ("REPORT_GRAPH_MAX_EDGES", "-1"),
        ("REPORT_GRAPH_MAX_NODES", "many"),
    ],
)
def test_invalid_graph_limit_configuration_fails_closed(monkeypatch, name, value):
    monkeypatch.setenv(name, value)

    with pytest.raises(RuntimeError, match="graph limits must be positive integers"):
        render(knowledge_data())


def test_news_report_rejects_knowledge_graph():
    from app.errors import ReportError

    data = knowledge_data()
    data["metadata"] = {
        "topic": "Тема",
        "period": "2026-09-17",
        "conditions": "Все",
        "publication_count": 1,
        "event_count": 1,
        "categories": [],
    }

    with pytest.raises(ReportError) as captured:
        render(data, "news", "1.1.0")

    assert captured.value.code == "INVALID_INPUT"


def test_published_knowledge_templates_reject_nonempty_graphs():
    from app.errors import ReportError

    with pytest.raises(ReportError) as captured:
        render(knowledge_data(), version="1.1.0")

    assert captured.value.code == "GRAPH_TEMPLATE_UNSUPPORTED"
    assert captured.value.stage == "validate"
