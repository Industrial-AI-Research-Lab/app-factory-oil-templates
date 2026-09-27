MISSING = "Нет данных"


def display(value):
    if value is None:
        return MISSING
    if isinstance(value, bool):
        return "Да" if value else "Нет"
    return str(value)


def metadata(data, family):
    labels = {
        "question": "Вопрос",
        "schema_version": "Версия схемы",
        "collection_version": "Версия коллекции",
        "topic": "Тема",
        "period": "Период",
        "conditions": "Условия и основания отбора",
        "publication_count": "Количество публикаций",
        "event_count": "Количество событий",
        "categories": "Категории",
    }
    keys = (
        ("question", "schema_version", "collection_version")
        if family == "knowledge"
        else (
            "topic",
            "period",
            "conditions",
            "publication_count",
            "event_count",
            "categories",
        )
    )
    return [
        (
            labels[key],
            (
                ", ".join(data["metadata"][key]) or MISSING
                if key == "categories"
                else data["metadata"][key]
            ),
        )
        for key in keys
    ]


def headers(table):
    return [
        c["label"] + (f" ({c['unit']})" if c["unit"] is not None else "")
        for c in table["columns"]
    ] + ["Источники"]


def rows(table):
    for row in table["rows"]:
        yield [row["values"][c["key"]] for c in table["columns"]] + [
            ", ".join(row["source_ids"]) or MISSING
        ]


def text_report(data, style, family):
    lines = [
        style["heading"],
        data["title"],
        f"{data['project_id']} / {data['run_id']} / {data['result_id']} / {data['result_version']}",
    ]
    lines.extend(f"{label}: {value}" for label, value in metadata(data, family))
    lines.append(data["answer"])
    for table in data["tables"]:
        lines.extend([table["title"], " | ".join(headers(table))])
        lines.extend(" | ".join(map(display, row)) for row in rows(table))
        if not table["rows"]:
            lines.append(MISSING)
    lines.append("Источники")
    lines.extend(
        f"{s['id']} — {s['title']} — {display(s['url'])}" for s in data["sources"]
    )
    if not data["sources"]:
        lines.append(MISSING)
    lines.append("Ограничения")
    lines.extend(data["limitations"] or [MISSING])
    return "\n\n".join(lines)
