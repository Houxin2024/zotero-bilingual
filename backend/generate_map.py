#!/usr/bin/env python3
"""Build paragraph/sentence coordinate pairs for a side-by-side bilingual PDF."""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path

import pymupdf


def clean_text(value: str) -> str:
    value = value.replace("\x00", " ").replace("\x01", " ")
    return re.sub(r"\s+", " ", value).strip()


def union_box(boxes: list[list[float]]) -> list[float] | None:
    if not boxes:
        return None
    return [
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    ]


def rounded(box: list[float] | None) -> list[float] | None:
    return [round(float(value), 2) for value in box] if box else None


def line_rects(chars: list[dict]) -> list[list[float]]:
    grouped: dict[int, list[list[float]]] = {}
    order: list[int] = []
    for item in chars:
        if not item.get("box") or not item.get("c", "").strip():
            continue
        line = int(item.get("line", 0))
        if line not in grouped:
            grouped[line] = []
            order.append(line)
        grouped[line].append(item["box"])
    return [union_box(grouped[line]) for line in order if grouped[line]]


def sentence_units(chars: list[dict], language: str) -> list[dict]:
    """Split a block into ordered sentence units while retaining PDF geometry."""
    units: list[dict] = []
    current: list[dict] = []
    abbreviations = {"fig.", "eq.", "ref.", "refs.", "dr.", "mr.", "mrs.", "et al."}

    def flush() -> None:
        nonlocal current
        text = clean_text("".join(item["c"] for item in current))
        rects = line_rects(current)
        boxes = [item["box"] for item in current if item["c"].strip() and item.get("box")]
        if text and boxes:
            units.append({
                "text": text,
                "box": rounded(union_box(boxes)),
                "rects": [rounded(rect) for rect in rects],
            })
        current = []

    for index, item in enumerate(chars):
        current.append(item)
        char = item["c"]
        if language == "zh":
            terminal = char in "。！？"
        else:
            terminal = char in "!?"
            if char == ".":
                prefix = clean_text("".join(entry["c"] for entry in current)).lower()
                is_abbreviation = any(prefix.endswith(value) for value in abbreviations)
                next_nonspace = ""
                for following in chars[index + 1:]:
                    if following["c"].strip():
                        next_nonspace = following["c"]
                        break
                terminal = not is_abbreviation and (not next_nonspace or next_nonspace.isupper() or next_nonspace.isdigit() or next_nonspace in "([")
        if terminal and len(clean_text("".join(entry["c"] for entry in current))) >= 12:
            flush()
    flush()
    return units


def extract_blocks(page: pymupdf.Page, language: str) -> list[dict]:
    result: list[dict] = []
    raw = page.get_text("rawdict", sort=True)
    for block in raw.get("blocks", []):
        if block.get("type") != 0:
            continue
        chars: list[dict] = []
        lines = block.get("lines", [])
        for line_index, line in enumerate(lines):
            if line_index:
                chars.append({"c": " ", "box": None, "line": line_index})
            for span in line.get("spans", []):
                for char in span.get("chars", []):
                    chars.append({
                        "c": char.get("c", ""),
                        "box": [float(v) for v in char.get("bbox", span.get("bbox"))],
                        "line": line_index,
                    })
        text = clean_text("".join(item["c"] for item in chars))
        if not text:
            continue
        result.append({
            "text": text,
            "box": [float(value) for value in block["bbox"]],
            "sentences": sentence_units(chars, language),
        })
    return result


def column_index(box: list[float], width: float) -> int:
    center = (box[0] + box[2]) / 2
    return min(1, max(0, int(center / max(width / 2, 1))))


def spatial_cost(source: dict, target: dict, width: float, height: float) -> float:
    sx0, sy0, sx1, sy1 = source["box"]
    tx0, ty0, tx1, ty1 = target["box"]
    scx, scy = (sx0 + sx1) / 2, (sy0 + sy1) / 2
    tcx, tcy = (tx0 + tx1) / 2, (ty0 + ty1) / 2
    spans_page = (sx1 - sx0) > width * 0.68 or (tx1 - tx0) > width * 0.68
    column_penalty = 0.0 if spans_page or column_index(source["box"], width) == column_index(target["box"], width) else 1.8
    vertical_inside = ty0 - 8 <= scy <= ty1 + 8
    dy = abs(scy - tcy) / max(height, 1)
    dx = abs(scx - tcx) / max(width, 1)
    height_ratio = abs(math.log(max(sy1 - sy0, 2) / max(ty1 - ty0, 2)))
    return column_penalty + 3.1 * dy + 0.35 * dx + 0.08 * height_ratio - (0.18 if vertical_inside else 0)


