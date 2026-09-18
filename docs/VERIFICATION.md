# 验证状态

## 本次私有源码预览

这是包装、路径迁移和来源整理，不是剪映全部功能或所有机器的完整兼容测试。
以下结果来自 2026-09-15 对本次发布快照的实际执行；没有实测的项目不标为通过。

- 原始本机实现和已安装 Skill 保留，没有被这份发布快照覆盖。
- 已从所收录的 C++ 源码重建 codec，SHA-256 与原有已验二进制完全一致：
  `b6533eb5eb1eea58dfa74fb1d16d3bb580970fe881f587605d358af1745f971d`。
- 未完成另一台干净机器验收；不同编译器/SDK导致的输出差异会被固定哈希拒绝。

| 本次检查 | 结果与范围 |
| --- | --- |
| 安装入口体检 | 11.4.2 的应用版本、build、官方库与 codec 哈希通过 |
| IO 来源核对 | `SOURCE_MANIFEST.json` 中原文件 SHA 一致，选出的 32 个定义 AST 完全一致；不是洁净室重写 |
| 打包测试 | 10/10：跨工作目录定位、独立 Skill 配置、缺失/相对路径拒绝、核心篡改拒绝、严格 JSON、快照/链接、目录迁移与越界拒绝 |
| 原有功能回归 | 149/149；使用本机保留的既有测试 fixture，只读源资料，变更写入新的隔离测试目录 |
| Skill 校验 | 标准结构校验和只读私有发布预检通过；不是远端推送或安装器可发现性验收 |
| 源码包检查 | 语法、常见隐私模式、相对链接、组件/资源/来源哈希与 Git 跟踪边界通过；不等于完整漏洞/秘密扫描 |
| 新包合成 build | 2 秒、3 轨、4 片段，分段与 1.5 倍速度、旋转画中画、中文字幕；结构和全部来源字节检查通过 |
| 新包原生导出 | 640×360、30 fps、60/60 帧、H.264/AAC、标准 `isom` MP4；完整解码通过；容器时长 1.999998 秒 |
| 本次画面检查 | 0.5 秒和 1.5 秒抽帧实际查看：测试图案、旋转画中画、中文字幕与变速后的源画面可见 |
| 未做 | 新发布快照的剪映 UI 打开/保存/冷重开、人工听感、其他机器/工具链、所有效果组合 |

原有回归明细：基础草稿 24、关键帧 10、图片/GIF 7、蒙版 12、转场 10、视觉效果 19、
独立副本编辑 17、复合结构 22、导出防护 28，合计 149。复合首页登记拒绝属于必须通过的
防护项，不是将其可编辑交付标为通过。

可复现命令：

```bash
python3 tools/build_native_codec.py
python3 skills/yichen-jianying-edit/scripts/headless_draft.py doctor
python3 tools/check_package.py
python3 -m unittest discover -s tests -v
python3 tools/smoke_test.py --export
JY_NATIVE_EXPORT_TEST_WORK="$PWD/work/export-guard-checks" python3 engine/test_native_export.py
```

`smoke_test.py` 自行生成合成素材，保留随机唯一 `work/package-smoke-*` 中的计划、
日志、build、导出及 `smoke-result.json`；不调用 ASR，不使用用户媒体，不登记首页。
其他历史专项测试的 `--fixture` / `--build` / `--capture` 参数需要另行提供匹配测试资料；
这些本机原始资料不随仓库分发。原始本机测试结果与本次发行快照检查分别记录。

## 既有本机验收背景

在打包前，现有本机 11.4.2 工作流已对多轨视频、文字、本地音频、图片/GIF、线性
关键帧、六类静态几何蒙版、叠化和三个已采集视觉资源做过分项原生测试。
新建草稿、原生打开/保存/冷重开、隔离导出和画面比较是不同证据层级，不能互相代替。
这些历史测试的原始素材、截图、用户草稿和日志不随源码发布。

## 始终保留的边界

- 复合片段的正式首页登记仍被拦截：冻结快照导出可用不代表嵌套草稿保存持久化已通过。
- 低分辨率花字曾在严格图像重合检查中未达门槛；不能声称所有分辨率逐像素一致。
- 叠化区间的同相原声音频可能叠加增益，需检查真实响度；不会静默调整。
- 部分原生资源带会员或不可商用标记；`entitlement_verified: false` 不是授权成功。
- `encoded-and-decoded` 表示编码/完整解码检查通过，不等于人眼画面和主观听感全部合格。

下一次变更需按实际影响选择验证；不得为了完成发布而降低既有版本、哈希、资源或
首页登记门禁。原生交互验收只在具备条件并有对应证据时单独报告。
## Windows FFmpeg export

Run `python -m unittest engine.test_windows_ffmpeg -v`. The backend must
preserve the verified build manifest, retain its filter graph and logs, pass
ffprobe, and pass a full `ffmpeg -xerror` decode. Its output is a rendered MP4,
not a continuable Jianying-native draft.
