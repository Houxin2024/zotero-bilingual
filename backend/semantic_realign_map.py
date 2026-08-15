#!/usr/bin/env python3
"""Re-align bilingual PDF sentence geometry by column, order, and semantics."""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path

import numpy as np
from fastembed import TextEmbedding


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def union_box(rects: list[list[float]]) -> list[float] | None:
    if not rects:
        return None
    return [
        min(rect[0] for rect in rects),
        min(rect[1] for rect in rects),
        max(rect[2] for rect in rects),
        max(rect[3] for rect in rects),
    ]


def unit_rects(unit: dict) -> list[list[float]]:
    return unit.get("rects") or ([unit["box"]] if unit.get("box") else [])


def merge_fragments(units: list[dict], language: str) -> list[dict]:
    terminal = re.compile(r"[。！？!?]\s*$") if language == "zh" else re.compile(r"[.!?][\d,;:()\[\]–—\s]*$")
    merged, current = [], None
    for unit in units:
        text, rects = clean(unit.get("text", "")), unit_rects(unit)
        if not text or not rects:
            continue
        if current is None:
            current = {"text": text, "rects": list(rects)}
        else:
            current["text"] = clean(current["text"] + " " + text)
            current["rects"].extend(rects)
        if terminal.search(current["text"]):
            current["box"] = union_box(current["rects"])
            merged.append(current)
            current = None
    if current is not None:
        current["box"] = union_box(current["rects"])
        merged.append(current)
    return merged


def column_for_box(box: list[float], page: dict, english: bool) -> int:
    offset = page["rightOffset"] if english else 0.0
    original_width = page["width"] - page["rightOffset"]
    if (box[2] - box[0]) > original_width * 0.68:
        return 2
    center = (box[0] + box[2]) / 2 - offset
    return 0 if center < original_width / 2 else 1


def ordered_units(segments: list[dict], page: dict, english: bool, column: int) -> list[dict]:
    field = "enSentences" if english else "zhSentences"
    box_field = "enBox" if english else "zhBox"
    units: list[dict] = []
    seen: set[tuple] = set()
    for segment_index, segment in enumerate(segments):
        box = segment.get(box_field)
        if not box or column_for_box(box, page, english) != column:
            continue
        for unit_index, unit in enumerate(segment.get(field, [])):
            text = clean(unit.get("text", ""))
            rects = unit_rects(unit)
            if not text or not rects:
                continue
            key = (text, tuple(round(value, 2) for value in (unit.get("box") or union_box(rects))))
            if key in seen:
                continue
            seen.add(key)
            units.append({
                "text": text,
                "box": unit.get("box") or union_box(rects),
                "rects": rects,
                "sourceOrder": (segment_index, unit_index),
            })
    # PDF coordinates use a bottom-left origin. Larger y is earlier on the page.
    units.sort(key=lambda unit: (-unit["box"][3], unit["box"][0], -unit["box"][1]))
    return units


def lexical_length(text: str, language: str) -> int:
    pattern = r"[A-Za-z]" if language == "en" else r"[\u3400-\u9fff]"
    return max(1, len(re.findall(pattern, text)))


def shared_anchor_score(en_text: str, zh_text: str) -> float:
    pattern = re.compile(r"(?:https?://\S+|[A-Za-z]{2,}\d*|\d+(?:\.\d+)*)", re.I)
    en_tokens = {token.lower().strip(".,;:()[]{}") for token in pattern.findall(en_text)}
    zh_tokens = {token.lower().strip(".,;:()[]{}") for token in pattern.findall(zh_text)}
    if not en_tokens or not zh_tokens:
        return 0.0
    return len(en_tokens & zh_tokens) / max(1, min(len(en_tokens), len(zh_tokens)))


def normalized_urls(text: str) -> set[str]:
    return {
        url.rstrip(".,;:!?)]}）】。；，")
        for url in re.findall(r"https?://\S+", text, re.I)
    }


def is_heading(unit: dict, language: str) -> bool:
    text = clean(unit["text"])
    if lexical_length(text, language) > 55 or re.search(r"\d", text):
        return False
    terminal = r"[.!?]\s*$" if language == "en" else r"[。！？]\s*$"
    return not re.search(terminal, text)


def group_vector(vectors: np.ndarray, start: int, count: int) -> np.ndarray:
    vector = vectors[start:start + count].mean(axis=0)
    norm = np.linalg.norm(vector)
    return vector / max(float(norm), 1e-12)


