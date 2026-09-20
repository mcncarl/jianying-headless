# 本机无界面原生草稿

## 入口与验证边界

- 入口：本 Skill 的 `scripts/headless_draft.py`。代码位于单独检出的 Jianying Headless 项目 `engine/`；独立安装 Skill 时设置 `JIANYING_HEADLESS_ROOT`，入口核验代码与蓝图 SHA-256。
- 应用：`/Applications/VideoFusion-macOS.app`；目标根为当前用户 `Movies/JianyingPro/User Data/Projects/com.lveditor.draft`。
- 精确 runtime profile：11.5.0 为主版本，11.4.2 为兼容版本；11.4.0 仅保留历史草稿配置。各自固定 version、build、bundle ID 与 libvideoeditor hash；写 live 前核验完整深度签名及 Team ID。未列入的版本会拒绝，不改旧组件的常量。
- 历史本机验收包含 11.4.0 / 11.4.2 的 6 秒、6 轨草稿：视频切片、混合速度、画中画、字幕、标题、WAV BGM 和 MP3 音效。原生打开、播放、保存、退出和冷重开均有分项记录；私人工作目录及原始证据不随源码分发，见下方验证说明。
- 草稿 build/publish 入口没有调用网络、ASR、视频导出或在线资源下载。用户明确要求成片时另用 [export-macos.md](export-macos.md)；任意本地音频可以导入，但音频格式仍须在剪映中验收；实测源编码为 H.264/AAC、WAV、MP3。HEVC 在输入白名单，尚未在本次新建链路单独验收。
- 11.4.2 的 25 fps、12 度旋转画中画、同一主轨混合不同视频、彩色标题及描边均已完成实际显示、播放和冷重开回读。其他帧率和编码仍需按实际项目验收，不能把一个样例视为所有组合均已验证。
- 图片/GIF：PNG（含 alpha）、JPEG、原始 GIF 已接入生成和媒体库登记。历史 5 秒 PNG/GIF 样本完成原生播放、保存和冷重开；JPEG 有离线测试，未单独做原生显示验收。GIF 时长按容器实际时间，不用帧数乘平均帧间隔推算。
- 线性关键帧：历史 4 秒样本的图片 x/y、统一缩放、旋转、透明度，文字 y 和音量曲线经过原生播放、保存、冷重开及导出检查；合成音频曲线验证不等于绝对响度标定。
- 几何蒙版：11.4.2 的六种静态形状经过 12 秒样本的原生播放、保存、冷重开及隔离导出。资源仅来自实际采集并固定逐文件 hash 的本机样本，不可据此分发。
- 叠化转场：已采集 11.4.2 原生结构，支持主视频相邻片段；历史 4 秒样本的解析、导出、界面播放、保存和冷重开经过检查。
- 画面特效：当前仅保留已采集的轻微抖动；高清黑白滤镜与橙色描边花字已移除。普通文字、颜色和描边不受影响。
- 精确 `11.5.3-beta2` 只复用了逐文件哈希完全一致的叠化和轻微抖动资源，并各自完成自动构建、首页登记、播放、保存、完全退出、冷重开与结构回读。蒙版在首次打开时可见，但保存后冷启动重开持续卡在界面加载，因此仍拒绝；带会员标记的黑白质感滤镜虽在当前原生账号中手工保存重开通过，也没有加入无界面资源目录。
- 修改已有单时间线多轨草稿，使用 [edit-existing-macos.md](edit-existing-macos.md) 的 `edit` 入口，不用小型新建计划重建复杂项目。
- 复合片段已有离线保留/修改/构建和原生冻结快照导出实验能力；原生保存会在本次对照样本中将子草稿引用改成缺少 ID 的根级路径，新旧侧文件不一致，因此正式首页登记被拦截，不能交付为已验的可编辑嵌套草稿。范围及操作见 [edit-existing-macos.md](edit-existing-macos.md)。

以上为原始实现的分项历史背景，不是当前机器自动验收结果。发行整理后的测试与限制见核心项目的 `docs/VERIFICATION.md`；每次用户交付仍需对应画面、声音和保存验收。

