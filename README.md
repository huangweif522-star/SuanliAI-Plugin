# SuanliAI-Plugin · 算力 AI 视频插件

为字字动画提供算力 AI 视频生成、模型分类检索、模型目录在线更新，以及插件版本检查和安装包下载。

**当前版本：v1.1.4**

[下载最新版](https://github.com/huangweif522-star/SuanliAI-Plugin/releases/latest) · [算力 AI 接口文档](https://suanliai.top/docs)

## 安装

1. 在 Releases 下载 **Suanli_Ai_v1.1.4.zip**。
2. 解压后，将 **Suanli_Ai_v1.1.4** 整个文件夹放入字字动画安装目录的 `user_resources/plugins/video_plugins/`。
3. 确认 `video_plugins/Suanli_Ai_v1.1.4/main.py` 存在；不要多套一层同名文件夹。
4. 重启字字动画，在视频插件列表选择 **算力AI视频 v1.1.4**，填写自己的算力 AI 令牌。

原 `_internal/plugins/video_plugins/` 安装位置也兼容。安装包和包内文件夹始终按版本号命名；宿主按文件夹区分配置，安装新版本后需填写该版本的令牌和参数。

## 使用

- 黑灰界面按接口、模型、生成参数、素材、等待和版本区分。
- 粘贴模型名或多个关键词检索，支持按家族、来源系列筛选。
- 当前模型卡片和列表中的已选项同步高亮；搜索和筛选不会自动更换模型。
- “更新模型”读取当前算力 AI 账号的模型目录；内置 111 个模型供离线检索。
- 时长仅 **15 秒、30 秒**，默认 **30 秒**；分镜时长不覆盖插件选择。
- 默认 **多参考图生成视频**、**9:16**、最长等待 **3600 秒**。
- 生成 30 秒时需选择支持该时长的模型；平台可能调整不支持的时长。

## 检查插件版本与下载

在插件底部点击 **检查更新**。检查只访问本仓库的公开 GitHub Releases，不需要 GitHub 账号或令牌，不会将算力 AI 令牌发给 GitHub。

检测到新版后，点击 **下载新版**，插件将安装包保存至本机下载目录并显示完整保存路径。下载会校验版本、文件哈希和 ZIP 完整性。解压安装新版后重启字字动画生效。

插件打开时不会自动检查版本。GitHub 暂时不可达或限流时，可以稍后重试，也可从本仓库 Releases 手动下载。

## 独立发布

本仓库仅维护 **算力 AI 插件**。版本号、安装包和更新地址独立维护。

源码位于 `plugin/`。发布时运行 `python build_release.py`，生成带版本号的安装包、`latest.json` 和 `SHA256SUMS`。GitHub Actions 先运行离线验证，再创建对应版本的 GitHub Release。

本地验证（Python 3.12、Node.js，宿主提供 `requests`）：

```text
python -m pip install requests
python -m unittest discover -s tests -p "test_*.py"
node --check plugin/ui/app.js
python build_release.py
```

界面行为检查位于 `tests/*.cjs`，可用 Node.js 分别运行。测试使用虚构令牌和模拟响应，不发起真实视频生成任务。
