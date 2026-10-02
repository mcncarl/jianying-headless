# 从零开始：用自己的视频生成第一个剪映草稿

目标：用一段本地视频的前 **2 秒**，生成带有一条可编辑文字的剪映草稿。先完成这个最小流程，再尝试多轨、转场或 Agent 自动剪辑。

不需要 GitHub 登录、API Key、AI 付费账号或会员素材。原视频不会被修改；第一次练习不自动导出视频，也不覆盖已有工程。

## 先确认：这台电脑适不适合

当前是有明确运行条件的源码预览，不是下载即可运行的通用安装包。

| 项目 | 要求 |
| --- | --- |
| 电脑 | Apple Silicon Mac，例如 M 系列芯片；不支持 Windows、Intel Mac 或 Rosetta 终端 |
| 系统 | macOS 26.0 或以上；仅精确的 11.4.0 build 481 草稿配置支持已验 macOS 15.6.1 |
| 剪映 | 国内版剪映专业版 11.5.0；11.4.2 有兼容配置；11.4.0 build 481 仅支持新建草稿，CapCut 国际版不适用 |
| 安装位置 | `/Applications/VideoFusion-macOS.app` |
| 程序身份 | 必须通过项目检查；只看到相同版本号还不够 |
| 编译工具 | 11.5.0／11.4.2：Apple clang 21.0.0 / SDK 26.5；11.4.0 build 481：Apple clang 17.0.0 / SDK 26.1；产物均须匹配各自固定哈希 |
| Python | 3.9 或以上 |
| 素材 | 至少 2 秒的本地 H.264、yuv420p 视频；首次建议普通 SDR MP4 |

**尚未完成另一台独立 Mac 的完整验收。** 目前已验证本机 11.5.0 的原生导出，以及 [11.4.0 build 481](APPSTORE-1140-B481.md) 在 macOS 15.6.1 上的新建、登记、播放、保存和冷重开；不等于其他安装包和电脑均兼容。

