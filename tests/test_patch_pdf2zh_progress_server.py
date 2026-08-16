from __future__ import annotations

import hashlib
import importlib
import importlib.util
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PATCH_SCRIPT = ROOT / "scripts" / "patch_pdf2zh_progress_server.py"
SERVER_PAYLOAD = ROOT / "windows" / "payload" / "zotero-pdf2zh-server-4.0.3.zip"


def load_patch_module():
    spec = importlib.util.spec_from_file_location("blr_progress_patcher", PATCH_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {PATCH_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


PATCHER = load_patch_module()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ProgressServerPatchTests(unittest.TestCase):
    def extract_official_server(self, destination: Path) -> Path:
        with zipfile.ZipFile(SERVER_PAYLOAD) as archive:
            archive.extractall(destination)
        return destination / "server"

    def import_patched_modules(self, server_root: Path):
        for name in ("utils.execute", "utils.task_manager", "utils"):
            sys.modules.pop(name, None)
        sys.path.insert(0, str(server_root))
        try:
            task_module = importlib.import_module("utils.task_manager")
            execute_module = importlib.import_module("utils.execute")
        finally:
            sys.path.remove(str(server_root))
        return task_module, execute_module

    def test_official_payload_patch_is_idempotent_and_enforces_contract(self):
        with tempfile.TemporaryDirectory(prefix="blr-progress-test-") as temporary:
            server_root = self.extract_official_server(Path(temporary))
            execute_path = server_root / "utils" / "execute.py"
            task_path = server_root / "utils" / "task_manager.py"

            before = (digest(execute_path), digest(task_path))
            check = PATCHER.patch_server(server_root.parent, check=True)
            self.assertEqual(check["files"], {
                "execute.py": "would-patch",
                "task_manager.py": "would-patch",
            })
            self.assertEqual(before, (digest(execute_path), digest(task_path)))

            first = PATCHER.patch_server(server_root.parent)
            second = PATCHER.patch_server(server_root)
            self.assertEqual(first["files"], {
                "execute.py": "patch",
                "task_manager.py": "patch",
            })
            self.assertEqual(second["files"], {
                "execute.py": "already-patched",
                "task_manager.py": "already-patched",
            })
            compile(execute_path.read_text(encoding="utf-8"), str(execute_path), "exec")
            compile(task_path.read_text(encoding="utf-8"), str(task_path), "exec")

            task_module, execute_module = self.import_patched_modules(server_root)
            manager = task_module.TaskManager()
            manager.add_task("preclamped", {
                "taskId": "preclamped",
                "active": True,
                "progress": 100,
            })
            self.assertEqual(manager.active_tasks["preclamped"]["progress"], 99)
            manager.add_task("fixture", {
                "taskId": "fixture",
                "active": True,
                "progress": 7,
                "status": "running",
                "message": "starting",
            })
            execute_module.task_manager = manager

            # Completed nested stages expose local phase progress only.
            execute_module._parse_progress("Parse Page Layout 1/1", "fixture")
            task = manager.active_tasks["fixture"]
            self.assertEqual(task["progress"], 7)
            self.assertEqual(task["phaseProgress"], 100)
            self.assertEqual(task["message"], "Parse Page Layout")

            execute_module._parse_progress(
                "Translate Paragraphs (1/1) ........ 1/1", "fixture"
            )
            self.assertEqual(task["progress"], 7)
            self.assertEqual(task["phaseProgress"], 100)

            # Only the strict master translate x/100 line owns global progress.
            execute_module._parse_progress("translate ━━━━━ 17/100", "fixture")
            self.assertEqual(task["progress"], 17)
            execute_module._parse_progress(
                "translate ----- 1096/1096 ----- 85/100Generate drawing instructions",
                "fixture",
            )
            self.assertEqual(task["progress"], 85)
            execute_module._parse_progress(
                "translate ----- 0/10032/32 -----", "fixture"
            )
            self.assertEqual(task["progress"], 85)
            execute_module._parse_progress(
                "DetectScannedFile (1/1) ........ 1/1", "fixture"
            )
            self.assertEqual(task["progress"], 85)
            self.assertEqual(task["phaseProgress"], 100)

            execute_module._parse_progress(
                "Automatic Term Extraction | 1/1 [00:02<00:00]", "fixture"
            )
            self.assertEqual(task["progress"], 85)
            self.assertEqual(task["phaseProgress"], 100)
            self.assertEqual(task["message"], "Automatic Term Extraction")

            execute_module._parse_progress("translate ━━━━━ 100/100", "fixture")
            self.assertEqual(task["progress"], 99)
            manager.update_task("fixture", {"progress": 96})
            self.assertEqual(task["progress"], 99)
            manager.update_task("fixture", {"progress": 100})
            self.assertEqual(task["progress"], 99)
            self.assertTrue(task["active"])

            manager.complete_task("fixture", "success")
            self.assertFalse(task["active"])
            self.assertEqual(task["progress"], 100)

    def test_unexpected_source_is_rejected_before_any_write(self):
        with tempfile.TemporaryDirectory(prefix="blr-progress-reject-") as temporary:
            server_root = self.extract_official_server(Path(temporary))
            execute_path = server_root / "utils" / "execute.py"
            task_path = server_root / "utils" / "task_manager.py"
            execute_path.write_text(
                execute_path.read_text(encoding="utf-8") + "\n# unexpected local edit\n",
                encoding="utf-8",
            )
            before = (execute_path.read_bytes(), task_path.read_bytes())

            with self.assertRaises(PATCHER.PatchError):
                PATCHER.patch_server(server_root)

            self.assertEqual(before, (execute_path.read_bytes(), task_path.read_bytes()))

    @unittest.skipUnless(
        Path("/mnt/e/zotero-pdf2zh/server/utils/execute.py").is_file(),
        "locally exercised Windows pipe variant is not available",
    )
    def test_current_windows_pipe_variant_is_supported(self):
        source = Path("/mnt/e/zotero-pdf2zh/server")
        with tempfile.TemporaryDirectory(prefix="blr-progress-windows-") as temporary:
            server_root = Path(temporary) / "server"
            (server_root / "utils").mkdir(parents=True)
            shutil.copy2(source / "server.py", server_root / "server.py")
            for name in ("execute.py", "task_manager.py"):
                shutil.copy2(source / "utils" / name, server_root / "utils" / name)

            first = PATCHER.patch_server(server_root)
            second = PATCHER.patch_server(server_root)
            self.assertTrue(
                set(first["files"].values()).issubset({"patch", "already-patched"})
            )
            self.assertEqual(set(second["files"].values()), {"already-patched"})
            task_module, _ = self.import_patched_modules(server_root)
            manager = task_module.TaskManager()
            claimed, _ = manager.claim_task("claimed", {
                "taskId": "claimed",
                "active": True,
                "progress": 100,
                "fileName": "fixture.pdf",
            })
            self.assertTrue(claimed)
            self.assertEqual(manager.active_tasks["claimed"]["progress"], 99)


if __name__ == "__main__":
    unittest.main()
