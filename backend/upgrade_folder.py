#!/usr/bin/env python3
"""Upgrade newly produced PDF2zh geometry sidecars to semantic v3 maps."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from semantic_realign_map import realign


def upgrade_one(path: Path, cache_dir: Path, model: str) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if int(payload.get("version", 1)) >= 3:
        return "already-v3"
    temporary = path.with_name(path.name + ".semantic.tmp")
    realign(path, temporary, model, cache_dir)
    os.replace(temporary, path)
    return "upgraded"


def scan(folder: Path, cache_dir: Path, model: str) -> dict:
    result = {"upgraded": 0, "already-v3": 0, "failed": []}
    for path in sorted(folder.glob("*.compare.pdf.bilingual.json")):
        try:
            state = upgrade_one(path, cache_dir, model)
            result[state] += 1
        except Exception as error:
            result["failed"].append({"path": str(path), "error": str(error)})
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--translated-dir", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, default=Path.home() / ".cache" / "bilingual-linked-reader")
    parser.add_argument("--model", default="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=float, default=3.0)
    args = parser.parse_args()
    while True:
        print(json.dumps(scan(args.translated_dir, args.cache_dir, args.model), ensure_ascii=False), flush=True)
        if not args.watch:
            break
        time.sleep(max(args.interval, 1.0))


if __name__ == "__main__":
    main()
