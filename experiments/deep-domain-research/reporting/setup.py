import argparse
import importlib.metadata
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PACKAGES = {
    "pango": ["libpango-1.0-0", "libpangoft2-1.0-0"],
    "fonts": ["fonts-dejavu-core"],
    "pdf-tools": ["poppler-utils"],
    "office": ["libreoffice-writer", "libreoffice-calc"],
}


def check():
    versions = {}
    missing = []
    for distribution in (
        "Jinja2",
        "python-docx",
        "openpyxl",
        "weasyprint",
        "pypdf",
        "httpx",
        "pytest",
    ):
        try:
            versions[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            missing.append(distribution)
    binaries = {
        name: shutil.which(name) for name in ("pdftoppm", "libreoffice", "fc-match")
    }
    missing.extend(name for name, path in binaries.items() if not path)
    result = {
        "status": "ready" if not missing else "missing",
        "versions": versions,
        "binaries": binaries,
        "missing": missing,
    }
    print(json.dumps(result))
    return int(bool(missing))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "step",
        choices=["check", "apt-index", *PACKAGES, "python", "tests"],
        default="check",
        nargs="?",
    )
    args = parser.parse_args()
    if args.step == "check":
        return check()
    if args.step == "apt-index":
        command = ["apt-get", "update", "-qq"]
    elif args.step in PACKAGES:
        command = [
            "apt-get",
            "install",
            "-y",
            "-qq",
            "--no-install-recommends",
            *PACKAGES[args.step],
        ]
    else:
        name = "requirements.txt" if args.step == "python" else "requirements-test.txt"
        command = [sys.executable, "-m", "pip", "install", "-r", str(ROOT / name)]
    logging.getLogger(__name__).info(
        "[REPORT_SETUP] step=%s — preparing container dependencies", args.step
    )
    env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive"}
    result = subprocess.run(command, env=env, check=False)
    print(json.dumps({"step": args.step, "exit_code": result.returncode}))
    return result.returncode


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