def align_units(en_units: list[dict], zh_units: list[dict], en_vectors: np.ndarray, zh_vectors: np.ndarray) -> list[dict]:
    if not en_units or not zh_units:
        return []
    n, m = len(en_units), len(zh_units)
    expected_ratio = sum(lexical_length(unit["text"], "zh") for unit in zh_units) / max(
        1, sum(lexical_length(unit["text"], "en") for unit in en_units)
    )
    expected_ratio = min(1.2, max(0.12, expected_ratio))
    infinity = float("inf")
    costs = [[infinity] * (m + 1) for _ in range(n + 1)]
    previous: list[list[tuple[int, int, int, int, float] | None]] = [[None] * (m + 1) for _ in range(n + 1)]
    costs[0][0] = 0.0
    max_group = 3
    for i in range(n + 1):
        for j in range(m + 1):
            if not math.isfinite(costs[i][j]):
                continue
            if i < n and costs[i][j] + 0.85 < costs[i + 1][j]:
                costs[i + 1][j] = costs[i][j] + 0.85
                previous[i + 1][j] = (i, j, 1, 0, -1.0)
            if j < m and costs[i][j] + 0.85 < costs[i][j + 1]:
                costs[i][j + 1] = costs[i][j] + 0.85
                previous[i][j + 1] = (i, j, 0, 1, -1.0)
            for en_count in range(1, min(max_group, n - i) + 1):
                en_text = clean(" ".join(unit["text"] for unit in en_units[i:i + en_count]))
                en_vector = group_vector(en_vectors, i, en_count)
                en_length = lexical_length(en_text, "en")
                for zh_count in range(1, min(max_group, m - j) + 1):
                    # A 2-to-2 or 3-to-3 transition hides several otherwise
                    # valid sentence links inside one large highlight.  Real
                    # translation divergence only needs 1-to-many or
                    # many-to-1 transitions, so keep equal-count neighbours
                    # independently selectable.
                    if en_count > 1 and zh_count > 1:
                        continue
                    zh_text = clean(" ".join(unit["text"] for unit in zh_units[j:j + zh_count]))
                    zh_vector = group_vector(zh_vectors, j, zh_count)
                    similarity = float(np.dot(en_vector, zh_vector))
                    ratio = lexical_length(zh_text, "zh") / max(1, en_length)
                    ratio_cost = abs(math.log(max(ratio, 1e-4) / expected_ratio))
                    group_cost = 0.11 * (en_count + zh_count - 2) + 0.05 * abs(en_count - zh_count)
                    position_cost = 0.18 * abs((i + en_count / 2) / n - (j + zh_count / 2) / m)
                    anchor_reward = 0.42 * shared_anchor_score(en_text, zh_text)
                    en_urls = normalized_urls(en_text)
                    zh_urls = normalized_urls(zh_text)
                    url_penalty = 0.0
                    if en_urls:
                        if en_urls & zh_urls:
                            anchor_reward += 1.5
                        else:
                            url_penalty += 1.0
                    elif zh_urls:
                        url_penalty += 0.55
                    en_has_heading = any(is_heading(unit, "en") for unit in en_units[i:i + en_count])
                    heading_penalty = 0.0
                    if en_has_heading and en_count > 1:
                        heading_penalty += 1.6
                    if en_has_heading and zh_count > 2:
                        heading_penalty += 0.7 * (zh_count - 2)
                    transition_cost = (
                        2.35 * (1.0 - similarity)
                        + 0.22 * ratio_cost
                        + group_cost
                        + position_cost
                        + heading_penalty
                        + url_penalty
                        - anchor_reward
                    )
                    ni, nj = i + en_count, j + zh_count
                    new_cost = costs[i][j] + transition_cost
                    if new_cost < costs[ni][nj]:
                        costs[ni][nj] = new_cost
                        previous[ni][nj] = (i, j, en_count, zh_count, similarity)
    if previous[n][m] is None:
        return []
    steps = []
    i, j = n, m
    while i or j:
        step = previous[i][j]
        if step is None:
            return []
        pi, pj, en_count, zh_count, similarity = step
        steps.append((pi, i, pj, j, similarity))
        i, j = pi, pj
    steps.reverse()

    pairs = []
    for pair_index, (en_start, en_end, zh_start, zh_end, similarity) in enumerate(steps):
        if en_start == en_end or zh_start == zh_end:
            continue
        en_group = en_units[en_start:en_end]
        zh_group = zh_units[zh_start:zh_end]
        en_rects = [rect for unit in en_group for rect in unit["rects"]]
        zh_rects = [rect for unit in zh_group for rect in unit["rects"]]
        pairs.append({
            "pairIndex": pair_index,
            "enText": clean(" ".join(unit["text"] for unit in en_group)),
            "zhText": clean(" ".join(unit["text"] for unit in zh_group)),
            "enRects": en_rects,
            "zhRects": zh_rects,
            "enBox": union_box(en_rects),
            "zhBox": union_box(zh_rects),
            "semanticScore": round(similarity, 4),
        })
    return pairs