def to_pdf_box(box: list[float], page_height: float, offset: float = 0.0) -> list[float]:
    """Convert PyMuPDF top-left coordinates to Zotero/PDF bottom-left coordinates."""
    return [box[0] + offset, page_height - box[3], box[2] + offset, page_height - box[1]]


def transform_sentences(sentences: list[dict], page_height: float, offset: float = 0.0) -> list[dict]:
    return [
        {
            "text": unit["text"],
            "box": rounded(to_pdf_box(unit["box"], page_height, offset)),
            "rects": [rounded(to_pdf_box(rect, page_height, offset)) for rect in unit.get("rects", [unit["box"]])],
        }
        for unit in sentences
    ]


def merge_sentence_fragments(units: list[dict], language: str) -> list[dict]:
    """Merge PDF-block fragments that do not end at sentence punctuation."""
    if not units:
        return []
    terminal_pattern = re.compile(r"[。！？!?]\s*$") if language == "zh" else re.compile(r"[.!?][\d,;:()\[\]–—\s]*$")
    merged: list[dict] = []
    current: dict | None = None
    for unit in units:
        if current is None:
            current = {
                "text": unit["text"],
                "rects": list(unit.get("rects", [unit["box"]])),
            }
        else:
            current["text"] = clean_text(current["text"] + " " + unit["text"])
            current["rects"].extend(unit.get("rects", [unit["box"]]))
        if terminal_pattern.search(current["text"]):
            current["box"] = rounded(union_box(current["rects"]))
            merged.append(current)
            current = None
    if current is not None:
        current["box"] = rounded(union_box(current["rects"]))
        merged.append(current)
    return merged


def align_sentence_units(en_units: list[dict], zh_units: list[dict]) -> list[dict]:
    """Monotonic length-aware 1-to-many / many-to-1 sentence alignment."""
    en_units = merge_sentence_fragments(en_units, "en")
    zh_units = merge_sentence_fragments(zh_units, "zh")
    if not en_units or not zh_units:
        return []
    n, m = len(en_units), len(zh_units)
    if n == m:
        groups = [(index, index + 1, index, index + 1) for index in range(n)]
    else:
        groups = None
    total_en = sum(max(len(clean_text(unit["text"])), 1) for unit in en_units)
    total_zh = sum(max(len(clean_text(unit["text"])), 1) for unit in zh_units)
    expected_ratio = max(0.18, min(1.5, total_zh / max(total_en, 1)))
    if groups is None:
        infinity = float("inf")
        costs = [[infinity] * (m + 1) for _ in range(n + 1)]
        previous: list[list[tuple[int, int, int, int] | None]] = [[None] * (m + 1) for _ in range(n + 1)]
        costs[0][0] = 0.0
        for i in range(n + 1):
            for j in range(m + 1):
                if not math.isfinite(costs[i][j]):
                    continue
                for en_count in range(1, 4):
                    for zh_count in range(1, 4):
                        if i + en_count > n or j + zh_count > m:
                            continue
                        en_len = sum(len(clean_text(unit["text"])) for unit in en_units[i:i + en_count])
                        zh_len = sum(len(clean_text(unit["text"])) for unit in zh_units[j:j + zh_count])
                        ratio = zh_len / max(en_len, 1)
                        ratio_cost = abs(math.log(max(ratio, 1e-3) / expected_ratio))
                        group_cost = 0.72 * (en_count + zh_count - 2) + 0.16 * abs(en_count - zh_count)
                        new_cost = costs[i][j] + ratio_cost + group_cost
                        ni, nj = i + en_count, j + zh_count
                        if new_cost < costs[ni][nj]:
                            costs[ni][nj] = new_cost
                            previous[ni][nj] = (i, j, en_count, zh_count)
        if previous[n][m] is None:
            groups = [(0, n, 0, m)]
        else:
            groups = []
            i, j = n, m
            while i or j:
                step = previous[i][j]
                if step is None:
                    groups = [(0, n, 0, m)]
                    break
                pi, pj, _, _ = step
                groups.append((pi, i, pj, j))
                i, j = pi, pj
            groups.reverse()

    pairs = []
    for pair_index, (en_start, en_end, zh_start, zh_end) in enumerate(groups):
        en_group = en_units[en_start:en_end]
        zh_group = zh_units[zh_start:zh_end]
        en_rects = [rect for unit in en_group for rect in unit.get("rects", [unit["box"]])]
        zh_rects = [rect for unit in zh_group for rect in unit.get("rects", [unit["box"]])]
        pairs.append({
            "pairIndex": pair_index,
            "enText": clean_text(" ".join(unit["text"] for unit in en_group)),
            "zhText": clean_text(" ".join(unit["text"] for unit in zh_group)),
            "enRects": en_rects,
            "zhRects": zh_rects,
            "enBox": rounded(union_box(en_rects)),
            "zhBox": rounded(union_box(zh_rects)),
        })
    return pairs


