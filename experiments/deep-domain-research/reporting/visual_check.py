import argparse
import json
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4


def inspect_file(source, output):
    source = source.resolve(strict=True)
    output.mkdir(parents=True, exist_ok=False)
    if source.suffix in {".docx", ".xlsx"}:
        if not shutil.which("libreoffice"):
            raise RuntimeError(
                "LibreOffice is required for native Office visual verification"
            )
        subprocess.run(
            [
                "libreoffice",
                f"-env:UserInstallation=file:///tmp/b05-office-{uuid4()}",
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                str(output),
                str(source),
            ],
            check=True,
            timeout=120,
        )
        pdf = output / source.with_suffix(".pdf").name
    else:
        pdf = source
    subprocess.run(
        ["pdftoppm", "-png", "-scale-to", "1500", str(pdf), str(output / "page")],
        check=True,
        timeout=120,
    )
    pages = sorted(output.glob("page-*.png"))
    if not pages:
        raise RuntimeError("Viewer produced no pages")
    print(
        json.dumps(
            {
                "source": str(source),
                "pages": [str(page) for page in pages],
                "visual_review": "pending",
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    inspect_file(args.source, args.output)
