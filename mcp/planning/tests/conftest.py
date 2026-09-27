"""Make the vendored source packages importable in the local test runner."""

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
for source_root in (
    ROOT / "sources" / "stairs-resource-model",
    ROOT / "sources" / "stairs-storage" / "src",
):
    sys.path.insert(0, str(source_root))
