from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "patch_pdf2zh_addon_ui.py"
SPEC = importlib.util.spec_from_file_location("patch_pdf2zh_addon_ui", SCRIPT)
assert SPEC and SPEC.loader
patcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(patcher)


class PatchPdf2zhAddonUITest(unittest.TestCase):
    def setUp(self) -> None:
        self.input_xpi = ROOT / "windows" / "payload" / "zotero-pdf2zh-4.0.3.xpi"

    def test_build_is_deterministic_and_changes_only_javascript_and_version(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            output_a = Path(folder) / "a.xpi"
            output_b = Path(folder) / "b.xpi"
            digest_a = patcher.build_patched_xpi(self.input_xpi, output_a)
            digest_b = patcher.build_patched_xpi(self.input_xpi, output_b)

            self.assertEqual(output_a.read_bytes(), output_b.read_bytes())
            self.assertEqual(digest_a, digest_b)

            with zipfile.ZipFile(self.input_xpi) as original, zipfile.ZipFile(output_a) as patched:
                self.assertEqual(sorted(original.namelist()), sorted(patched.namelist()))
                for name in original.namelist():
                    if name in {patcher.SCRIPT_NAME, "manifest.json"}:
                        self.assertNotEqual(original.read(name), patched.read(name))
                    else:
                        self.assertEqual(original.read(name), patched.read(name), name)

                manifest = json.loads(patched.read("manifest.json"))
                self.assertEqual(manifest["version"], patcher.PATCHED_VERSION)
                self.assertEqual(
                    manifest["applications"]["zotero"]["id"],
                    "pdf2zh@guaguastandup.com",
                )
                javascript = patched.read(patcher.SCRIPT_NAME).decode("utf-8")

            self.assertEqual(javascript.count(patcher.PATCH_MARKER), 1)
            self.assertIn("pdf2zh-main-window-progress-stack", javascript)
            self.assertIn('getElementById("zotero-pane-stack")', javascript)
            self.assertIn('getElementById("zotero-pane-overlay")', javascript)
            self.assertIn("inset-inline-end: 24px", javascript)
            self.assertIn("z-index: 100", javascript)
            self.assertIn(".pdf2zh-task-close:focus-visible", javascript)
            self.assertIn("new EventSourceCtor", javascript)
            self.assertIn("source.onmessage = null", javascript)
            self.assertIn("card.addMonitorStop(stop)", javascript)
            self.assertIn("if (taskId) {", javascript)
            self.assertIn("if (!task) return;", javascript)
            self.assertIn("return Number.isFinite(startedAt)", javascript)
            self.assertIn("for (const win of Zotero.getMainWindows())", javascript)
            self.assertIn("pane?.document?.defaultView", javascript)
            self.assertIn("getTaskKey(filepath, endpoint)", javascript)
            self.assertIn('replace(/\\\\/g, "/")', javascript)
            self.assertIn("duplicates: result.duplicates + duplicateNames.length", javascript)
            self.assertIn("!Array.isArray(response.fileList)", javascript)
            self.assertIn("关闭提示（任务会继续运行）", javascript)
            self.assertIn("正在生成双栏 PDF", javascript)
            self.assertIn("targetItem.getAttachments()", javascript)
            self.assertIn("current.size !== incoming.size", javascript)
            self.assertNotIn(
                'const progressWindow = new ztoolkit.ProgressWindow(\n'
                '        "PDF\\u5904\\u7406"\n'
                "      ).createLine",
                javascript,
            )

    def test_rejects_modified_input(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            tampered = Path(folder) / "tampered.xpi"
            tampered.write_bytes(self.input_xpi.read_bytes() + b"tampered")
            with self.assertRaisesRegex(patcher.PatchError, "unsupported input XPI"):
                patcher.build_patched_xpi(tampered, Path(folder) / "out.xpi")

    def test_rejects_same_input_and_output(self) -> None:
        with self.assertRaisesRegex(patcher.PatchError, "must differ"):
            patcher.build_patched_xpi(self.input_xpi, self.input_xpi)

    def test_javascript_transform_is_fail_closed(self) -> None:
        with self.assertRaises(patcher.PatchError):
            patcher.patch_javascript("// unexpected upstream source")

    def test_validated_input_bytes_are_the_bytes_unpacked(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("zipfile.ZipFile(io.BytesIO(input_bytes)", source)
        self.assertNotIn('zipfile.ZipFile(input_path, "r")', source)


if __name__ == "__main__":
    unittest.main()
