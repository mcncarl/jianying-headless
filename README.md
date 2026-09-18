# Jianying Headless

剪映专业版 macOS 的本地自动化工具：从剪辑计划生成可继续编辑的原生草稿，
在独立副本里修改已有多轨草稿，并在明确要求时调用本机原生引擎导出 MP4。

这是**私有源码预览**，不是剪映官方 SDK，也不是无需安装剪映的独立剪辑软件。
官方程序库、账号资料、真实素材与效果资源均不随仓库分发。

## 项目与 Skill 分开

| 目录 | 职责 |
| --- | --- |
| `engine/` | 草稿构建、独立副本编辑、资源校验与原生导出 |
| `bridge/` | 自有文件/管道适配源码，以及保留 MIT 来源声明的接口头文件 |
| `skills/yichen-jianying-edit/` | 单独可发现的 Agent Skill、入口脚本、口播计划编译与参考文档 |
| `tools/` | 本机桥接构建与发布包检查 |
| `tests/`、`engine/test_*.py` | 包装与功能验证 |
| `licenses/`、`THIRD_PARTY_NOTICES.md` | 上游许可证、参考关系与外部运行依赖边界 |

Skill 是调用入口，不包含剪映引擎。单独安装 Skill 后仍需取得本项目的访问权限、
检出核心代码，并安装匹配版本的剪映。

## 支持与限制

| 能力 | 当前范围 |
| --- | --- |
| 本地剪辑 | 视频分段、速度、音量、多轨、画中画、字幕/标题、本地 BGM/音效 |
| 图像素材 | PNG、JPEG、GIF；不同格式与组合的验收范围分别记录 |
| 动画 | 已接入的线性关键帧；不支持任意曲线变速或任意动画 |
| 原生效果 | 六类静态几何蒙版、叠化，以及三个已采集的滤镜/特效/花字；必须有本机匹配资源及使用权限 |
| 修改已有草稿 | 校验来源并创建独立副本，不覆盖原项目 |
| 原生 MP4 | 已验证快照、固定 11.4.2、H.264/AAC；不自动导出用户后来手改的内容 |
| 复合片段 | 离线保留/修改与冻结快照导出属实验能力；正式首页登记仍被阻止 |
| 不包含 | 在线模板、资源下载、账号权益获取、云端项目处理、任意版本适配 |

成功生成文件不等于原生播放、冷重开、视觉一致性、素材商用许可都已通过。
详见 [验证状态](docs/VERIFICATION.md) 和 [分发范围](docs/DISTRIBUTION-SCOPE.md)。

## 环境

- Apple Silicon Mac、macOS 26.0+；固定 codec 的最低系统版本为 26.0，本次实测为 26.5.1。
- 自行安装的剪映专业版：草稿运行档案固定为 **11.4.0 / 11.4.2**；原生导出只接受 **11.4.2**。
- Python 3.9+、FFmpeg / ffprobe、Xcode Command Line Tools。
- 桥接重建的已验工具链为 Apple clang 21.0.0 / macOS SDK 26.5。其他编译环境只有在输出仍匹配固定哈希时才会被接受。

版本号、build、bundle ID、官方库哈希和写入前完整应用签名都会核对。
不支持的版本会停止，不自动改常量、降级效果或下载资源。
这份预览尚未完成另一台干净机器的安装验收。

## 本机准备

仓库是私有的，以下检出操作需要已获授权的 GitHub 账号：

```bash
gh repo clone mcncarl/jianying-headless
cd jianying-headless
python3 tools/build_native_codec.py
python3 skills/yichen-jianying-edit/scripts/headless_draft.py doctor
```

构建脚本只编译本项目的桥接源码，链接已安装剪映提供的程序库。
它不解密或修改官方库，不下载剪映，不移除签名，不修改账号权益。
编译结果必须与原有已验 codec 的哈希一致；不一致时保留构建记录并停止。

如果 Skill 被复制到了另一个技能目录，通过环境变量指定核心项目检出位置：

```bash
export JIANYING_HEADLESS_ROOT="/absolute/path/to/jianying-headless"
```

这个变量只保存本机目录，不包含密钥。不要把个人路径写回仓库。
Skill 安装和依赖说明见 [独立 Skill](skills/yichen-jianying-edit/README.md)。

## 草稿与导出

先按 [计划格式](skills/yichen-jianying-edit/references/headless-macos.md) 准备 JSON。
[最小计划](examples/basic.plan.json) 的源路径是占位参数，必须替换为本次获准使用的真实文件。

```bash
python3 skills/yichen-jianying-edit/scripts/headless_draft.py build \
  --plan /absolute/path/to/plan.json --out "$PWD/work/new-build"
python3 skills/yichen-jianying-edit/scripts/headless_draft.py verify-build \
  --build "$PWD/work/new-build"
```

用户保存工作并完全退出剪映后，才能把新草稿登记到本机首页：

```bash
python3 skills/yichen-jianying-edit/scripts/headless_draft.py publish \
  --build "$PWD/work/new-build" --audit "$PWD/work/new-publish-audit"
```

这里的 `publish` 仅指**本机剪映首页登记**，不是互联网发布。
新建草稿不覆盖其他项目，失败不盲目重试；原生播放和冷重开仍需另行验收。

只有明确要求成片时才执行：

```bash
python3 skills/yichen-jianying-edit/scripts/headless_draft.py export \
  --build "$PWD/work/new-build" --out "$PWD/work/new-export"
```

导出是独立隔离进程，输出为新目录中的 `render.mp4`。默认不联网、不读取账号数据，
不会把素材、输出、日志或构建二进制加入 Git。

## 来源与许可

保留 pyJianYingDraft 的历史使用关系、jy-draftc 的 MIT 声明和各外部工具的许可边界，
不宣称整套链路完全原创或经过洁净室重写。详见 [第三方声明](THIRD_PARTY_NOTICES.md)。

原创部分沿用[个人学习和非商业使用许可](LICENSE)，商业使用需事先取得作者明确书面授权。
这不是 MIT / Apache-2.0 的整包开源授权；第三方内容继续适用各自原许可证，不受新增限制取代。
`licenses/` 中的第三方文本不自动授权整个项目，也不能代替剪映集成授权、账号权益或素材许可。

经单独授权，Skill 收录于公开的 [yichen-skills 总仓库](https://github.com/mcncarl/yichen-skills/tree/main/yichen-jianying-edit)。
该目录仅包含 Skill、调用入口和口播辅助脚本；本核心项目继续保持私有，公开 Skill 不授予核心访问权限。

## 可重复验证

```bash
python3 tools/check_package.py
python3 -m unittest discover -s tests -v
python3 tools/smoke_test.py
# 仅在需要验证原生导出时增加 --export；所有素材均为新生成的合成测试图案。
python3 tools/smoke_test.py --export
```

检查器覆盖源码类型、常见隐私模式、相对文档链接和固定哈希，不是完整安全审计或法律审查。
`tests/` 不需要私人原始素材；`engine/test_*.py` 的历史专项测试需要额外的对应 fixture，
不能直接对 `engine/` 运行普通 discovery 并视为可移植的完整回归。
所有测试输出保留在 `work/`，不登记到剪映首页，也不自动清理。
## Windows FFmpeg export

Windows uses the independent FFmpeg renderer and produces a playable MP4, not
a Jianying-native editable draft. See `docs/windows-ffmpeg.md` for setup,
supported features, logs, and verification.
