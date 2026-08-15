#!/usr/bin/env python3
"""Build the Zotero add-on without external tooling."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "addon"
DIST = ROOT / "dist"


def main() -> None:
    manifest = json.loads((ADDON / "manifest.json").read_text(encoding="utf-8"))
    DIST.mkdir(parents=True, exist_ok=True)
    output = DIST / f"bilingual-linked-reader-{manifest['version']}.xpi"
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(ADDON.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(ADDON).as_posix())
    print(output)


if __name__ == "__main__":
    main()
