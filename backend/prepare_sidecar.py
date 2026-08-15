#!/usr/bin/env python3
"""Generate a geometry map and optionally upgrade it to semantic sentence alignment."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from generate_map import generate_direct_dual_map, generate_map
from semantic_realign_map import realign


def prepare(
    original: Path | None,
    translated: Path | None,
    compare: Path,
    output: Path,
    cache_dir: Path,
    model: str,
    geometry_only: bool,
    direct_dual: bool = True,
) -> dict:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".v2.tmp")
    if direct_dual:
        geometry = generate_direct_dual_map(str(compare), str(temporary))
    else:
        if original is None or translated is None:
            raise ValueError("original and translated PDFs are required for legacy intermediate alignment")
        geometry = generate_map(str(original), str(translated), str(compare), str(temporary))
    if geometry_only:
        os.replace(temporary, output)
        return {"version": geometry["version"], **geometry["stats"]}
    try:
        stats = realign(temporary, output, model, cache_dir)
        temporary.unlink(missing_ok=True)
        return {"version": 4 if direct_dual else 3, **stats}
    except Exception as error:
        os.replace(temporary, output)
        return {"version": geometry["version"], "semanticError": str(error), **geometry["stats"]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--original", type=Path)
    parser.add_argument("--translated", type=Path, help="Chinese mono PDF")
    parser.add_argument("--compare", type=Path, required=True, help="Side-by-side PDF")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--cache-dir", type=Path, default=Path.home() / ".cache" / "bilingual-linked-reader")
    parser.add_argument("--model", default="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
    parser.add_argument("--geometry-only", action="store_true")
    parser.add_argument("--legacy-intermediate", action="store_true", help="Use separate original/mono geometry instead of the final dual PDF")
    args = parser.parse_args()
    output = args.output or Path(str(args.compare) + ".bilingual.json")
    result = prepare(
        args.original,
        args.translated,
        args.compare,
        output,
        args.cache_dir,
        args.model,
        args.geometry_only,
        not args.legacy_intermediate,
    )
    print(json.dumps({"output": str(output), **result}, ensure_ascii=False))


if __name__ == "__main__":
    main()
