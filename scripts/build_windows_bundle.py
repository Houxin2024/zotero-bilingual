#!/usr/bin/env python3
"""Build the self-contained source payload used by Install-Windows.cmd."""

from __future__ import annotations

import argparse
import io
import importlib.util
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "addon"
BACKEND = ROOT / "backend"
INTEGRATION = ROOT / "integration"
WINDOWS = ROOT / "windows"
DIST = ROOT / "dist"
ZIP_TIMESTAMP = (2020, 1, 1, 0, 0, 0)


def iter_payload_files(folder: Path) -> list[Path]:
    return [
        path
        for path in sorted(folder.rglob("*"))
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix.lower() not in {".pyc", ".pyo"}
    ]


def zip_info(name: str, executable: bool = False) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    mode = 0o755 if executable else 0o644
    info.external_attr = (mode & 0xFFFF) << 16
    return info


def write_bytes(
    archive: zipfile.ZipFile,
    name: str,
    content: bytes,
    *,
    executable: bool = False,
) -> None:
    archive.writestr(zip_info(name, executable), content)


def build_xpi() -> tuple[str, bytes, dict]:
    manifest = json.loads((ADDON / "manifest.json").read_text(encoding="utf-8"))
    version = str(manifest["version"])
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in iter_payload_files(ADDON):
            name = path.relative_to(ADDON).as_posix()
            write_bytes(archive, name, path.read_bytes())
    return f"bilingual-linked-reader-{version}.xpi", output.getvalue(), manifest


def validate_sources() -> None:
    required = [
        ROOT / "Install-Windows.cmd",
        ROOT / "LICENSE",
        WINDOWS / "README-Windows.md",
        WINDOWS / "install.ps1",
        WINDOWS / "start.ps1",
        WINDOWS / "stop.ps1",
        WINDOWS / "status.ps1",
        WINDOWS / "common.ps1",
        WINDOWS / "requirements-win.txt",
        WINDOWS / "THIRD_PARTY_NOTICES.md",
        WINDOWS / "patches" / "zotero-pdf2zh-v4.0.3-loopback.patch",
        WINDOWS / "payload" / "zotero-pdf2zh-server-4.0.3.zip",
        WINDOWS / "payload" / "zotero-pdf2zh-4.0.3.xpi",
        WINDOWS / "payload" / "zotero-pdf2zh-4.0.3.3-blr.xpi",
        WINDOWS / "payload" / "uv-0.12.5-windows-x64.zip",
        ROOT / "scripts" / "patch_pdf2zh_addon_ui.py",
        ROOT / "scripts" / "patch_pdf2zh_progress_server.py",
        WINDOWS / "licenses" / "ZOTERO-PDF2ZH-AGPL-3.0.txt",
        WINDOWS / "licenses" / "PDFMATHTRANSLATE-NEXT-AGPL-3.0.txt",
        WINDOWS / "licenses" / "BABELDOC-AGPL-3.0.txt",
        BACKEND / "watch_translated.py",
        BACKEND / "repair_caption_overlap.py",
        BACKEND / "prepare_sidecar.py",
        BACKEND / "generate_map.py",
        BACKEND / "semantic_realign_map.py",
        BACKEND / "requirements.txt",
        INTEGRATION / "start_watcher.ps1",
        ADDON / "manifest.json",
        ADDON / "bootstrap.js",
        ADDON / "prefs.js",
        ADDON / "sync.js",
    ]
    missing = [str(path.relative_to(ROOT)) for path in required if not path.is_file()]
    if missing:
        raise SystemExit("Windows bundle inputs are missing: " + ", ".join(missing))

    patch_script = ROOT / "scripts" / "patch_pdf2zh_addon_ui.py"
    spec = importlib.util.spec_from_file_location("blr_pdf2zh_addon_patch", patch_script)
    if spec is None or spec.loader is None:
        raise SystemExit(f"Could not load PDF2zh add-on patcher: {patch_script}")
    patcher = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = patcher
    spec.loader.exec_module(patcher)
    upstream = WINDOWS / "payload" / "zotero-pdf2zh-4.0.3.xpi"
    tracked = WINDOWS / "payload" / "zotero-pdf2zh-4.0.3.3-blr.xpi"
    with tempfile.TemporaryDirectory(prefix="blr-pdf2zh-addon-") as folder:
        rebuilt = Path(folder) / tracked.name
        patcher.build_patched_xpi(upstream, rebuilt)
        if rebuilt.read_bytes() != tracked.read_bytes():
            raise SystemExit(
                "windows/payload/zotero-pdf2zh-4.0.3.3-blr.xpi is not the "
                "deterministic output of scripts/patch_pdf2zh_addon_ui.py"
            )


def build_bundle(output: Path) -> Path:
    validate_sources()
    addon_name, addon_bytes, addon_manifest = build_xpi()
    bundle_manifest = {
        "schemaVersion": 1,
        "bundleVersion": str(addon_manifest["version"]),
        "addonFile": f"addons/{addon_name}",
        "addonId": addon_manifest["applications"]["zotero"]["id"],
        "platform": "windows-x86_64",
        "installer": "Install-Windows.cmd",
        "pdf2zhAddonFile": "windows/payload/zotero-pdf2zh-4.0.3.3-blr.xpi",
        "pdf2zhAddonPatch": "scripts/patch_pdf2zh_addon_ui.py",
        "pdf2zhServerProgressPatch": "scripts/patch_pdf2zh_progress_server.py",
    }

    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    temporary.unlink(missing_ok=True)
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            write_bytes(
                archive,
                "Install-Windows.cmd",
                (ROOT / "Install-Windows.cmd").read_bytes(),
            )
            write_bytes(archive, "LICENSE", (ROOT / "LICENSE").read_bytes())
            write_bytes(
                archive,
                "README-Windows.md",
                (WINDOWS / "README-Windows.md").read_bytes(),
            )
            write_bytes(
                archive,
                "bundle-manifest.json",
                (json.dumps(bundle_manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
            )
            write_bytes(archive, f"addons/{addon_name}", addon_bytes)

            for patch_script in (
                ROOT / "scripts" / "patch_pdf2zh_addon_ui.py",
                ROOT / "scripts" / "patch_pdf2zh_progress_server.py",
            ):
                write_bytes(
                    archive,
                    patch_script.relative_to(ROOT).as_posix(),
                    patch_script.read_bytes(),
                    executable=True,
                )

            for folder in (WINDOWS, BACKEND, INTEGRATION):
                for path in iter_payload_files(folder):
                    name = path.relative_to(ROOT).as_posix()
                    executable = path.suffix.lower() == ".py"
                    write_bytes(archive, name, path.read_bytes(), executable=executable)

        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return output


def parse_args() -> argparse.Namespace:
    manifest = json.loads((ADDON / "manifest.json").read_text(encoding="utf-8"))
    default_output = DIST / f"bilingual-linked-reader-{manifest['version']}-windows.zip"
    parser = argparse.ArgumentParser(
        description="Build the native-Windows installer bundle and Zotero XPI."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=default_output,
        help=f"output ZIP (default: {default_output})",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    print(build_bundle(args.output))


if __name__ == "__main__":
    main()
