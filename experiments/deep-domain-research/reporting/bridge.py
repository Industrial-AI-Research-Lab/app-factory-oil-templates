import argparse
import base64
import hashlib
import io
import json
import zipfile
from pathlib import Path


def pack(folder):
    folder = Path(folder).resolve(strict=True)
    stream = io.BytesIO()
    records = []
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(folder.rglob("*")):
            if path.is_symlink():
                raise ValueError("Symlinks cannot be exported")
            if path.is_file():
                data = path.read_bytes()
                name = path.relative_to(folder).as_posix()
                archive.writestr(name, data)
                records.append(
                    {
                        "path": name,
                        "size_bytes": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                    }
                )
    payload = stream.getvalue()
    return {
        "encoding": "base64-zip",
        "archive_sha256": hashlib.sha256(payload).hexdigest(),
        "files": records,
        "data": base64.b64encode(payload).decode("ascii"),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", type=Path)
    args = parser.parse_args()
    print(json.dumps(pack(args.folder)))
