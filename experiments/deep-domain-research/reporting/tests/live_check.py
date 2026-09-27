import argparse
import hashlib
import json
from pathlib import Path

from reporting.render import MIMES, render_report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--case", choices=["knowledge", "news", "empty", "long"], required=True
    )
    parser.add_argument("--version", default="1.0.0")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    filename = "small.json" if args.case == "knowledge" else f"{args.case}.json"
    fixture = Path(__file__).parent / "fixtures" / filename
    data = json.loads(fixture.read_text(encoding="utf-8"))
    data.update(project_id=args.project_id, run_id=args.run_id)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    source = (
        args.output_dir
        / f"control-{args.case}-{hashlib.sha256(payload).hexdigest()}.json"
    )
    if source.exists() and source.read_bytes() != payload:
        raise ValueError("Control input conflict")
    source.write_bytes(payload)
    family = "news" if args.case == "news" else "knowledge"
    manifest = render_report(source, family, args.version, list(MIMES), args.output_dir)
    print(json.dumps(manifest, ensure_ascii=False))
    return int(
        any(item["status"] != "generated" for item in manifest["formats"].values())
    )


if __name__ == "__main__":
    raise SystemExit(main())
