#!/usr/bin/env python3
"""Repair genuinely overlapping PDF caption lines without changing the source PDF.

The detector is intentionally conservative: a text block is eligible only when
its first line starts with Figure/Table/图/表 plus a number and that line's bbox
has a positive-area intersection with the following line's bbox.  Once such a
block is found, immediately adjacent continuation blocks in the same column are
included so the whole caption can be reflowed as one paragraph.
"""

from __future__ import annotations

import argparse
import html
import json
import os
from pathlib import Path
import re
import shutil
import statistics
import tempfile
from typing import Any, Iterable

import pymupdf


NUMBER_TOKEN = r"(?:[A-Z]?\d+(?:[.\-]\d+)*|[IVXLCDM]+|[一二三四五六七八九十百]+)"
CAPTION_RE = re.compile(
    rf"^\s*(?P<label>(?:(?:Figure|Table)\s*{NUMBER_TOKEN}|(?:图|表)\s*{NUMBER_TOKEN}))"
    r"(?=$|[\s.:：、，,（(])",
    re.IGNORECASE,
)
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]+")
SPACE_RE = re.compile(r"\s+")
MIN_INTERSECTION_AREA = 0.01
MIN_LAYOUT_SCALE = 0.80
CAPTION_LINE_HEIGHT = 1.52


def _rect(value: Iterable[float]) -> pymupdf.Rect:
    return pymupdf.Rect(tuple(float(v) for v in value))


def _rect_json(rect: pymupdf.Rect) -> list[float]:
    return [round(value, 3) for value in (rect.x0, rect.y0, rect.x1, rect.y1)]


def _intersection_area(first: pymupdf.Rect, second: pymupdf.Rect) -> float:
    intersection = first & second
    if intersection.is_empty or intersection.is_infinite:
        return 0.0
    return max(0.0, intersection.width) * max(0.0, intersection.height)


def _union_rect(rects: Iterable[pymupdf.Rect]) -> pymupdf.Rect:
    iterator = iter(rects)
    result = pymupdf.Rect(next(iterator))
    for rect in iterator:
        result |= rect
    return result


def _clean_text(value: str) -> str:
    value = CONTROL_RE.sub(" ", value.replace("\u00ad", ""))
    return SPACE_RE.sub(" ", value).strip()


def _line_text(line: dict[str, Any]) -> str:
    return _clean_text("".join(span.get("text", "") for span in line.get("spans", [])))


def _median_font_size(lines: Iterable[dict[str, Any]], default: float = 9.0) -> float:
    sizes = [
        float(span["size"])
        for line in lines
        for span in line.get("spans", [])
        if span.get("text", "").strip() and float(span.get("size", 0)) > 0
    ]
    return statistics.median(sizes) if sizes else default


def _text_blocks(page: pymupdf.Page) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for index, raw_block in enumerate(page.get_text("dict").get("blocks", [])):
        if raw_block.get("type") != 0:
            continue
        lines: list[dict[str, Any]] = []
        for raw_line in raw_block.get("lines", []):
            text = _line_text(raw_line)
            if not text:
                continue
            lines.append(
                {
                    "text": text,
                    "rect": _rect(raw_line["bbox"]),
                    "spans": raw_line.get("spans", []),
                }
            )
        if not lines:
            continue
        rect = _union_rect(line["rect"] for line in lines)
        blocks.append(
            {
                "index": index,
                "rect": rect,
                "lines": lines,
                "font_size": _median_font_size(lines),
            }
        )
    return blocks


def _horizontal_overlap_ratio(first: pymupdf.Rect, second: pymupdf.Rect) -> float:
    overlap = max(0.0, min(first.x1, second.x1) - max(first.x0, second.x0))
    denominator = min(first.width, second.width)
    return overlap / denominator if denominator > 0 else 0.0


def _same_column(anchor: pymupdf.Rect, other: pymupdf.Rect, font_size: float) -> bool:
    if _horizontal_overlap_ratio(anchor, other) < 0.65:
        return False
    return abs(anchor.x0 - other.x0) <= max(18.0, font_size * 2.25)


def _is_cjk(character: str) -> bool:
    codepoint = ord(character)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
    )


def _smart_join(lines: Iterable[str]) -> str:
    result = ""
    for raw in lines:
        piece = _clean_text(raw)
        if not piece:
            continue
        if not result:
            result = piece
            continue
        if result.endswith(("-", "‐")) and piece[:1].isalnum():
            result = result[:-1] + piece
            continue
        left = result[-1]
        right = piece[0]
        if _is_cjk(left) and _is_cjk(right):
            separator = ""
        elif left in "，。！？；：、）】》」』" and (_is_cjk(right) or right in "（("):
            separator = ""
        else:
            separator = " "
        result += separator + piece
    return result


