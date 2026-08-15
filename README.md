# Zotero Bilingual Linked Reader

在 Zotero 的左右双栏中英 PDF 中，单击任一句，立即高亮另一栏对应译句。双击和拖选仍保留 Zotero 原生复制、批注行为。

![Zotero](https://img.shields.io/badge/Zotero-7--9-CC2936)
![License](https://img.shields.io/badge/license-MIT-blue)

## 特点

- 单击联动高亮，使用下一帧绘制，不人为等待双击超时。
- 英文点中文、中文点英文，双向工作。
- 语义句对 v3：结合版面顺序与多语言语义对齐，减少错位。
- 与 Zotero PDF2zh 的 compare PDF 配合；新论文无需写死文件名。
- 映射只读入一次，并按页建立索引；点击时不写磁盘。
- PDF、翻译文本和 API Key 均不上传到插件作者服务器。

## 安装插件

1. 从 Releases 下载 `bilingual-linked-reader-*.xpi`。
2. Zotero → `工具` → `插件` → 右上角齿轮 → `Install Add-on From File...`。
3. 选择 XPI 并重启 Zotero。

自行构建：

```bash
python scripts/build_xpi.py
```

## 给新论文生成句对映射

先用 PDF2zh 生成：

- 原始英文 PDF
- 中文 mono PDF
- 左右双栏 compare PDF，或 PDF2zh Next 生成的 `LR_dual.pdf`

然后执行：

```bash
python -m venv .venv
.venv/Scripts/pip install -r backend/requirements.txt
.venv/Scripts/python backend/prepare_sidecar.py \
  --compare paper.LR_dual.pdf
```

它会在 compare PDF 旁生成：

```text
paper.compare.pdf.bilingual.json
```

新版直接读取最终双栏 PDF，自动判断英文/中文位于左侧还是右侧，并在
段落内对齐句子；不再依赖中间 mono PDF 的坐标。若输入是
`paper.LR_dual.pdf`，sidecar 将相应命名为
`paper.LR_dual.pdf.bilingual.json`，插件会自动识别两种形式。

如果你使用本地 PDF2zh Server，可在翻译结束后自动调用此命令；参考 [PDF2zh 自动集成](docs/pdf2zh-integration.md)。PDF2zh 插件会把 compare PDF 自动附加回原 Zotero 条目，本插件从本地服务器读取同名 sidecar。

## 映射发现顺序

插件按以下顺序寻找 `<PDF文件名>.bilingual.json`：

1. Zotero PDF 附件旁边；
2. `extensions.bilingualLinkedReader.mapDirectory` 指定目录；
3. PDF2zh Server 的 `/translatedFile/` 接口。

默认服务器地址是 `http://127.0.0.1:8890`，也会兼容 PDF2zh 已配置的服务器地址。

## 仅升级已有映射

已有 PDF2zh geometry v2 sidecar 时，可批量升级：

```bash
.venv/Scripts/python backend/upgrade_folder.py \
  --translated-dir path/to/pdf2zh/server/translated
```

加 `--watch` 可持续监听新论文。

## 隐私与开源边界

仓库不包含论文 PDF、翻译结果、模型缓存、虚拟环境或 API Key。语义模型首次使用时由 `fastembed` 下载到本地缓存。

## License

MIT
