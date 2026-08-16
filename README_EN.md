# Zotero Bilingual Side-by-Side Reader

<p align="center"><strong>Create layout-preserving, side-by-side bilingual PDFs in Zotero; click any sentence to highlight its counterpart.</strong></p>

<p align="center">
  <a href="README.md">简体中文</a> · <a href="README_EN.md">English</a>
</p>

<p align="center">
  <img src="docs/assets/showcase/textgrad-page-1-layout.png" width="1000" alt="A real translated TextGrad title page with side-by-side bilingual layout and linked highlighting">
</p>

<p align="center"><img src="docs/assets/showcase/textgrad-page-2-layout.png" width="1000" alt="A real translated TextGrad page preserving its framework figure and caption"></p>
<p align="center"><img src="docs/assets/showcase/textgrad-page-3-layout.png" width="1000" alt="A real translated TextGrad methods page with equations and linked sentence highlighting"></p>
<p align="center"><img src="docs/assets/showcase/textgrad-page-4-layout.png" width="1000" alt="A real translated TextGrad methods page with side-by-side layout and linked highlighting"></p>

<p align="center"><sub>All examples are real translated paper pages shown only to demonstrate layout preservation. Copyright remains with their respective authors.</sub></p>

<p align="center">
  <img src="https://img.shields.io/badge/Zotero-7--9-CC2936" alt="Zotero 7–9">
  <img src="https://img.shields.io/badge/Windows-10%20%7C%2011-0078D4" alt="Windows 10/11">
  <a href="https://github.com/Houxin2024/zotero-bilingual/releases/latest"><img src="https://img.shields.io/github/v/release/Houxin2024/zotero-bilingual?label=release" alt="Latest release"></a>
  <img src="https://img.shields.io/badge/core%20license-MIT-blue" alt="Core license MIT">
</p>

<p align="center">
  <a href="https://github.com/Houxin2024/zotero-bilingual/releases/download/v0.9.4/bilingual-linked-reader-0.9.4-windows.zip"><strong>⬇️ Download the Windows installer v0.9.4</strong></a>
  · <a href="docs/windows-one-click.md">Advanced setup guide (Chinese)</a>
  · <a href="https://github.com/Houxin2024/zotero-bilingual/issues">Report an issue</a>
</p>

## Get started in four steps

1. [Download the Windows package](https://github.com/Houxin2024/zotero-bilingual/releases/download/v0.9.4/bilingual-linked-reader-0.9.4-windows.zip) and extract the ZIP completely.
2. Double-click `Install-Windows.cmd` and wait for the green completion message.
3. In Zotero, open `Tools → Add-ons → gear icon → Install Add-on From File...`, install both XPI files from the automatically opened `addons` folder, then restart Zotero.
4. Right-click a paper and choose `PDF2zh → Translate`.

When upgrading, reinstall both XPI files as well. The local PDF2zh `4.0.3.3` revision contains the new translation card and progress fix.

> [!TIP]
> New installations use **SiliconFlow Free** by default, with no account or API key required. It is an online third-party service and may be rate-limited. Use your own API or a local service for sensitive papers.

The first setup downloads a private Python runtime, PDF2zh Next, and the local sentence-alignment model. Allow a few minutes. After setup, the backend starts automatically at Windows sign-in.

After setup, select a paper or PDF attachment in Zotero and choose `PDF2zh → Translate` from the context menu. The bilingual PDF is attached and opened automatically, with sentence-mapping progress in the lower-right corner; once mapping is ready, click any sentence to highlight its counterpart.

Translation status appears in a dismissible card inside the Zotero window. It shows stable processing stages instead of treating each nested stage as whole-document 100%. Closing the card does not stop the background task, and the card never stays above other applications.

## Privacy

PDF parsing, coordinate extraction, caption repair, and sentence alignment run locally. The Windows service listens only on `127.0.0.1`; this project's author does not receive your papers, translations, or API keys.

Text to be translated is sent to the provider you select. The default SiliconFlow Free service is online and third-party. For unpublished or confidential papers, use a provider you trust or a local translation service.

## FAQ

<details>
<summary><strong>Can I use it without configuring a provider or API key?</strong></summary>

Yes. A new installation defaults to PDF2zh Next with `SiliconFlow Free`, translating English into Simplified Chinese without an API key. If it is temporarily rate-limited, switch providers in the PDF2zh settings.

</details>

<details>
<summary><strong>Why are there two XPI files?</strong></summary>

`Zotero PDF2zh` translates the full paper and attaches the bilingual PDF. `Zotero Bilingual PDF Reader` adds sentence mapping, progress, and linked highlighting. Zotero requires users to confirm extension installation.

</details>

<details>
<summary><strong>Why is highlighting not ready immediately after translation?</strong></summary>

The PDF returns to Zotero first; the local backend then builds its sentence map. Progress appears in the reader and linked highlighting activates automatically when mapping finishes.

</details>

<details>
<summary><strong>Do I need to start the backend every time?</strong></summary>

No. The Windows package enables startup at sign-in by default. If you stopped it manually, use the “Zotero 双语阅读器 - 启动” desktop shortcut.

</details>

<details>
<summary><strong>Does it support macOS or Linux?</strong></summary>

The one-click package currently targets Windows 10/11 x64. macOS and Linux users can deploy PDF2zh Server and the watcher manually; cross-platform packaging contributions are welcome.

</details>

## Developer and advanced use

Regular Windows users do not need these instructions.

- [Windows setup and troubleshooting (Chinese)](docs/windows-one-click.md)
- [PDF2zh Server and sentence-map watcher integration](docs/pdf2zh-integration.md)
- [Download only the Zotero Bilingual PDF Reader v0.9.4 XPI](https://github.com/Houxin2024/zotero-bilingual/releases/download/v0.9.4/bilingual-linked-reader-0.9.4.xpi)

```bash
# Build the XPI
python scripts/build_xpi.py

# Build the complete Windows package
python scripts/build_windows_bundle.py
```

## Upstream projects and licenses

Full-document translation and layout preservation are powered by [Zotero PDF2zh](https://github.com/guaguastandup/zotero-pdf2zh), [PDFMathTranslate-next](https://github.com/PDFMathTranslate-next/PDFMathTranslate-next), and [BabelDOC](https://github.com/funstory-ai/BabelDOC). This project adds the one-click Windows backend, automatic sentence alignment, linked highlighting, and caption-collision repair.

This repository's own code is MIT-licensed. Bundled or installed upstream components retain their AGPL, Apache-2.0, or dual commercial licenses. See [`windows/THIRD_PARTY_NOTICES.md`](windows/THIRD_PARTY_NOTICES.md).

---

If this improves your paper-reading workflow, please give it a **Star** and share it with other Zotero users.
