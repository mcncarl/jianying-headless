# 无界面修改已有草稿的独立副本

适用于本机 11.5.0（主版本）/ 11.4.2（兼容版本），并保留 11.4.0 的历史草稿配置；正式交付限单时间线、多轨本地草稿。入口是 `scripts/headless_draft.py edit`；不是重建成单条视频，也不写原草稿。复制整个草稿目录后，仅修改明确指定的原生字段；未知字段和已登记效果结构在副本构建阶段保留。

## 边界与验收

- 必须先完全退出剪映，再检查、构建或登记副本。只接受当前草稿根的直接子目录；不解析 symlink，不复制云端身份。
- 正式可编辑交付仍限单时间线多轨；多时间线和云草稿拒绝。复合/嵌套的离线实验入口见下文，尚未通过原生保存持久化，不能当成已完成的可编辑副本交付。
- 用 draft ID 与 `draft_info.json` SHA-256 固定来源；整个原草稿的文件清单在构建、登记和回读时再次校验。来源被用户或剪映改变时停止，不用旧计划覆盖它。
- 源目录及已有本地素材不写入；新替换素材复制到副本 `Resources/headless-edited-media/`。草稿内路径重定向到副本；外部依赖仍保持原路径并验证字节身份。因此这是本机私有副本，不是自包含的可分发项目包。
- 历史本机 11.4.2 的 6 秒、6 轨样本中，9 项修改经过打开、完整播放、保存、冷重开及回读，原草稿保持不变；发行后的回归状态见核心项目 `docs/VERIFICATION.md`。原始私人草稿和工作记录不随源码分发。

## 工作流

```bash
python3 SKILL/scripts/headless_draft.py edit inspect --draft ABSOLUTE_EXISTING_DRAFT --out WORK/source-inspection.json
python3 SKILL/scripts/headless_draft.py edit build --plan WORK/edit-plan.json --out WORK/edited-build
python3 SKILL/scripts/headless_draft.py edit verify-build --build WORK/edited-build
python3 SKILL/scripts/headless_draft.py edit publish --build WORK/edited-build --audit WORK/edited-publish
python3 SKILL/scripts/headless_draft.py edit verify --build WORK/edited-build --report WORK/after-native-save.json
```

以上大写路径是占位符。`inspect` 输出草稿身份、轨道/片段/文字素材 ID 与当前时间范围；按当前结果准备计划，不按文件名猜测素材身份。

```json
{
  "schema": "jy14-edit-plan/v1",
  "name": "本次修改副本",
  "source": {
    "draft_path": "/absolute/current/draft",
    "draft_id": "FROM_INSPECTION",
    "timeline_sha256": "FROM_INSPECTION"
  },
  "operations": [
    {"op": "replace_text", "id": "TEXT_MATERIAL_ID", "text": "新的字幕内容"},
    {"op": "set_segment", "id": "VIDEO_SEGMENT_ID", "set": {"x": -0.3, "scale": 0.5}},
    {"op": "set_segment", "id": "AUDIO_SEGMENT_ID", "set": {"volume": 0.15}}
  ]
}
```

## 可用操作