def align_page(en_blocks: list[dict], zh_blocks: list[dict], page_width: float, page_height: float, page_index: int, right_offset: float, output_height: float) -> list[dict]:
    assignments: list[list[dict]] = [[] for _ in en_blocks]
    for zh in zh_blocks:
        if not en_blocks:
            continue
        best_index, best_cost = min(
            ((index, spatial_cost(zh, en, page_width, page_height)) for index, en in enumerate(en_blocks)),
            key=lambda value: value[1],
        )
        if best_cost <= 0.78:
            assignments[best_index].append(zh)

    rows: list[dict] = []
    for index, en in enumerate(en_blocks):
        matched = sorted(assignments[index], key=lambda block: (block["box"][1], block["box"][0]))
        if not matched:
            continue
        zh_text = clean_text(" ".join(block["text"] for block in matched))
        zh_boxes = [block["box"] for block in matched]
        zh_sentences = [unit for block in matched for unit in block["sentences"]]
        en_sentences = transform_sentences(en["sentences"], output_height, right_offset)
        zh_sentences = transform_sentences(zh_sentences, output_height)
        rows.append({
            "id": f"p{page_index + 1}-b{index}",
            "pageIndex": page_index,
            "en": en["text"],
            "zh": zh_text,
            "enBox": rounded(to_pdf_box(en["box"], output_height, right_offset)),
            "zhBox": rounded(to_pdf_box(union_box(zh_boxes), output_height)),
            "enSentences": en_sentences,
            "zhSentences": zh_sentences,
            "sentencePairs": align_sentence_units(en_sentences, zh_sentences),
        })
    return rows


def generate_map(original_path: str, translated_path: str, compare_path: str, output_path: str) -> dict:
    original = pymupdf.open(original_path)
    translated = pymupdf.open(translated_path)
    compare = pymupdf.open(compare_path)
    page_count = min(original.page_count, translated.page_count, compare.page_count)
    segments: list[dict] = []
    pages: list[dict] = []
    for page_index in range(page_count):
        en_page = original[page_index]
        zh_page = translated[page_index]
        compare_page = compare[page_index]
        right_offset = compare_page.rect.width - en_page.rect.width
        pages.append({
            "pageIndex": page_index,
            "width": round(compare_page.rect.width, 2),
            "height": round(compare_page.rect.height, 2),
            "rightOffset": round(right_offset, 2),
        })
        segments.extend(align_page(
            extract_blocks(en_page, "en"),
            extract_blocks(zh_page, "zh"),
            en_page.rect.width,
            en_page.rect.height,
            page_index,
            right_offset,
            compare_page.rect.height,
        ))
    payload = {
        "version": 2,
        "compareFile": Path(compare_path).name,
        "pages": pages,
        "segments": segments,
        "stats": {
            "pages": page_count,
            "pairedParagraphs": len(segments),
            "englishSentences": sum(len(row["enSentences"]) for row in segments),
            "chineseSentences": sum(len(row["zhSentences"]) for row in segments),
            "explicitSentencePairs": sum(len(row["sentencePairs"]) for row in segments),
        },
    }
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--original", required=True)
    parser.add_argument("--translated", required=True)
    parser.add_argument("--compare", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    payload = generate_map(args.original, args.translated, args.compare, args.output)
    print(json.dumps(payload["stats"], ensure_ascii=False))


if __name__ == "__main__":
    main()
