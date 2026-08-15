# PDF2zh 自动集成

目标是在 PDF2zh 生成 `*.compare.pdf` 后，同时生成同名 `*.bilingual.json`。Zotero PDF2zh 插件仍负责下载并附加 PDF；Bilingual Linked Reader 负责读取 sidecar 和联动高亮。

## 推荐调用

在 PDF2zh Server 的翻译成功分支中调用：

```python
subprocess.run([
    align_python,
    "backend/prepare_sidecar.py",
    "--original", original_pdf,
    "--translated", mono_pdf,
    "--compare", compare_pdf,
    "--output", compare_pdf + ".bilingual.json",
], check=True, timeout=600)
```

后处理失败不应把 PDF 翻译标记为失败。`prepare_sidecar.py` 的语义阶段失败时会自动保留 geometry v2 sidecar，因此插件仍可使用基础联动。

## 不修改 PDF2zh 源码的方式

让后台进程监听输出目录：

```bash
python backend/upgrade_folder.py \
  --translated-dir /path/to/pdf2zh/server/translated \
  --watch
```

PDF2zh Server 需先生成 geometry v2 sidecar。仓库中的 `generate_map.py` 可用于生成该文件；完整的一次性入口是 `prepare_sidecar.py`。

## 服务端读取

插件会请求：

```text
GET http://127.0.0.1:8890/translatedFile/<compare文件名>.bilingual.json
```

服务器应限制路径在翻译输出目录中，防止目录穿越。