- `set_segment`：片段 ID；`set` 支持 `start_us`、`duration_us`、`source_start_us`、`source_duration_us`、`speed`、`volume`、`visible`。视频/文字还支持 `x/y/scale/rotation/opacity`；文字不接受源时间、速度、音量。修改速度时同时提供相符的源/目标时长。动画属性不得只改静态基值；已带关键帧片段的源时间变更暂不支持自动重映射。
- `replace_text`：文字素材 ID；保留全文一致样式并按 UTF-16 更新长度。局部多样式文字拒绝自动改范围，需专用样式映射。
- `set_text_font`：文字素材 ID 和绝对 `source`，如 `{"op":"set_text_font","id":"TEXT_MATERIAL_ID","source":"/absolute/path/font.otf"}`。只处理外层及各样式字体路径一致、无在线字体身份的普通文字；静态 OTF/TTF 复制到 `Resources/headless-fonts/`，保留文字、样式和时间。缺失或不支持的字体会报错。独立换字体样例已完成本机 11.5.0 显示、保存重开与原生渲染检查，使用本机构建的 codec；完整 `edit verify` 仍因上游不接受省略默认 `speed: 1` 而失败，不能以字体检查通过代替完整回读通过。环境与证据见核心项目 `docs/LOCAL-FONTS.md`。新建或 Hypit 交接直接使用文字片段的 `font_path`，见 [无界面计划](headless-macos.md)。
- `replace_media`：视频/图片/GIF/本地音频素材 ID 和绝对 `source`。媒体类别须与原素材一致，所有引用的源范围都要落在新素材实际时长内。
- `rename_track`：轨道 ID 和 `name`。
- `duplicate_segment`：片段 ID、`start_us`，可指定同类 `track_id`。复制全部配套素材节点和关键帧 ID，保留媒体库文件复用，不共享可变片段节点。
- `remove_segment`：只从新副本时间线移除指定片段；不删除任何源文件。涉及已绑定转场或复合片段的复制/移除拒绝执行，避免损坏边界关系。
- `set_filter_intensity`：已登记 `filter` 素材 ID，`set` 为 `{"value": 0.5}`，范围 0–1。只修改强度，不能借此创建未经登记或授权的在线滤镜。

所有时间为整数微秒。同轨新增重叠会拒绝；已有重叠保留。原生保存会吸附时间到帧网格，回读允许最多一帧并列出差异。原生已存在的 MP3 尾部帧对齐填充只在原区间保持不变时保留，不作为新剪辑或替换素材越界的依据。

`verify` 比较完整已知结构及非空字段、轨道/片段顺序、四个活动镜像、媒体字节、首页身份和原草稿不变性。它不替代原生画面/声音验收；未登记效果的渲染能力也不能仅凭 JSON 保留而宣称通过。

登记中断可使用 `edit resume-publish`，条件与新建相同：目标目录须与构建清单逐字节一致，且编辑器关闭；不重写已经改动的副本。

## 复合片段：离线实验，禁止首页登记

11.4.2 采集的原生 `combination` 嵌套时间线与草稿内 `subdraft/<child-id>/` 结构保留，11.5.0 也提供离线实验入口，不展平成视频。`inspect` / `build` / `verify-build` 可以处理已采集的本地结构；原生冻结快照导出见 [export-macos.md](export-macos.md)。未知复合类型、循环/重复身份、路径越界、嵌套时长变更及多时间线仍拒绝。

已有操作加 `"timeline_id":"CHILD_TIMELINE_ID"` 可明确修改指定子时间线；`{"op":"create_compound","name":"复合片段名称"}` 会包裹所选时间线的全部轨道，不是任意选中子集。此操作仅用于隔离构建研究，未完成子草稿媒体库登记和原生持久化验收。

2026-09-15：4 秒/4 条内部轨道的文字、画中画、BGM 和轨道名修改，离线回读、原生导出画面与合成音频增益检查通过；但打开、播放、展开子轨道之后，原生保存把路径从 `subdraft/<child-id>/…` 改成 `subdraft//…`。脚本副本两种路径写法及原生首页“复制”对照均复现。随后全程 UI 新建的 8 秒复合对照也在冷加载后实际修改内部音量时复现：独立配置 `project_id` 丢空，新根级侧文件保存了修改，但旧 child-ID 目录仍为原内容，媒体库多出第二条记录。未修改重开时三个侧文件不变，不代表真实修改持久化通过。不能把这一有限样本泛化为所有剪映草稿，也没有可靠的纯字段修补方案。

因此 `edit publish` / `resume-publish` 在写目录或首页之前拒绝含复合片段的 build；不放宽路径检查、不保存后偷偷修补，也不以画面正确代替持久化验收。22 项历史专项检查的通过不取消此限制；原始对照草稿不随源码分发。
