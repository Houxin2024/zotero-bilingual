# Third-party components bundled or downloaded by the Windows installer

Third-party projects remain under their own licenses rather than this
repository's MIT license. The Windows ZIP includes the pinned official PDF2zh
Server, PDF2zh Zotero add-on, uv release artifacts, and the AGPL license texts;
the installer verifies their recorded SHA-256 identities before use. Remaining
Python packages and the model are downloaded into the user's private runtime.
The PDF2zh Server and Zotero add-on receive the documented local patches below;
their corresponding source transformations remain in the release bundle and
installation directory.

## Zotero PDF2zh Server and Zotero add-on

- Project: <https://github.com/guaguastandup/zotero-pdf2zh>
- Pinned release: `v4.0.3`
- License: GNU Affero General Public License v3.0
- License text: <https://github.com/guaguastandup/zotero-pdf2zh/blob/v4.0.3/LICENSE>
- `patches/zotero-pdf2zh-v4.0.3-loopback.patch` changes the Flask listener from
  `0.0.0.0` to `127.0.0.1`; it does not alter translation.
- `scripts/patch_pdf2zh_progress_server.py` separates whole-task progress from
  nested PDF-processing stages and reserves 100% for successful completion.
- `scripts/patch_pdf2zh_addon_ui.py` replaces the always-on-top progress popup
  with a dismissible progress card inside the Zotero main window. It shows
  stable stage labels instead of presenting nested stage percentages as the
  whole translation percentage.
- The bundle retains the exact official XPI as
  `windows/payload/zotero-pdf2zh-4.0.3.xpi` and installs the deterministic
  local revision `zotero-pdf2zh-4.0.3.3-blr.xpi` generated from it. The local
  `4.0.3.3` version is above upstream `4.0.3` for reliable upgrades and below
  a future upstream `4.0.4`.
- The installer saves the upstream license and progress patch source beside the
  installed notices and leaves the patched Python source in the installation
  directory.
- The modified PDF2zh XPI/server files and their patch sources are distributed
  under AGPL-3.0, not the repository's MIT license.

## pdf2zh-next

- Project: <https://github.com/PDFMathTranslate-next/PDFMathTranslate-next>
- Pinned package: `pdf2zh-next==2.9.0`
- License: GNU Affero General Public License v3.0
- Installed from PyPI into the private runtime; the upstream license is saved
  in the installation's `licenses` directory.

## BabelDOC

- Project: <https://github.com/funstory-ai/BabelDOC>
- Pinned package: `babeldoc==0.6.2`
- License: GNU Affero General Public License v3.0
- Installed from PyPI into the private runtime; the upstream `v0.6.2` license
  is saved in the installation's `licenses` directory.

## PyMuPDF

- Project: <https://github.com/pymupdf/PyMuPDF>
- Pinned package: `pymupdf==1.25.2`
- License: GNU Affero General Public License v3.0 or Artifex commercial license

## FastEmbed and sentence-alignment model

- FastEmbed: <https://github.com/qdrant/fastembed>, pinned to `0.8.0`, Apache-2.0
- Model: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, Apache-2.0;
  the installer pins Qdrant's ONNX snapshot to commit
  `faf4aa4225822f3bc6376869cb1164e8e3feedd0`
- Model files are downloaded to the user's local model cache during setup.

## uv

- Project: <https://github.com/astral-sh/uv>
- Pinned release: `0.12.5`
- Licenses: Apache-2.0 OR MIT

The installer does not upload PDFs, translated text, API keys, or Zotero library
data to this project's servers. Translation providers configured in Zotero
PDF2zh may have their own data-handling terms.
