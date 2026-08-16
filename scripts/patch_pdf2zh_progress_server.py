#!/usr/bin/env python3
"""Apply the Bilingual Reader progress contract to a PDF2zh server tree.

The upstream PDF2zh progress stream contains several nested progress bars.  A
completed nested bar (for example ``Parse Page Layout 1/1``) must not be
reported as completion of the whole translation.  This patch gives ownership
of the global percentage to the top-level ``translate x/100`` line, exposes
nested progress separately as ``phaseProgress``, and makes TaskManager reject
100% while a task is still active.

The patch is intentionally fail-closed.  Only the two PDF2zh 4.0.3 source
variants shipped/tested by this project, plus the exact outputs produced by
this script, are accepted.  Both target files are validated and compiled
before either one is replaced.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


PATCH_VERSION = 1
EXECUTE_MARKER = "BLR_PROGRESS_PATCH_V1_EXECUTE"
TASK_MANAGER_MARKER = "BLR_PROGRESS_PATCH_V1_TASK_MANAGER"

# Official 4.0.3 payload and the locally exercised Windows pipe variant.
SUPPORTED_UNPATCHED_HASHES = {
    "execute.py": {
        "984c77b7e6f771e01247328d7eb1a1e8a32a648342744ebeae8b23e6fe9b9fea",
        "eec321a3b0a2d9b747f7d545381bbf6c04c13f4558659586f5539ae98f324a62",
    },
    "task_manager.py": {
        "93c655bdcce8d69009bbf9c576219d627667a1eb23e01596111a51b7a4123bb0",
        "59163dfc00f7395a6b40c681693d49c1b0ad4bf13da22400c24e6a72bedef0fb",
    },
}

# Filled with the exact deterministic outputs of this patch.  Keeping this
# allowlist separate from the marker prevents an unrelated file from opting in
# merely by copying the marker comment.
SUPPORTED_PATCHED_HASHES = {
    "execute.py": {
        "21019358184eab75dfdf80c60bd38ce25ecee34eee058ede36adfc393d9b6f2d",
        "539e40df76c3a0efe1806f2625e0c030f14516dc251a5f3715a35585a4f4f9b3",
    },
    "task_manager.py": {
        "fb435fed3ce3a38adbdd8616cbcec5359c8c87a2152ce01ff6948805b09c1f7d",
        "597fe8ea3504902f2a1895f5549977d07b7146054c39209140a2f30e8d39b294",
    },
}


class PatchError(RuntimeError):
    """The target is not a supported PDF2zh source tree."""


@dataclass(frozen=True)
class FilePlan:
    path: Path
    original: str
    patched: str
    action: str


_OLD_MAIN_PATTERN = '''MAIN_PROGRESS_RE = re.compile(
    r"\\btranslate\\s+[^a-z\\(\\)\\r\\n]*?(\\d+)/(\\d+)\\b",\x20
    re.IGNORECASE
)'''

_NEW_MAIN_PATTERN = f'''# {EXECUTE_MARKER}: only the master translate x/100 line owns global progress.
MAIN_PROGRESS_RE = re.compile(
    r"^[^\\w\\r\\n]*translate\\s+[^a-z()\\r\\n]*?(?<!\\d)(\\d{{1,3}})/(100)(?!\\d)",
    re.IGNORECASE | re.MULTILINE,
)'''

_NEW_PARSE_PROGRESS = '''def _parse_progress(text, task_id):
    """Parse master and nested progress without conflating their percentages."""
    if task_id is None:
        return

    clean = ANSI_ESCAPE.sub("", text)
    updates = {}

    # Only the master line has a denominator of exactly 100 and begins with
    # "translate" (apart from terminal drawing characters).  It is the sole
    # owner of the task-wide percentage.  Completion remains reserved for
    # TaskManager.complete_task().
    master_matches = list(MAIN_PROGRESS_RE.finditer(clean))
    if master_matches:
        current, total = map(int, master_matches[-1].groups())
        if total == 100:
            updates.update({
                "progress": max(0, min(99, current)),
                "status": "running",
                "message": f"translate {current}/{total}",
            })

    # Rich step rows contain both a stage counter and a local progress counter.
    # These fields are useful to the UI but must never overwrite global progress.
    phase_match = None
    step_matches = list(STEP_PROGRESS_RE.finditer(clean))
    if step_matches:
        match = step_matches[-1]
        phase_match = (match.group(1), int(match.group(2)), int(match.group(3)))
    else:
        # Legacy Parse/Running/translate rows are also nested unless they match
        # the strict master expression above.
        for line in clean.replace("\\r", "\\n").splitlines():
            if MAIN_PROGRESS_RE.search(line):
                continue
            legacy_matches = list(LEGACY_PROGRESS_RE.finditer(line))
            if not legacy_matches:
                continue
            match = legacy_matches[-1]
            current, total = int(match.group(1)), int(match.group(2))
            ratio_offset = match.start(1) - match.start(0)
            label = match.group(0)[:ratio_offset].strip(" \\t|:.-")
            phase_match = (label or "PDF processing", current, total)

    if phase_match is not None:
        label, current, total = phase_match
        label = " ".join(str(label).split()).strip(" |:.-") or "PDF processing"
        if total > 0:
            updates.update({
                "message": label,
                "phaseProgress": max(0, min(100, int(current * 100 / total))),
                "phaseCurrent": current,
                "phaseTotal": total,
            })
    elif not master_matches:
        # A bare tqdm row still owns only local phase progress.  Use its line
        # prefix as a best-effort stage label without touching global progress.
        tqdm_match = None
        tqdm_label = ""
        for line in clean.replace("\\r", "\\n").splitlines():
            matches = list(PDF2ZH_TQDM_RE.finditer(line))
            if matches:
                tqdm_match = matches[-1]
                tqdm_label = line[:tqdm_match.start()].strip(" \\t|:.-")
        if tqdm_match is not None:
            current, total = map(int, tqdm_match.groups())
            if total > 0:
                updates.update({
                    "phaseProgress": max(0, min(100, int(current * 100 / total))),
                    "phaseCurrent": current,
                    "phaseTotal": total,
                })
                tqdm_label = " ".join(tqdm_label.split()).strip(" |:.-")
                if tqdm_label:
                    updates["message"] = tqdm_label

    if updates:
        task_manager.update_task(task_id, updates)
'''

_OLD_ADD_TASK = '''    def add_task(self, task_id, info):
        with self.lock:
            self.active_tasks[task_id] = info
            # _debug_progress_log("TASK_ADD", task=self._task_snapshot(task_id, self.active_tasks[task_id]))
'''

_NEW_ADD_TASK = f'''    # {TASK_MANAGER_MARKER}: active tasks may approach, but never claim, completion.
    @staticmethod
    def _normalize_active_progress(task, values):
        normalized = dict(values)
        remains_active = bool(normalized.get("active", task.get("active", True)))
        if remains_active and "progress" in normalized:
            try:
                incoming = max(0, min(99, int(float(normalized["progress"]))))
                previous = max(0, min(99, int(float(task.get("progress", 0)))))
                normalized["progress"] = max(previous, incoming)
            except (TypeError, ValueError, OverflowError):
                normalized.pop("progress", None)
        return normalized

    def add_task(self, task_id, info):
        with self.lock:
            normalized = self._normalize_active_progress({{}}, info)
            self.active_tasks[task_id] = normalized
            # _debug_progress_log("TASK_ADD", task=self._task_snapshot(task_id, normalized))
'''

_OLD_CLAIM_ASSIGNMENT = '''            self.active_tasks[task_id] = info
            return True, None'''

_NEW_CLAIM_ASSIGNMENT = '''            normalized = self._normalize_active_progress({}, info)
            self.active_tasks[task_id] = normalized
            return True, None'''

_OLD_UPDATE_TASK = '''    def update_task(self, task_id, updates):
        with self.lock:
            if task_id in self.active_tasks:
                self.active_tasks[task_id].update(updates)
                # _debug_progress_log(
                #     "TASK_UPDATE",
                #     updates=json.dumps(updates, ensure_ascii=False, sort_keys=True),
                #     task=self._task_snapshot(task_id, self.active_tasks[task_id]),
                # )
'''

_NEW_UPDATE_TASK = '''    def update_task(self, task_id, updates):
        with self.lock:
            if task_id in self.active_tasks:
                task = self.active_tasks[task_id]
                normalized = self._normalize_active_progress(task, updates)
                task.update(normalized)
                # _debug_progress_log(
                #     "TASK_UPDATE",
                #     updates=json.dumps(normalized, ensure_ascii=False, sort_keys=True),
                #     task=self._task_snapshot(task_id, task),
                # )
'''


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise PatchError(f"expected exactly one {label}, found {count}")
    return text.replace(old, new, 1)


def patch_execute_text(text: str) -> str:
    patched = _replace_once(
        text,
        _OLD_MAIN_PATTERN,
        _NEW_MAIN_PATTERN,
        "PDF2zh master progress pattern",
    )
    start = patched.find("def _parse_progress(text, task_id):")
    end_marker = "\n# def _parse_progress(text, task_id):"
    end = patched.find(end_marker, start + 1)
    if start < 0 or end < 0:
        raise PatchError("could not locate the active PDF2zh _parse_progress function")
    patched = patched[:start] + _NEW_PARSE_PROGRESS + patched[end:]
    ast.parse(patched, filename="execute.py")
    return patched


def patch_task_manager_text(text: str) -> str:
    patched = _replace_once(
        text,
        _OLD_ADD_TASK,
        _NEW_ADD_TASK,
        "TaskManager.add_task implementation",
    )
    if _OLD_CLAIM_ASSIGNMENT in patched:
        patched = _replace_once(
            patched,
            _OLD_CLAIM_ASSIGNMENT,
            _NEW_CLAIM_ASSIGNMENT,
            "TaskManager.claim_task assignment",
        )
    patched = _replace_once(
        patched,
        _OLD_UPDATE_TASK,
        _NEW_UPDATE_TASK,
        "TaskManager.update_task implementation",
    )
    ast.parse(patched, filename="task_manager.py")
    return patched


def _resolve_server_root(candidate: Path) -> Path:
    candidate = candidate.expanduser().resolve()
    roots = [candidate, candidate / "server"]
    matches = [
        root
        for root in roots
        if (root / "server.py").is_file()
        and (root / "utils" / "execute.py").is_file()
        and (root / "utils" / "task_manager.py").is_file()
    ]
    if len(matches) != 1:
        raise PatchError(
            "expected one PDF2zh server root containing server.py and utils/{execute,task_manager}.py"
        )
    return matches[0]


def _plan_file(path: Path, kind: str) -> FilePlan:
    original = path.read_text(encoding="utf-8")
    digest = _sha256_text(original)
    if digest in SUPPORTED_PATCHED_HASHES[kind]:
        marker = EXECUTE_MARKER if kind == "execute.py" else TASK_MANAGER_MARKER
        if original.count(marker) != 1:
            raise PatchError(f"{path} has a patched digest but an invalid marker")
        ast.parse(original, filename=str(path))
        return FilePlan(path, original, original, "already-patched")
    if digest not in SUPPORTED_UNPATCHED_HASHES[kind]:
        raise PatchError(f"refusing unexpected {kind} source (sha256={digest})")
    patched = patch_execute_text(original) if kind == "execute.py" else patch_task_manager_text(original)
    expected_digest = _sha256_text(patched)
    if expected_digest not in SUPPORTED_PATCHED_HASHES[kind]:
        raise PatchError(
            f"internal allowlist does not recognize patched {kind} (sha256={expected_digest})"
        )
    return FilePlan(path, original, patched, "patch")


def _atomic_write(path: Path, text: str) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.blr-progress-",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        shutil.copymode(path, temporary)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def patch_server(candidate: Path, *, check: bool = False) -> dict[str, object]:
    root = _resolve_server_root(candidate)
    plans = [
        _plan_file(root / "utils" / "execute.py", "execute.py"),
        _plan_file(root / "utils" / "task_manager.py", "task_manager.py"),
    ]

    # Syntax-check the complete planned pair before touching either source.
    for plan in plans:
        ast.parse(plan.patched, filename=str(plan.path))

    if not check:
        replaced: list[FilePlan] = []
        try:
            for plan in plans:
                if plan.action != "patch":
                    continue
                _atomic_write(plan.path, plan.patched)
                replaced.append(plan)
        except Exception as error:
            rollback_errors = []
            for plan in reversed(replaced):
                try:
                    _atomic_write(plan.path, plan.original)
                except Exception as rollback_error:  # pragma: no cover - catastrophic I/O
                    rollback_errors.append(str(rollback_error))
            detail = f"; rollback errors: {rollback_errors}" if rollback_errors else ""
            raise PatchError(f"patch write failed and was rolled back: {error}{detail}") from error

    return {
        "schemaVersion": 1,
        "patchVersion": PATCH_VERSION,
        "serverRoot": str(root),
        "checkOnly": check,
        "files": {
            plan.path.name: (
                "would-patch" if check and plan.action == "patch" else plan.action
            )
            for plan in plans
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "server_root",
        type=Path,
        help="extracted root containing server.py, or its parent containing server/",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate the source and report the planned action without writing",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = patch_server(args.server_root, check=args.check)
    except (OSError, UnicodeError, PatchError, SyntaxError) as error:
        print(f"progress patch refused: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