def _find_triggers(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    triggers: list[dict[str, Any]] = []
    for block in blocks:
        lines = block["lines"]
        if len(lines) < 2:
            continue
        title_line, body_line = lines[0], lines[1]
        match = CAPTION_RE.match(title_line["text"])
        if not match:
            continue
        area = _intersection_area(title_line["rect"], body_line["rect"])
        if area <= MIN_INTERSECTION_AREA:
            continue
        intersection = title_line["rect"] & body_line["rect"]
        triggers.append(
            {
                "block": block,
                "match": match,
                "intersection": intersection,
                "intersection_area": area,
            }
        )
    return triggers


def _collect_continuations(
    trigger: dict[str, Any], blocks: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    anchor = trigger["block"]
    collected = [anchor]
    current_bottom = anchor["rect"].y1
    body_size = _median_font_size(anchor["lines"][1:], anchor["font_size"])
    line_heights = [line["rect"].height for line in anchor["lines"] if line["rect"].height > 0]
    typical_line_height = statistics.median(line_heights) if line_heights else body_size * 1.35
    max_gap = max(6.0, typical_line_height * 1.35)

    candidates = sorted(
        (
            block
            for block in blocks
            if block["index"] != anchor["index"]
            and _same_column(anchor["rect"], block["rect"], body_size)
            and block["rect"].y1 > anchor["rect"].y0
        ),
        key=lambda block: (block["rect"].y0, block["rect"].x0),
    )
    for block in candidates:
        if block["rect"].y0 < current_bottom - 1.0:
            continue
        gap = block["rect"].y0 - current_bottom
        if gap > max_gap:
            break
        if CAPTION_RE.match(block["lines"][0]["text"]):
            break
        size_ratio = block["font_size"] / body_size if body_size else 1.0
        if not 0.72 <= size_ratio <= 1.35:
            break
        collected.append(block)
        current_bottom = max(current_bottom, block["rect"].y1)
    return collected


def _next_same_column_top(
    anchor: dict[str, Any],
    blocks: list[dict[str, Any]],
    excluded_indexes: set[int],
    after_y: float,
) -> float | None:
    candidates = [
        block["rect"].y0
        for block in blocks
        if block["index"] not in excluded_indexes
        and block["rect"].y0 >= after_y - 0.1
        and _same_column(anchor["rect"], block["rect"], anchor["font_size"])
    ]
    return min(candidates) if candidates else None


def _build_layout(
    page: pymupdf.Page,
    trigger: dict[str, Any],
    blocks: list[dict[str, Any]],
) -> dict[str, Any]:
    source_blocks = _collect_continuations(trigger, blocks)
    source_rect = _union_rect(block["rect"] for block in source_blocks)
    excluded = {block["index"] for block in source_blocks}
    body_size = _median_font_size(
        [line for block in source_blocks for line in block["lines"]][1:],
        trigger["block"]["font_size"],
    )
    next_top = _next_same_column_top(
        trigger["block"], blocks, excluded, source_rect.y1
    )
    page_bottom = page.rect.y1 - max(4.0, body_size * 0.5)
    safe_bottom = page_bottom
    if next_top is not None:
        safe_bottom = min(safe_bottom, next_top - max(1.5, body_size * 0.22))
    safe_rect = pymupdf.Rect(
        max(page.rect.x0 + 2.0, source_rect.x0 - 0.5),
        max(page.rect.y0 + 2.0, source_rect.y0 - 0.5),
        min(page.rect.x1 - 2.0, source_rect.x1 + 0.5),
        safe_bottom,
    )

    all_lines = [line for block in source_blocks for line in block["lines"]]
    title_text = all_lines[0]["text"]
    body_text = _smart_join(line["text"] for line in all_lines[1:])
    title_size = _median_font_size([all_lines[0]], body_size)
    html_text = (
        '<div class="caption"><span class="caption-title">'
        + html.escape(title_text)
        + "</span>"
        + (" " + html.escape(body_text) if body_text else "")
        + "</div>"
    )
    css = (
        ".caption { margin: 0; padding: 0; font-family: sans-serif; "
        f"font-size: {body_size:.3f}pt; line-height: {CAPTION_LINE_HEIGHT:.2f}; color: #000; "
        "text-align: left; } "
        ".caption-title { "
        f"font-size: {title_size:.3f}pt; font-weight: bold; }}"
    )
    return {
        "source_blocks": source_blocks,
        "source_rect": source_rect,
        "safe_rect": safe_rect,
        "title_text": title_text,
        "body_text": body_text,
        "html": html_text,
        "css": css,
        "body_size": body_size,
    }


def _redaction_conflict(
    layout: dict[str, Any], blocks: list[dict[str, Any]]
) -> dict[str, Any] | None:
    target_indexes = {block["index"] for block in layout["source_blocks"]}
    for source in layout["source_blocks"]:
        redact_rect = pymupdf.Rect(source["rect"])
        redact_rect.x0 -= 0.25
        redact_rect.y0 -= 0.25
        redact_rect.x1 += 0.25
        redact_rect.y1 += 0.25
        for other in blocks:
            if other["index"] in target_indexes:
                continue
            area = _intersection_area(redact_rect, other["rect"])
            if area > MIN_INTERSECTION_AREA:
                return {"block": other["index"], "intersection_area": area}
    return None


def _preflight_layout(
    page_rect: pymupdf.Rect, layout: dict[str, Any]
) -> tuple[float, float]:
    safe_rect = layout["safe_rect"]
    if safe_rect.width <= 1 or safe_rect.height <= 1:
        return -1.0, 0.0
    probe = pymupdf.open()
    try:
        probe_page = probe.new_page(width=page_rect.width, height=page_rect.height)
        spare, scale = probe_page.insert_htmlbox(
            safe_rect,
            layout["html"],
            css=layout["css"],
            scale_low=MIN_LAYOUT_SCALE,
        )
        return float(spare), float(scale)
    finally:
        probe.close()


def _regions_overlap(first: pymupdf.Rect, second: pymupdf.Rect) -> bool:
    return _intersection_area(first, second) > MIN_INTERSECTION_AREA


def _plan_page(
    page: pymupdf.Page, page_number: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    blocks = _text_blocks(page)
    plans: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    occupied: list[pymupdf.Rect] = []
    for trigger in _find_triggers(blocks):
        block = trigger["block"]
        base = {
            "page": page_number,
            "block": block["index"],
            "title": block["lines"][0]["text"],
            "title_bbox": _rect_json(block["lines"][0]["rect"]),
            "body_bbox": _rect_json(block["lines"][1]["rect"]),
            "intersection_bbox": _rect_json(trigger["intersection"]),
            "intersection_area": round(trigger["intersection_area"], 3),
        }
        layout = _build_layout(page, trigger, blocks)
        conflict = _redaction_conflict(layout, blocks)
        if conflict:
            skipped.append(
                {
                    **base,
                    "status": "skipped",
                    "reason": "redaction_conflicts_with_other_text",
                    "conflict": conflict,
                }
            )
            continue
        if any(_regions_overlap(layout["safe_rect"], rect) for rect in occupied):
            skipped.append({**base, "status": "skipped", "reason": "overlapping_repair_regions"})
            continue
        spare, scale = _preflight_layout(page.rect, layout)
        if spare < 0 or scale < MIN_LAYOUT_SCALE:
            skipped.append(
                {
                    **base,
                    "status": "skipped",
                    "reason": "insufficient_safe_layout_space",
                    "safe_bbox": _rect_json(layout["safe_rect"]),
                    "layout_scale": round(scale, 4),
                }
            )
            continue
        details = {
            **base,
            "status": "planned",
            "source_blocks": [item["index"] for item in layout["source_blocks"]],
            "source_bbox": _rect_json(layout["source_rect"]),
            "safe_bbox": _rect_json(layout["safe_rect"]),
            "layout_scale": round(scale, 4),
            "layout_spare_height": round(spare, 3),
            "merged_text": _smart_join([layout["title_text"], layout["body_text"]]),
        }
        plans.append({"layout": layout, "details": details})
        occupied.append(layout["safe_rect"])
    return plans, skipped


def _apply_page_repairs(page: pymupdf.Page, plans: list[dict[str, Any]]) -> None:
    for plan in plans:
        for block in plan["layout"]["source_blocks"]:
            redact_rect = pymupdf.Rect(block["rect"])
            redact_rect.x0 -= 0.25
            redact_rect.y0 -= 0.25
            redact_rect.x1 += 0.25
            redact_rect.y1 += 0.25
            page.add_redact_annot(redact_rect, fill=None, cross_out=False)
    if plans:
        page.apply_redactions(
            images=pymupdf.PDF_REDACT_IMAGE_NONE,
            graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
            text=pymupdf.PDF_REDACT_TEXT_REMOVE,
        )
    for plan in plans:
        layout = plan["layout"]
        spare, scale = page.insert_htmlbox(
            layout["safe_rect"],
            layout["html"],
            css=layout["css"],
            scale_low=MIN_LAYOUT_SCALE,
        )
        if spare < 0 or scale < MIN_LAYOUT_SCALE:
            raise RuntimeError("layout unexpectedly failed after successful preflight")
        plan["details"]["layout_scale"] = round(float(scale), 4)
        plan["details"]["layout_spare_height"] = round(float(spare), 3)
        plan["details"]["status"] = "repaired"


def _parse_pages(values: list[int] | None, page_count: int) -> set[int]:
    if not values:
        return set(range(page_count))
    invalid = sorted({value for value in values if value < 1 or value > page_count})
    if invalid:
        raise ValueError(f"page number out of range: {invalid}; PDF has {page_count} pages")
    return {value - 1 for value in values}


def _save_document(doc: pymupdf.Document, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.stem}.", suffix=".pdf", dir=output_path.parent
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    temporary_path.unlink()
    try:
        doc.save(temporary_path, garbage=4, deflate=True)
        os.replace(temporary_path, output_path)
    finally:
        temporary_path.unlink(missing_ok=True)


def repair_pdf(
    input_path: Path,
    output_path: Path | None,
    *,
    dry_run: bool,
    selected_pages: list[int] | None,
    overwrite: bool,
) -> dict[str, Any]:
    input_path = input_path.expanduser().resolve()
    if not input_path.is_file():
        raise FileNotFoundError(f"input PDF does not exist: {input_path}")
    if input_path.suffix.lower() != ".pdf":
        raise ValueError(f"input is not a PDF: {input_path}")
    if not dry_run and output_path is None:
        raise ValueError("output PDF is required unless --dry-run is used")
    resolved_output = output_path.expanduser().resolve() if output_path else None
    if resolved_output == input_path:
        raise ValueError("output must differ from input; the source PDF is never overwritten")
    if resolved_output and resolved_output.exists() and not overwrite and not dry_run:
        raise FileExistsError(f"output already exists (use --overwrite): {resolved_output}")

    document = pymupdf.open(input_path)
    try:
        if document.needs_pass:
            raise ValueError("encrypted PDF requires a password and was not modified")
        page_indexes = _parse_pages(selected_pages, document.page_count)
        summary: dict[str, Any] = {
            "input": str(input_path),
            "output": str(resolved_output) if resolved_output else None,
            "dry_run": dry_run,
            "pages_total": document.page_count,
            "pages_scanned": [index + 1 for index in sorted(page_indexes)],
            "detections": [],
            "skipped": [],
        }
        page_plans: dict[int, list[dict[str, Any]]] = {}
        for page_index in sorted(page_indexes):
            plans, skipped = _plan_page(document[page_index], page_index + 1)
            page_plans[page_index] = plans
            summary["detections"].extend(plan["details"] for plan in plans)
            summary["skipped"].extend(skipped)

        summary["detected_count"] = len(summary["detections"]) + len(summary["skipped"])
        summary["repairable_count"] = len(summary["detections"])
        if dry_run:
            for detection in summary["detections"]:
                detection["status"] = "would_repair"
            summary["repaired_count"] = 0
            summary["output_written"] = False
            return summary

        repair_count = sum(len(plans) for plans in page_plans.values())
        if repair_count:
            for page_index, plans in page_plans.items():
                _apply_page_repairs(document[page_index], plans)
            _save_document(document, resolved_output)
        else:
            resolved_output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(input_path, resolved_output)
        summary["repaired_count"] = repair_count
        summary["output_written"] = True
    finally:
        document.close()

    check_document = pymupdf.open(resolved_output)
    try:
        remaining = 0
        for page_index in page_indexes:
            remaining += len(_find_triggers(_text_blocks(check_document[page_index])))
        summary["postcheck_overlapping_caption_count"] = remaining
        summary["postcheck_pages_total"] = check_document.page_count
    finally:
        check_document.close()
    return summary


def _write_summary(summary: dict[str, Any], path: Path | None) -> None:
    payload = json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    print(payload, end="")
    if path:
        path = path.expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Conservatively detect and repair geometrically overlapping PDF caption lines."
    )
    parser.add_argument("input", type=Path, help="source PDF (never modified)")
    parser.add_argument("output", type=Path, nargs="?", help="new repaired PDF")
    parser.add_argument(
        "--page",
        action="append",
        type=int,
        help="1-based page to scan; repeat for multiple pages (default: all)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="detect and plan only; do not write a PDF"
    )
    parser.add_argument("--summary", type=Path, help="also write the JSON summary to this path")
    parser.add_argument("--overwrite", action="store_true", help="replace an existing output PDF")
    args = parser.parse_args()

    try:
        summary = repair_pdf(
            args.input,
            args.output,
            dry_run=args.dry_run,
            selected_pages=args.page,
            overwrite=args.overwrite,
        )
    except Exception as error:
        parser.exit(2, f"repair_caption_overlap.py: {error}\n")
    _write_summary(summary, args.summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
