# PDF2zh 自动集成

目标是在 PDF2zh 生成 `*.dual.pdf`、`*.LR_dual.pdf` 或 `*.compare.pdf` 后，后台生成同名 `*.bilingual.json`。Zotero PDF2zh 插件仍负责下载、附加并打开 PDF；Bilingual Linked Reader 负责读取 sidecar 和联动高亮。

## 推荐：持久 watcher

不要在 `/translate` 请求内同步生成映射，否则 Zotero 会额外等待模型处理。让翻译接口先返回 PDF，再由 watcher 处理：

```bash
python backend/watch_translated.py \
  --translated-dir /path/to/pdf2zh/server/translated \
  --cache-dir /path/to/model-cache \
  --status /path/to/automation-status.json
```

watcher 会在文件稳定后处理，映射写入 `.building`，验证存在有效句对后再原子替换最终 sidecar。异常写入状态文件并退避重试，不会把 PDF 翻译标记为失败。

Windows 一次性启动：

```powershell
powershell -ExecutionPolicy Bypass -File integration/start_watcher.ps1
```

把该调用加入现有 PDF2zh Server 启动脚本即可随服务器自动恢复。脚本会检测已有 watcher，避免重复启动。

新版从最终双栏 PDF 直接提取左右语言、句子和坐标并生成 v4 sidecar，不依赖 mono PDF 或旧 geometry v2 文件。已有有效 v3/v4 映射会被保留。

## 服务端读取

插件会请求：

```text
GET http://127.0.0.1:8890/translatedFile/<compare文件名>.bilingual.json
```

服务器应限制路径在翻译输出目录中，防止目录穿越。
