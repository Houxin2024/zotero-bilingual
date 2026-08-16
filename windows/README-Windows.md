# Zotero Bilingual PDF Reader - Windows 一键安装

这个安装包面向 Windows 10/11 x64。它会安装本地 PDF2zh 翻译服务和双语句子映射后台；不需要 WSL、Conda、系统 Python 或管理员权限。

## 安装

1. 把发布页下载的 Windows ZIP **完整解压**，不要直接在压缩包预览窗口中运行文件。
2. 双击根目录的 `Install-Windows.cmd`。
3. 等待安装器校验包内固定版本的官方 PDF2zh Server 与 uv，并下载 `pdf2zh-next`、BabelDOC 和 Python 3.12 私有运行环境。
4. 安装结束后会打开 `addons` 文件夹。在 Zotero 中打开“工具 → 插件”，分别安装文件夹中的：
   - `zotero-pdf2zh-*.xpi`
   - `bilingual-linked-reader-*.xpi`
5. 重启 Zotero。之后右键论文 PDF，选择 PDF2zh 翻译即可。新安装默认使用 SiliconFlow Free，不需要 API Key。

默认安装目录为：

```text
%LOCALAPPDATA%\ZoteroBilingualLinkedReader
```

安装器只写入当前用户目录。首次安装和首次生成句对时需要下载较大的 Python 依赖、字体及语义模型，建议预留至少 3 GB 磁盘空间。

## 日常使用

安装完成后，桌面和开始菜单会出现启动、停止、状态和插件安装目录快捷方式。后台默认在登录 Windows 后自动启动，不需要每次打开 PowerShell、Python 或手动双击。若你曾手动停止后台，可用“Zotero 双语阅读器 - 启动”快捷方式恢复。

若不希望登录后自动启动，可从命令提示符重新安装并明确关闭自启：

```bat
Install-Windows.cmd -NoAutoStart
```

服务仅监听本机 `127.0.0.1:8890`。健康状态页面为：

```text
http://127.0.0.1:8890/health
```

## 常见问题

### Zotero 显示 NetworkError

先运行桌面的启动快捷方式，再访问上述健康状态地址。若仍失败，打开“Zotero 双语阅读器 - 状态”，并检查安装目录下的 `logs` 文件夹。

### 翻译完成后暂时不能联动高亮

翻译 PDF 会先返回 Zotero，句对映射随后在本机后台生成。状态会依次显示“等待 PDF 写入完成”“加载语义模型”“编码中英句子”和“句子映射已就绪”。首次运行需要下载语义模型，耗时会更长。

watcher 会在映射前保守检查图片说明排版。只有 `Figure/Table/图/表 + 编号` 的标题与正文发生真实几何相交时才会整体重排；普通正文、公式和没有碰撞的图注不会被改动。

### 8890 端口已占用

停止旧的 PDF2zh 服务后重新启动。也可以在命令提示符中用其他端口重新安装，例如：

```bat
Install-Windows.cmd -Port 8891
```

随后在 Zotero 的 PDF2zh 和 Zotero Bilingual PDF Reader 设置中使用同一个本地地址。

### 安装中断

直接再次运行 `Install-Windows.cmd`。已经校验成功的下载和已安装的私有运行环境会被复用。

## 隐私与许可证

PDF 解析、版面坐标提取和句子映射在本机完成。默认的 SiliconFlow Free 无需 API Key，但会把待翻译文本发送给第三方联网服务；请根据论文敏感性选择服务并阅读其数据条款。

本项目代码采用 MIT License。安装包内置或安装的 PDF2zh Server、PDFMathTranslate-next、BabelDOC、PyMuPDF 等组件有各自许可证；详见 `windows/THIRD_PARTY_NOTICES.md`。安装器使用固定上游版本并校验发布文件，且关闭上游的可变 `main` 自动更新。

## 为发布者构建安装包

在仓库根目录运行：

```bash
python scripts/build_windows_bundle.py
```

输出位于 `dist/bilingual-linked-reader-<version>-windows.zip`，其中包含 Windows 安装器、后台源代码、当前 Zotero XPI、许可证与第三方声明。
