# Windows 支持

本项目原本只面向 macOS。本页说明在 Windows 上可以做到什么、怎么做，
以及哪些能力仍然只在 macOS 可用。

**结论先行**：Windows 上已完成可编辑草稿的构建、结构校验与首页登记，
但界面内打开、播放、保存及冷重开仍待验收。剪映原生 MP4 导出与原生效果资源采集
仍只在 macOS 可用；不需要剪映的独立 MP4 路径见 [Windows FFmpeg 导出](windows-ffmpeg.md)。

## 支持范围

| 能力 | macOS | Windows | 说明 |
| --- | --- | --- | --- |
| 环境检查 `doctor` | 支持 | 支持 | 校验安装版本、官方库哈希、桥接源码与工具 |
| 生成草稿 `build` | 支持 | 支持 | 同一套计划格式与蓝图 |
| 结构校验 `verify-build` / `verify` | 支持 | 支持 | 包含四份镜像一致性检查 |
| 首页登记 `publish` | 支持 | 支持 | 带审计目录与失败留证 |
| 一键 `create` | 支持 | 支持 | 计划 → 构建 → 登记 |
| 合成素材自检 `tools/smoke_test.py` | 支持 | 支持 | 不读用户素材、不登记首页 |
| 原生 MP4 导出 | 支持 | **不支持** | 依赖 macOS 侧编译的导出组件 |
| 原生效果资源（蒙版/转场/轻微抖动） | 支持 | **不支持** | 资源目录绑定 macOS 采集档案，Windows 明确拒绝 |
| 本地字体随草稿保存 | 支持 | 未验收 | 代码路径可运行，尚无本机实测记录 |

导出与效果资源不是"暂时没测"，而是**主动失败关闭**：
遇到需要这些能力的需求会在写入前报错，不会产出半成品草稿。

## 运行环境

- 64 位 Windows 10 / 11。已验证环境为 Windows 11（10.0.22631）。
- 剪映专业版 **11.5.0**（已验证 build 14471）。
- Python 3.9+、FFmpeg 与 ffprobe 在 `PATH` 中。
- **不需要** C++ 编译器、Xcode 或任何构建工具链。
- 需要 PowerShell 或 CMD 下的 Python；`.gitattributes` 会强制检出为 LF，
  请不要用 `core.autocrlf=true` 覆盖它（见下文"为什么不能改行尾"）。

剪映安装目录由程序自行定位，默认在
`%LOCALAPPDATA%\JianyingPro\Apps\<版本>`；
草稿目录默认在
`%LOCALAPPDATA%\JianyingPro\User Data\Projects\com.lveditor.draft`。

## 与 macOS 实现的差别

Windows 没有 `fcntl`、扩展属性和 `renameat2`，因此桥接层按平台分派。
`engine/platform_support.py` 是唯一的切换点，macOS 行为保持逐字节不变。

| 关注点 | macOS | Windows |
| --- | --- | --- |
| 草稿加密 | 编译 `jy14_codec.cpp` 生成 helper 进程 | 用 `ctypes` 在进程内直接调用 `videoeditor.dll` 导出的 `lvve::EncryptUtils` |
| 密文文件名 | `draft_info.json` | `draft_content.json` |
| 目录事务锁 | `fcntl.flock` | 命名互斥体 `Local\jy14-headless-*` |
| 独占放置 | `renameat2` / 目标已存在即失败 | `os.rename`（目标存在时本就会失败） |
| 编辑器进程检查 | `/bin/ps` | Toolhelp32 快照 |
| 扩展属性 | 读取并复制，拒绝改动 `com.apple.*` | 记录为空操作（剪映不使用 NTFS 数据流） |
| 重解析点 | `O_NOFOLLOW` / `dir_fd` | 逐级检查重解析点；无 `dir_fd`，检查与打开之间无法完全消除竞态 |
| 缓存路径 | `~/Movies/JianyingPro/...` | `%LOCALAPPDATA%\JianyingPro\...` |

加密编解码直接绑定官方库，**不重新分发**任何官方二进制：

```
?enable@EncryptUtils@lvve@@QEAAX_N@Z
?isEnable@EncryptUtils@lvve@@QEAA_NXZ
?encrypt@EncryptUtils@lvve@@QEAA?AV?$basic_string@...@@AEBV34@@Z
?decrypt@EncryptUtils@lvve@@QEAA?AV?$basic_string@...@@AEBV34@0AEA_N@Z
```

明文始终只存在于内存中；加密与解密都不落临时明文文件。

## 快速开始

```powershell
git clone https://github.com/masfrank/jianying-headless.git
cd jianying-headless
python skills/yichen-jianying-edit/scripts/headless_draft.py doctor
```

`doctor` 输出中包含 Windows 特有的字段：

```json
{
  "engine_library": "videoeditor.dll",
  "bridge_sources_verified": 3,
  "runtime_profile": "jy14-headless-win-11.5.0",
  "timeline_filename": "draft_content.json"
}
```

计划格式与 macOS 完全一致，路径使用 Windows 形式：

```powershell
python skills/yichen-jianying-edit/scripts/headless_draft.py build `
  --plan C:\path\to\plan.json --out C:\path\to\new-build