def realign(base_map: Path, output: Path, model_name: str, cache_dir: Path) -> dict:
    payload = json.loads(base_map.read_text(encoding="utf-8"))
    model = TextEmbedding(model_name, cache_dir=str(cache_dir))
    new_segments = []
    semantic_scores = []
    if payload.get("layout", {}).get("source") == "final-dual":
        prepared, all_texts = [], []
        for segment in payload.get("segments", []):
            en_units = merge_fragments(segment.get("enSentences", []), "en")
            zh_units = merge_fragments(segment.get("zhSentences", []), "zh")
            all_texts.extend(unit["text"] for unit in en_units)
            all_texts.extend(unit["text"] for unit in zh_units)
            prepared.append((segment, en_units, zh_units))
        all_vectors = np.asarray(list(model.embed(all_texts)), dtype=np.float32)
        all_vectors /= np.maximum(np.linalg.norm(all_vectors, axis=1, keepdims=True), 1e-12)
        vector_index = 0
        for segment, en_units, zh_units in prepared:
            en_vectors = all_vectors[vector_index:vector_index + len(en_units)]
            vector_index += len(en_units)
            zh_vectors = all_vectors[vector_index:vector_index + len(zh_units)]
            vector_index += len(zh_units)
            pairs = align_units(en_units, zh_units, en_vectors, zh_vectors)
            semantic_scores.extend(pair["semanticScore"] for pair in pairs)
            updated = dict(segment)
            updated["sentencePairs"] = pairs
            new_segments.append(updated)
        payload["version"] = 4
        payload["alignment"] = {"method": "final-dual-orientation-aware-paragraph-and-semantic-sentence-dp", "model": model_name, "baseMap": base_map.name}
        payload["segments"] = new_segments
        payload["stats"] = {
            **payload.get("stats", {}), "semanticParagraphs": len(new_segments),
            "semanticSentencePairs": sum(len(segment["sentencePairs"]) for segment in new_segments),
            "semanticScoreMean": round(float(np.mean(semantic_scores)), 4) if semantic_scores else None,
            "semanticScoreMedian": round(float(np.median(semantic_scores)), 4) if semantic_scores else None,
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return payload["stats"]
    for page in payload["pages"]:
        page_index = int(page["pageIndex"])
        page_segments = [segment for segment in payload["segments"] if int(segment["pageIndex"]) == page_index]
        for column in (0, 1, 2):
            en_units = ordered_units(page_segments, page, True, column)
            zh_units = ordered_units(page_segments, page, False, column)
            if not en_units or not zh_units:
                continue
            texts = [unit["text"] for unit in en_units + zh_units]
            vectors = np.asarray(list(model.embed(texts)), dtype=np.float32)
            vectors /= np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12)
            en_vectors = vectors[:len(en_units)]
            zh_vectors = vectors[len(en_units):]
            pairs = align_units(en_units, zh_units, en_vectors, zh_vectors)
            semantic_scores.extend(pair["semanticScore"] for pair in pairs)
            en_rects = [rect for unit in en_units for rect in unit["rects"]]
            zh_rects = [rect for unit in zh_units for rect in unit["rects"]]
            new_segments.append({
                "id": f"p{page_index + 1}-c{column}",
                "pageIndex": page_index,
                "column": column,
                "en": clean(" ".join(unit["text"] for unit in en_units)),
                "zh": clean(" ".join(unit["text"] for unit in zh_units)),
                "enBox": union_box(en_rects),
                "zhBox": union_box(zh_rects),
                "enSentences": en_units,
                "zhSentences": zh_units,
                "sentencePairs": pairs,
            })
    payload["version"] = 3
    payload["alignment"] = {
        "method": "column-monotonic-multilingual-semantic-dp",
        "model": model_name,
        "baseMap": base_map.name,
    }
    payload["segments"] = new_segments
    payload["stats"] = {
        **payload.get("stats", {}),
        "semanticColumns": len(new_segments),
        "semanticSentencePairs": sum(len(segment["sentencePairs"]) for segment in new_segments),
        "semanticScoreMean": round(float(np.mean(semantic_scores)), 4) if semantic_scores else None,
        "semanticScoreMedian": round(float(np.median(semantic_scores)), 4) if semantic_scores else None,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload["stats"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-map", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument(
        "--model",
        default="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    )
    args = parser.parse_args()
    print(json.dumps(realign(args.base_map, args.output, args.model, args.cache_dir), ensure_ascii=False))


if __name__ == "__main__":
    main()