剪映官方入口为 [剪映官网](https://www.capcut.cn/)。官网可能只提供新版，本项目不提供安装包，也没有确认长期可用的官方 11.5.0 下载地址。已有新版时，不要为试用直接降级或覆盖旧草稿；没有匹配版本，应先停在环境准备阶段。

本项目采用[个人学习和非商业使用许可](../LICENSE)，不应按 MIT / Apache-2.0 的商业开源项目理解。

## 第 1 步：打开终端，确认电脑

在 Mac 上按 Command＋空格，搜索“终端”并打开。后续每个代码框都可以复制到终端，再按回车；不要复制代码框外的说明。

```bash
uname -m
sw_vers -productVersion
```

**成功标志：**第一行输出 `arm64`，系统版本为 `26.x` 或以上；精确的 11.4.0 build 481 配置也可使用已验的 `15.6.1`。如果是 `x86_64`，先确认是否为 Intel Mac 或终端运行在 Rosetta 模式；不要继续构建。

本教程以命令输出作为成功对照，不使用模拟界面截图。真实案例画面见 [README](../README.md#画面对照)，不代表每一步的安装截图。

## 第 2 步：安装命令行工具

先检查 Apple 的编译工具：

```bash
xcode-select -p
xcrun clang++ --version
xcrun --show-sdk-version
```

如果提示没有工具，运行：

```bash
xcode-select --install
```

在系统弹窗中完成安装后，重新运行上面的三个检查命令。如果提示已安装，无需重复安装。
Apple 提供 [Xcode 与附加工具下载入口](https://developer.apple.com/xcode/resources/)。安装最新版不保证与项目已验工具链完全一致，构建哈希仍是最终门槛。

接着安装 Python 和 FFmpeg。FFmpeg 在这里负责读取视频信息和检查输出，不替代剪映原生导出。

若尚未安装 Homebrew，请从 [Homebrew 官网](https://brew.sh/) 阅读并执行官方安装步骤；按安装完成后的 **Next steps** 配置终端，不从不明镜像复制安装脚本。然后运行：

```bash
brew install python ffmpeg
python3 --version
ffmpeg -version
ffprobe -version
```

**成功标志：**三个工具都能打印版本，Python 不低于 3.9。不需要 `pip install`，本教程的工具使用 Python 标准库。

后续若要显式指定本地字体，再按[字体使用说明](LOCAL-FONTS.md#optional-font-parser)
安装可选解析依赖。本教程的默认字体流程和已有快照的验证不需要该依赖。

若 `brew` 提示找不到命令，先执行安装器给出的 Next steps，再重新打开终端；不要继续复制后续步骤。

## 第 3 步：准备剪映首页

1. 打开匹配的官方剪映，确认“关于”中的版本。
2. 第一次使用剪映时，创建并保存一个空白工程，返回首页。
3. 保持默认草稿位置，不把本次流程指向自定义磁盘或云端。
4. 保存其他工作，通过剪映菜单正常退出。关闭窗口不一定代表程序完全退出。

这个步骤让剪映自行建立首页索引。本项目不伪造一个新的首页数据库，也不修改已有草稿来完成初始化。

剪映自身如果需要账户登录，由使用者在官方应用里完成；项目脚本不读取或代办账号权益。

## 第 4 步：下载项目

以下命令把源码放入“文稿”中的一个独立文件夹，不需要安装 `gh` 或登录 GitHub：

```bash
cd ~/Documents
git clone https://github.com/mcncarl/jianying-headless.git
cd jianying-headless
```

**成功标志：**文件夹中能看到 `README.md`、`engine`、`tools` 和 `skills`。

如果提示文件夹已存在，不要删除或覆盖：已有检出直接进入其目录；需要另一份时，用新的文件夹名克隆。网络失败时先解决 GitHub 访问问题，不切换到来源不明的压缩包。

从此步骤起，命令都在项目根目录执行。新开终端后先运行：

```bash
cd ~/Documents/jianying-headless
```

## 第 5 步：检查条件，构建桥接组件

可以先执行只读精确工具链检查：

```bash
python3 tools/build_native_codec.py --check-toolchain
```

它会寻找符合要求的工具链，不改变系统默认设置。有多个 Xcode 时可加
`--developer-dir /Applications/Xcode.app/Contents/Developer` 指定目录；
该参数同样适用于后面的构建命令。完整版本和仍待解决事项见
[Issue 修复进展](ISSUE-REMEDIATION.md)。

先运行新手检查：

```bash
python3 tools/start_here.py check
```

- `PASS`：该项已满足。
- `WARN`：按说明处理。首次未构建桥接组件是预期状态；首页未初始化会影响后面的登记。
- `FAIL`：先解决这项问题，不继续生成草稿。

检查没有 FAIL 后构建：

```bash
python3 tools/build_native_codec.py
python3 skills/yichen-jianying-edit/scripts/headless_draft.py doctor
```

**成功标志：**构建输出 `built-and-verified`，或已构建时输出 `already-valid`；随后 doctor 输出 `"status": "ok"`，11.5.0 对应 `"app_version": "11.5.0"`。

“桥接组件”是让项目代码调用本机剪映的小程序。`doctor` 是环境体检，不会剪视频；通过体检不等于画面和声音已经验收。

构建可能需要等待签名检查。出现 `Compiler output differs` 时不能把新哈希粘进配置让它强行通过。保留提示的日志，按后面的报错表处理。

## 第 6 步：生成第一个草稿

准备一段有权使用、已完整下载的普通 MP4。视频至少 2 秒；建议先用 1080p，避免首次就用 HDR、旋转信息未归一化的手机原片或特殊编码。

运行：

```bash
python3 tools/start_here.py build
```

出现提示后，将**一个视频文件**从访达拖入终端，再按回车。脚本支持文件名中的中文、空格和引号。

脚本会取前 2 秒，沿用视频宽高，以 30 fps 创建一条视频轨和一条“我的第一个可编辑草稿”文字轨。它不是自动识别字幕，也不添加会员素材。

**成功标志：**输出 `"status": "build-verified"`，显示草稿名和后续命令。
输出在 `work/first-draft-随机字符/`，其中 `next-steps.md` 保存了可直接复制的本次命令，`next-steps.json` 是机器可读记录。再次运行会建立新目录，不覆盖上一份。

此时还没写入剪映首页，也没导出 MP4；剪映首页暂时看不到草稿是正常的。

## 第 7 步：登记到剪映首页

确认剪映已经保存工作并完全退出。复制脚本最后打印的 **publish 命令整行**执行；它已带好本次实际路径，无需手改随机目录名。

`publish` 在本项目里只表示“登记到本机剪映首页”，**不会上传到互联网**。

**成功标志：**命令成功结束，再打开剪映，首页出现刚才输出的 `first-draft-…` 草稿。

打开后检查：

- 时长为 2 秒，画面来自刚才选择的视频。
- 文字可以单独选中、修改，视频不是一张合成截图。
- 没有“素材丢失”提示，原片有声音时检查播放声音。

首页未出现或发布报错时，保留原目录和日志，不重复创建同名项目、手改索引或删除其他工程。

## 第 8 步：保存，再打开一次

先完成这份未修改样例的验收：

1. 播放开头和结尾，保存工程并正常退出。
2. 重新启动剪映，打开同一草稿，检查画面、文字和声音。
3. 再次正常退出剪映。
4. 复制 `next-steps.md` 中的 **verify 命令**执行。

**成功标志：**输出 `"status": "verified"`，并确认素材未变、活动数据一致。
这一步检查原生保存是否保留工程结构；只有亲自播放，才能确认画面和声音符合预期。

验收前若自行改过文字、时间线或音量，verify 与原计划比较时可能正确地报错。不要为了通过而丢掉手工修改；先用未改过的练习稿跑通，再进入[独立副本编辑流程](../skills/yichen-jianying-edit/references/edit-existing-macos.md)。

## 可选：导出 MP4

只在需要检查原生视频导出时，执行 `next-steps.md` 中的 **export 命令**。

**成功标志：**本次工作目录中的 `export/render.mp4` 存在，`result.json` 状态为 `encoded-and-decoded`。还应实际播放成片，检查首末画面和声音。

这条命令导出的是最初构建的快照，**不是剪映里后来手改的版本**。手改后的最终作品可使用剪映界面的导出功能；不要把旧快照误当成最新工程。

遇到超时、少帧或解码错误就保留记录，不把残缺视频当作成功结果，也不自动重试掩盖错误。

## 常见问题

| 看到什么 | 原因或下一步 |
| --- | --- |
| 找不到 `Info.plist` / `VideoFusion-macOS.app` | 剪映未安装在支持的位置，或装的是其他版本/产品；先核对官方安装，不重命名软件假装匹配 |
| `Unsupported Jianying version/build/identity` | 版本或安装身份未适配；15.x 等版本不能按 11.5.0 使用 |
| `Editor library differs` | 版本号相同但程序库不同；不是简单“取消版本限制”就能解决 |
| `Compiler output differs` | 构建结果与已验组件不同，常见于工具链差异；提交脱敏构建日志供排查，不修改固定哈希 |
| `IO/codec component unavailable` | 先运行桥接构建脚本 |
| `Headless component changed` | Skill 与核心代码不同步或文件变动；优先使用同一仓库附带入口，不跳过校验 |
| 首页索引缺失 / `root path mismatch` | 在剪映中初始化默认本地草稿目录；自定义位置暂不支持，不手工修改索引 |
| 提示编辑器仍运行 | 保存工程，通过应用菜单退出，而不是只关闭窗口 |
| 视频格式、像素格式或旋转信息报错 | 选择普通 SDR H.264 视频，或自行导出一个正常定向的新副本；脚本不偷偷转码 |
| 提示目录已存在 | 保存旧输出；首次构建工具每次创建新目录。不要重复执行同一次 publish/export 命令 |
| 蒙版 / 转场 / 特效资源缺失 | 这些资源不随源码分发；先跑无效果的基础示例，不复制作者缓存或绕过权益检查 |
| 导出超时或帧数不一致 | 已知问题仍在调查，保留日志并停止交付；不要关闭帧数检查 |

求助时提供：芯片类型、macOS 版本、剪映版本、检查失败行、编译器和 SDK 版本、失败步骤。
剪映安装版本不匹配时，可运行 `python3 tools/runtime_report.py` 获取不含个人路径、
素材或账号信息的报告。报告不会上传任何数据；官方签名通过也不代表新版本已适配。
分享日志前删去个人路径、草稿名或素材信息；不要上传账号数据、官方程序库或私人视频。

## 跑通之后

先读 [计划格式](../skills/yichen-jianying-edit/references/headless-macos.md)，再尝试多轨、变速和字幕。
需要 Agent 协助时再安装[配套 Skill](../skills/yichen-jianying-edit/README.md)；基本流程不依赖任何 Agent。
完整兼容边界及未解决问题见 [验证状态](VERIFICATION.md)。
