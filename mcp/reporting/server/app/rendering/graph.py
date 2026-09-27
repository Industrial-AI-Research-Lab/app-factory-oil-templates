import math


WIDTH = 960
HEIGHT = 540
NODE_RADIUS = 28
LAYOUT_RADIUS_RATIO = 0.31


def graph_view(graph):
    if not graph or not graph["nodes"]:
        return None
    nodes = []
    count = len(graph["nodes"])
    radius = min(WIDTH, HEIGHT) * LAYOUT_RADIUS_RATIO
    for index, node in enumerate(graph["nodes"]):
        angle = 2 * math.pi * index / count - math.pi / 2
        nodes.append(
            {
                **node,
                "x": round(WIDTH / 2 + radius * math.cos(angle), 2),
                "y": round(HEIGHT / 2 + radius * math.sin(angle), 2),
            }
        )
    positions = {node["id"]: (node["x"], node["y"]) for node in nodes}
    edges = []
    for edge in graph["edges"]:
        source_x, source_y = positions[edge["source"]]
        target_x, target_y = positions[edge["target"]]
        if edge["source"] == edge["target"]:
            start_x = source_x - 8
            end_x = source_x + 8
            start_y = end_y = source_y - NODE_RADIUS - 2
            path = (
                f"M {start_x} {start_y} "
                f"C {source_x + 60} {source_y - 90}, "
                f"{source_x - 60} {source_y - 90}, "
                f"{end_x} {end_y}"
            )
            label_x, label_y = source_x, source_y - 92
        else:
            delta_x = target_x - source_x
            delta_y = target_y - source_y
            distance = math.hypot(delta_x, delta_y)
            offset_x = (NODE_RADIUS + 2) * delta_x / distance
            offset_y = (NODE_RADIUS + 2) * delta_y / distance
            start_x = round(source_x + offset_x, 2)
            start_y = round(source_y + offset_y, 2)
            end_x = round(target_x - offset_x, 2)
            end_y = round(target_y - offset_y, 2)
            path = f"M {start_x} {start_y} L {end_x} {end_y}"
            label_x = round((start_x + end_x) / 2, 2)
            label_y = round((start_y + end_y) / 2, 2)
        edges.append(
            {
                **edge,
                "path": path,
                "start_x": start_x,
                "start_y": start_y,
                "end_x": end_x,
                "end_y": end_y,
                "label_x": label_x,
                "label_y": label_y,
            }
        )
    return {"width": WIDTH, "height": HEIGHT, "nodes": nodes, "edges": edges}