## 正常调用

以下大写路径是待替换参数；实际执行使用绝对路径。输出必须是本次工作区 `work/` 下的新目录。

```bash
python3 SKILL/scripts/headless_draft.py doctor
python3 SKILL/scripts/headless_draft.py from-compiled --compiled WORK/compiled/compiled.json --name 本次新草稿名 --out WORK/headless-plan.json
python3 SKILL/scripts/headless_draft.py build --plan WORK/headless-plan.json --out WORK/headless-build
python3 SKILL/scripts/headless_draft.py verify-build --build WORK/headless-build
python3 SKILL/scripts/headless_draft.py publish --build WORK/headless-build --audit WORK/headless-publish
python3 SKILL/scripts/headless_draft.py verify --build WORK/headless-build --report WORK/after-native-save.json
```

已关闭剪映且计划已审查时，可用 `create --plan PLAN --work NEW_DIR` 一次执行 build + publish。它不是公开发布，只把新草稿登记到本机剪映首页。

`build` 可以在剪映运行时进行；`publish` / `resume-publish` 必须完全关闭编辑器，不能在签名检查还没完成时提前打开剪映。`verify` 是结构回读，保存并退出后执行，避免读取保存中的镜像；它不代替播放验收。

有编译音效提示时：

```bash
python3 SKILL/scripts/headless_draft.py cached-sound --name 啵1
python3 SKILL/scripts/headless_draft.py from-compiled --compiled WORK/compiled/compiled.json --name 本次新草稿名 --out WORK/headless-plan.json --cached-sounds-as-local
```

这项选择只支持已固定字节身份的本机「啵1」缓存。资源本身复制为本地音频，不保留在线音效的授权/资源身份。MP3 解码时长可能短于音效库显示时长，转换器保留实际可解码部分并报告差异；重复音效发生重叠时使用独立音轨，不裁掉事件。其他已合法取得的音频可直接写入下面的本地计划。

## `jy14-headless-plan/v1`

时间全部为整数微秒。计划可以直接手写，也可在 `from-compiled` 输出后加入 BGM、额外标题或 B-roll；任何改动应发生在 build 之前。

```json
{
  "schema": "jy14-headless-plan/v1",
  "name": "本次独立草稿",
  "canvas": {"width": 1920, "height": 1080, "fps": 30},
  "tracks": [
    {"type": "video", "name": "主视频", "segments": [
      {"source": "/absolute/source.mp4", "start_us": 0, "duration_us": 2000000,
       "source_start_us": 1000000, "source_duration_us": 3000000, "speed": 1.5, "volume": 1.0}
    ]},
    {"type": "text", "name": "字幕", "segments": [
      {"text": "这是一条可编辑字幕", "start_us": 0, "duration_us": 2000000,
       "size": 6, "x": 0, "y": -0.78, "color": "#FFFFFF", "border_color": "#000000", "border_width": 0.05}
    ]},
    {"type": "audio", "name": "按需 BGM", "segments": [
      {"source": "/absolute/music.wav", "start_us": 0, "duration_us": 2000000, "volume": 0.12}
    ]}
  ]
}
```

