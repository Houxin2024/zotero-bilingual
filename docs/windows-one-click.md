# Windows 一键安装

适用于 Windows 10/11 x64 和 Zotero 7–9。安装使用当前用户的 `%LOCALAPPDATA%` 目录，不要求管理员权限、WSL、系统 Python 或 `winget`。

## 最快安装

1. 下载 `bilingual-linked-reader-<版本>-windows.zip`。
2. 右键 ZIP 选择“全部解压”，不要在压缩包预览窗口中直接运行。
3. 双击 `Install-Windows.cmd`，等到绿色的安装完成提示。
4. 脚本会打开 `addons` 文件夹。在 Zotero 中打开“工具 → 插件 → 齿轮 → Install Add-on From File...”，分别安装：
   - `zotero-pdf2zh-*.xpi`
   - `bilingual-linked-reader-*.xpi`
5. 重启 Zotero，打开 PDF2zh 设置，选择翻译服务并填写该服务需要的配置。
6. 右键一篇 PDF → `PDF2zh` → 翻译。双栏 PDF 打开后，右下角会显示句对映射进度；完成后单击任意一侧句子即可联动高亮。

Zotero 的扩展安装确认和第三方翻译服务设置无法由普通脚本安全地代替用户完成。这两部分只需配置一次。

## 安装器会做什么

- 安装到 `%LOCALAPPDATA%\ZoteroBilingualLinkedReader`。
- 校验并安装包内固定版本的 uv、官方 Zotero PDF2zh Server 和 PDF2zh XPI；即使 Windows 无法连接 GitHub Release 也不影响这三项。
- 建立独立的 Python 3.12 环境，安装 PDF2zh Next 和句对映射依赖。
- 预热多语言句向量模型，避免第一篇论文时静默下载或降级。
- 在映射前检测并重排真正发生几何碰撞的图注/表注标题与正文。
- 对固定的上游 Server 应用可审计的单行补丁，使它只监听 `127.0.0.1`。
- 启动翻译 Server 和句对 watcher，创建启动/状态/停止快捷方式，并默认添加当前用户的登录自启。

首次安装需要网络下载，建议预留 2–3 GB 磁盘空间。论文 PDF、翻译文本和 API Key 不会发送到本项目的服务器；用户自行选择的翻译提供商可能有不同的数据政策。

图注检查只匹配 `Figure/Table/图/表 + 编号` 开头且文字框真实相交的说明；普通正文、公式和没有碰撞的图注不会被修改。修复后的坐标会用于随后生成的句子映射。

## 日常启动和排查

正常情况下，后端会在 Windows 登录后自动启动。如果 Zotero 显示“双语后台未运行”：

1. 双击桌面的“Zotero 双语阅读器 - 启动”。
2. 双击“Zotero 双语阅读器 - 状态”，确认 Server 和 watcher 都是 `RUNNING`。
3. 查看 `%LOCALAPPDATA%\ZoteroBilingualLinkedReader\logs`。

如果 8890 端口已被其他程序占用，可以用其他端口安装：

```powershell
powershell -ExecutionPolicy Bypass -File windows\install.ps1 -Port 8891
```

然后在 PDF2zh 设置中把 Server 地址设为 `http://127.0.0.1:8891`。Bilingual Linked Reader 在未显式设置自己地址时会继承该地址。

## 高级参数

```powershell
# 仅校验安装包，不写入文件
powershell -ExecutionPolicy Bypass -File windows\install.ps1 -DryRun

# 不创建登录自启快捷方式
powershell -ExecutionPolicy Bypass -File windows\install.ps1 -NoAutoStart

# 安装但暂不启动后端
powershell -ExecutionPolicy Bypass -File windows\install.ps1 -NoStart
```

完整参数可通过下列命令查看：

```powershell
Get-Help .\windows\install.ps1 -Detailed
```

## 安全和许可证边界

这个仓库的代码使用 MIT License。安装包内置固定版本的 Zotero PDF2zh 与 uv，并在安装时获取 PDF2zh Next/BabelDOC、PyMuPDF、FastEmbed 和句向量模型；它们保留各自的 AGPL、Apache-2.0 或商业双重授权条款。安装目录会保留上游许可证、来源链接和本地安全补丁，详见 `windows\THIRD_PARTY_NOTICES.md`。
