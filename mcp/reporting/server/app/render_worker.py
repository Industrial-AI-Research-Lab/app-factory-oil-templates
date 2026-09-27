import json
import os
import pickle
import sys
from pathlib import Path

from app.rendering.render import render_prepared_bytes


def main():
    report_path, result_path, template_id, template_version, fmt = sys.argv[1:]
    original = Path(report_path).read_bytes()
    data = json.loads(original)
    rendered = render_prepared_bytes(
        original,
        data,
        template_id,
        template_version,
        [fmt],
    )
    destination = Path(result_path)
    temporary = destination.with_suffix(".tmp")
    temporary.write_bytes(pickle.dumps(rendered.formats[fmt]))
    os.replace(temporary, destination)


if __name__ == "__main__":
    main()