- 非空草稿的第一轨必须是连续的主视频：首段从 0 开始，同轨有序且不重叠，不允许黑场间隙。去掉某段源视频就是不将它加入计划；不删除源文件。
- 后续 `video` 为画中画/B-roll 轨，可以有间隙；`text` 用于字幕和标题；`audio` 用于本地 BGM/音效。所有轨道须在主视频长度内。空 `tracks` 可离线构造空草稿，但本次未做原生空草稿验收。
- 视频、音频：`source` 为本次授权的绝对本地文件路径；`source_start_us` 默认 0；`source_duration_us` 默认等于目标时长；`speed` 默认 1。`source_duration_us / speed` 必须与 `duration_us` 一致，且源区间不能越界。
- 速度范围 0.1–8；`volume` 是 0–4 线性增益。保持音高当前没有独立计划字段，不能宣称已经精确控制该开关。需要指定时原生核对或另行扩展。
- 视频额外支持 `scale`（统一缩放，默认 1）、`x/y`（默认 0）、`rotation`（默认 0）、`opacity`（0–1，默认 1）。它们不是像素坐标；按剪映画面实测调整。文字默认 `y=-0.78`，多行或画面主体不同不要盲用默认位置。
- 文字支持 `size`、`x/y`、`color`、`border_color`、`border_width` 和可选的 `font_path`；颜色为 `#RRGGBB`。省略 `font_path` 时使用原有内置中文字库，指定时直接使用绝对路径对应的本地静态 `.otf` / `.ttf`，如 `"font_path": "/absolute/path/SourceHanSerif-Bold.otf"`。文件缺失、损坏或格式不支持会报错，不回落到默认字体。普通字号与描边使用原生内部值，不等同于 CSS px。emoji 使用 UTF-16 长度范围写入。
- 字体按内容 hash 复制到草稿 `Resources/headless-fonts/`，同字节字体只保留一份文件；`build.json` 的 `font_assets` 单独记录依赖，不进入视频/音频媒体库。创建时同步写入文字材料和富文本样式的字体路径，`verify-build` / `verify` 检查两处绑定及字体字节，原生导出暂存也复制并映射字体。构建后不再依赖原字体文件。当前不接受字体名称、URL、TTC/OTC 字体集合、可变字体或在线账号字体；每个文字片段使用一个字体，字重由字体文件决定。Hypit 转换见 [素材与字体交接](hypit-handoff.md)。独立 TTF/CFF 样例已完成本机 11.5.0 显示、可编辑性、保存重开及原生渲染检查；使用本机构建的 codec，完整编辑回读仍触发上游默认速度字段问题。具体环境与限制见核心项目 `docs/LOCAL-FONTS.md`，每个实际项目仍须验收。
- 输入视频当前限制 H.264/HEVC、单视频流及最多一条音频流，拒绝旋转元数据及未知像素格式；不偷偷转码。`video` 轨也可直接使用 PNG、JPEG 和 GIF，按实际编码识别媒体类型并保留原文件。静态图片不能指定源偏移或非 1 倍速度；GIF 暂不接受超过实际动画时长的循环延长。APNG、复合片段、文字/自定义蒙版、未采集的滤镜/特效/花字、除叠化外的转场和在线模板尚未接入新计划，未知字段会报错。
- 创建时为每个源文件复制一个内容 hash 命名的草稿内素材。源路径不再是播放依赖；不要清理草稿 `Resources`。不写剪映全局 bookmark，也不携带用户账号或在线签名 URL。

片段可加入 `keyframes`，例如 `"keyframes": {"x": [{"at_us": 0, "value": -0.5}, {"at_us": 2000000, "value": 0.5}]}`。时间相对片段开头；每条曲线至少两点，第一点在 0，严格递增且不超过片段时长，仅支持线性插值。视频支持 x/y/scale/rotation/opacity/volume，文字支持 x/y/scale/rotation，音频支持 volume；不能只改动画属性的静态值。当前关键帧限制源起点为 0、速度为 1，剪过源入点或变速后的关键帧时间映射尚未验收，会拒绝。不要将这个限制解释为剪映自身不支持。

视频轨片段可加入 `"mask": {"shape": "circle", "width": 0.28, "height": 0.5}`。`shape` 可选 `circle/rectangle/line/mirror/star/heart`；尺寸为原生归一化包围框，不是像素，默认 0.28 × 0.5 与 16:9 样本相符。支持 `x/y`、`rotation`、`feather`（0–1）、`invert`（布尔），矩形另支持 `round_corner`（0–1）。线性蒙版为半平面，拒绝无效果的 `width/height` 参数。蒙版关键帧未接入。

