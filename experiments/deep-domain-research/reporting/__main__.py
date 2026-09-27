import argparse
import json
import logging

from .render import ReportError, render_report, retry_report


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ReportError("INVALID_ARGUMENTS", "validate", message)


def main():
    parser = Parser(prog="python -m reporting")
    commands = parser.add_subparsers(dest="command", required=True)
    render = commands.add_parser("render")
    render.add_argument("input_path")
    render.add_argument("--template-id", required=True)
    render.add_argument("--template-version", required=True)
    render.add_argument("--formats", nargs="+", required=True)
    render.add_argument("--output-dir", required=True)
    retry = commands.add_parser("retry")
    retry.add_argument("manifest_path")
    try:
        args = parser.parse_args()
        if args.command == "render":
            result = render_report(
                args.input_path,
                args.template_id,
                args.template_version,
                args.formats,
                args.output_dir,
            )
        else:
            result = retry_report(args.manifest_path)
        status = int(
            any(item["status"] == "failed" for item in result["formats"].values())
        )
    except ReportError as exc:
        result = {
            "error": {"code": exc.code, "stage": exc.stage, "details": exc.details}
        }
        status = 2
    except (OSError, ValueError, KeyError) as exc:
        logging.getLogger(__name__).exception("[REPORT] — export command failed")
        result = {
            "error": {"code": "EXPORT_FAILED", "stage": "render", "details": str(exc)}
        }
        status = 2
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
