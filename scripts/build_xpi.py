#!/usr/bin/env python3
"""Build the Zotero add-on with the bundle's deterministic ZIP writer."""

from __future__ import annotations

from pathlib import Path

from build_windows_bundle import build_xpi


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"


def main() -> None:
    name, payload, _manifest = build_xpi()
    DIST.mkdir(parents=True, exist_ok=True)
    output = DIST / name
    output.write_bytes(payload)
    print(output)


if __name__ == "__main__":
    main()