蒙版资源的采集来源仍为 11.4.2，已审查的运行版本为 11.5.0 / 11.4.2；不修改来源身份或资源哈希。构建会验证资源目录、拒绝符号链接和字节变化，并完整复制至草稿的 `Resources/headless-native/`。但原生保存会按资源 ID 回指剪映自己的缓存；回读仅接受原先固定目录且逐文件 hash 一致的这种改写，并报告 `native_cache_dependency: true`。不要因此宣称移机可用、完全离线独立或可公开分发；缓存缺失时停止，不自动下载、去掉授权身份或改造收费资源。

主视频片段可加入 `"transition_out": {"name": "dissolve", "duration_us": 400000, "edge_policy": "require-handles"}`，表示接到下一段的叠化。当前仅主视频相邻片段可用，末段不能加；时长需对应偶数帧且不长于两边任一段。默认要求两侧各有半个转场长度的源余量，变速时按速度换算，静态图像可自然保持。源余量不足会拒绝；只有明确接受端点重复帧时，才指定 `repeat-edge`，具体受影响边会记录在 `transition_audit`，时间线总时长不被缩短。资源限制与蒙版相同；原生渲染画面、编辑器播放和冷重开验收已通过。叠化不等于音频淡出淡入：同相测试原声在重叠区间增加约 6.03 dB，原生 UI 导出同一样本约 6.02 dB，二者仅差约 0.01 dB。这是该测试样本的原生叠加行为；仍须检查真实音频，不静默修正音量，详见 [export-macos.md](export-macos.md)。

### 已采集的视觉效果

在主视频之后加入一条 `effect` 轨，可包含有序、无重叠且位于主视频内的区间：

```json
{"type":"effect","segments":[{"name":"light-shake","start_us":0,"duration_us":2000000,"params":{"range":0.15,"speed":0.33}}]}
```

`range` 和 `speed` 为 0–1，分别控制抖动幅度和特效运动速度，默认 0.15 / 0.33；后者不是视频倍速。不接受 source、关键帧或片段速度字段。

轻微抖动保留原生资源身份与逐文件哈希，采集时未见会员标记，但技术支持不授予商用或再分发许可。原生保存可能回指已固定的本机缓存；只接受已记录的源文件与编译产物，未知字节变化会拒绝。不自动下载或绕过账号要求。

高清黑白滤镜和橙色描边花字已移除支持、资源条目与使用示例。旧计划或冻结快照含这两项时会报错，不自动移除效果后交付。普通文字及自定义颜色、描边仍可用。历史测试记录不构成当前支持范围。

## 验证与部分失败

`verify-build` 核对完整文件清单、计划 hash、编解码、目标路径、轨道/素材引用、时间、速度、音量、画布、文字内容与样式。`verify` 还检查 live 草稿与首页身份、四个活动镜像的内容一致和 inode 独立，以及所有媒体字节。原生保存会把路径改为剪映特定 draftpath placeholder 或 `./Resources/`，验证器只接受已实测格式并解析到同一素材。

原生会把时间吸附到帧网格，回读容差为一帧；这不是允许删除发音保护区。口播时间映射仍由 `edit_plan.py` 在 build 前审查。

首页索引原件及替换前后的扩展属性保存在审计目录。quarantine 等安全属性必须原样保留；当前 macOS 会为替换 inode 赋予不同的 `com.apple.provenance` 字节，该唯一变化单独记录，不能为了通过检查移除它或其他属性。

若出现目录已写入、但首页登记失败，查看本次审计 `published.json` 后可以：

```bash
python3 SKILL/scripts/headless_draft.py resume-publish --build WORK/headless-build --audit WORK/headless-resume
```

它只在整个目标目录仍与 build 清单逐字节一致、剪映关闭、当前 runtime 一致时继续，不覆盖草稿；已成功登记时返回只读幂等结果。若用户或剪映已改变任何文件，保留现场并定向诊断。审计保留原首页索引，所有中间材料保留；不自动删除、回滚或重新生成另一份同名项目。

本机封装不等于可分发安装包：不打包 codec、真实草稿、缓存音效、bookmark 或账号配置。移机或公开发布需要另行授权与兼容验证。