python skills/yichen-jianying-edit/scripts/headless_draft.py verify-build `
  --build C:\path\to\new-build
```

保存当前工作并**完全退出剪映**后，把新草稿登记到本机首页：

```powershell
python skills/yichen-jianying-edit/scripts/headless_draft.py publish `
  --build C:\path\to\new-build --audit C:\path\to\new-publish-audit
```

审计目录必须是尚不存在的新目录：这是为了保证每次登记都留下独立证据，
失败时也会在审计目录里写入 `failure.json` 与恢复建议。

## 组件身份与来源校验

Windows 没有需要编译的 helper，因此信任锚点是**官方库哈希**加**桥接源码哈希**：

- `bridge/SOURCE_MANIFEST-windows.json` 固定官方 `videoeditor.dll` 的 SHA-256、
  四个 codec 符号名、密文文件名，以及三个桥接模块的源码哈希。
- `engine/platform_support.py` 中的 `WINDOWS_IO_MANIFEST_SHA` 固定该清单自身的哈希。
  它**不**把 `platform_support.py` 自身的哈希写进清单，否则清单哈希与源码哈希会互相依赖，
  永远无法收敛。
- Skill 入口把 `engine/` 各模块的哈希写进 `PINS`，其中包括 `platform_support.py`。

重新生成清单（仅在审查过桥接改动后）：

```powershell
python tools/runtime_report_windows.py --verify-codec --write-manifest
```

`--verify-codec` 会用真实的密文草稿做一次解密→加密→再解密往返，确认符号可解析且结果一致。
默认报告只读取版本和文件指纹，不加载 DLL；安装版本或引擎指纹未经审核时，
即使显式要求 `--verify-codec` 也不会加载 DLL。源码库中的 Windows CI 只跑
离线回归与封包检查，没有安装剪映，不代表界面打开、播放、保存和冷重开验收。

## 为什么不能改行尾

`PINS` 与 `SOURCE_MANIFEST*.json` 校验的是**原始字节**的 SHA-256。
在 `core.autocrlf=true` 的检出中，每个 LF 都会被改写成 CRLF，
于是所有哈希失配，`doctor` 会以"组件已变更"为由拒绝运行——

这跟剪映安装没有任何关系，纯粹是检出方式造成的。
仓库根目录的 `.gitattributes` 用 `* text=auto eol=lf` 固定了这一点。
如果本地已有覆盖设置，请执行：

```powershell
git config core.autocrlf false
git ls-files --eol bridge engine
```

如果输出仍显示工作区为 `w/crlf`，请先保存自己的未提交改动，
再在一个新的目录重新检出仓库；不要使用会清除工作区改动的重置命令。

## 已验证 / 未验证

以下结果来自 2026-09-20 在 Windows 11 + 剪映 11.5.0 (14471) 的实际执行。

| 检查项 | 结果 | 范围 |
| --- | --- | --- |
| `doctor` | 通过 | 版本、build、官方库哈希、清单与源码哈希、ffmpeg/ffprobe |
| codec 往返 | 通过 | 对真实草稿做解密→加密→解密，明文一致、长度一致 |
| 便携测试套件 | 48 项通过，2 项按平台跳过 | `python -m unittest discover -s tests` |
| 视觉效果模块 | 9/9 | `engine/test_native_visual_effects.py` |
| 合成素材自检 | 通过 | `tools/smoke_test.py`，不读用户素材、不登记首页 |
| 源码包检查 | 通过 | `tools/check_package.py`，85 个文件 |
| 完整流程 | 通过 | 1920×1080@30fps，4 轨（2 视频轨 / 1 文字轨 / 1 音频轨）、3 个素材、6 秒 |
| 首页登记 | 通过 | 草稿出现在首页列表首位；四份镜像一致；素材源文件未被改动 |
| 登记确定性 | 通过 | 同一份构建重复登记，生成的首页索引哈希完全一致 |
| **未做** | — | 剪映里打开/播放/保存/完全退出/冷重开（需人工） |
| **未做** | — | 原生 MP4 导出、原生效果资源、本地字体、其他剪映版本、干净机器安装 |

结构校验通过不等于画面与声音验收。打开、播放、保存、冷重开是独立的证据层级，
需要在剪映界面里人工完成。

## 可复现命令

```powershell
python skills/yichen-jianying-edit/scripts/headless_draft.py doctor
python tools/check_package.py
python -m unittest discover -s tests -v
python -m unittest engine.test_native_visual_effects
python tools/smoke_test.py
```

`engine/` 下其余专项测试需要作者本机保留的 fixture、capture 或 build 资料
（`--fixture` / `--build` / `--capture` / `--font`），这些原始资料不随仓库分发，
因此无法在仓库内直接复现。这与平台无关。

## 已知限制

- 不支持的剪映版本会在写入前被拒绝；不要手工替换哈希绕过。
- 原生效果资源的采集档案绑定 macOS 11.4.2，Windows 上无法满足，会明确报错。
- 原生导出不在支持范围。
- 首页登记是本机行为，不是互联网发布。
- 草稿在剪映里手工修改后，不会自动同步回计划或旧快照。
