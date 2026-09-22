# 剪辑计划与脚本接口

## 输入计划

`edit_plan.py compile` 读取 `jianying-edit-plan/v1`。所有时间都是原片秒数，只有编译结果使用微秒。路径、hash、时长由本次素材实测取得，不沿用示例值。

```json
{
  "schema": "jianying-edit-plan/v1",
  "source": "/absolute/path/persistent-assets/source.mp4",
  "source_sha256": "本次副本的 SHA-256",
  "source_duration": 10.0,
  "fps": 30,
  "speed": 1.5,
  "voice_volume": 1.0,
  "settings_confirmed": true,
  "settings": {"intensity": "本次确认的力度", "sound_effects": "本次确认的需求"},
  "check_decisions_pending": false,
  "bgm": false,
  "keeps": [
    {"start": 0.0, "end": 2.4, "protect": [[0.08, 2.32]], "reason": "完整开场"},
    {"start": 3.0, "end": 6.0, "protect": [[3.08, 5.92]], "reason": "保留重说中更完整的一遍"}
  ],
  "subtitles": [
    {"start": 0.08, "end": 2.32, "text": "这里是第一句"},
    {"start": 3.08, "end": 5.92, "text": "这里是后一句"}
  ],
  "sfx": [{"source_time": 3.08, "name": "啵1", "volume": 0.13, "reason": "演示开始"}],
  "decisions": [{"action": "DELETE", "start": 2.4, "end": 3.0, "reason": "气口"}]
}
```

如果用户要在剪映内使用会员的语音转文字，不需要本流程生成字幕，将字幕字段显式设为：

```json
"subtitles": false
```

- `settings_confirmed` 表示 Agent 已从当前用户请求取得参数；字段自身不能代替用户指令。
- `check_decisions_pending=false` 只能在 CHECK 项已经解决后填写。草稿中间版可继续准备，但不能把未决定的删除伪装成最终计划。
- `protect` 为保留词的有效发音区间，可按词列多个。只有明确无语音、且确实需要保留的画面才用空列表。
- 编译器向内选择可容纳完整发音的整帧区间，不自动延长保留区间。无解时报错，应调整低能量处的边界，而不是删保护词来通过检查。
- 一条字幕可以跨多个保留片段；起止点必须仍在保留内容中，文字仅包含保留语义。编译器增加 20ms/40ms 的轻微显示余量并裁掉相邻字幕重叠。
- `subtitles` 必须是字幕列表或 `false`。`false` 会产生只含视频的可编辑草稿，不生成空文字轨、SRT 或剪后转写稿。
- 音效事件落在删除区间时默认报错。确认应随下一段保留内容开始时，可显式设置 `"snap": "next"`。
- `voice_volume` 是线性增益；先检查源音量与峰值，再决定是否调整，不机械套用 2 倍。
- 编译器只生成口播映射、普通字幕和白名单音效提示。输出转为 [headless-macos.md](headless-macos.md) 的多轨计划后，可以按用户需求加入本地 BGM、其他音效和 B-roll；无界面新计划的 build/verify 会核对这些内容。需在线账号资源时再使用原生 UI。

## 命令

脚本均位于 Skill 的 `scripts/`。下列 `WORK`、`SKILL` 表示本次工作目录和 Skill 目录；实际调用使用绝对路径，并保留每一版新文件。

```bash
python3 SKILL/scripts/prepare_source.py inspect --source SOURCE
python3 SKILL/scripts/prepare_source.py normalize --source SOURCE --out WORK/source-normalized.mp4
python3 SKILL/scripts/asr_once.py run --source SOURCE --ledger WORK/asr-ledger
python3 SKILL/scripts/asr_once.py adopt --source SOURCE --cache CACHE --expect-source-sha HASH --ledger WORK/asr-ledger
python3 SKILL/scripts/edit_plan.py compile --plan WORK/edit-plan.json --out WORK/compiled-v1
python3 SKILL/scripts/edit_plan.py render-audio --plan WORK/compiled-v1/compiled.json --out WORK/voice-v1.wav --work WORK/audio-render-v1
python3 SKILL/scripts/asr_once.py run --source WORK/voice-v1.wav --ledger WORK/asr-ledger
```

先运行 `inspect`。如果结果的 `compatible` 为 `false`，再运行 `normalize`；输出固定为 H.264 8-bit 4:2:0 与 AAC，保留原片，绝不覆盖已有文件。转码时不输出帧级进度，只保留错误；完成后仅输出一行 JSON 摘要。

`asr_once.py` 的一次请求记录以素材内容 hash 为键。完成结果复用；不明状态、旧 pending 文件或未经绑定的旧缓存会阻止新提交。默认没有 `force` 开关。恢复长任务应使用原执行器与已保存的 request ID；不要为了绕过状态另换 ledger 目录重提。

确认参数和删留决定后，用 `headless_draft.py from-compiled` 转换并无界面创建，命令见 [headless-macos.md](headless-macos.md)。本发行不包含旧版模板适配器 `native_draft.py`，不调用未分发的脚本。

ASR 执行器不随 Skill 分发。默认寻找当前用户目录的 `scripts/transcribe.py`，也可用 `YICHEN_ASR_EXECUTOR` 指定绝对路径；执行前确认其兼容本脚本所需参数，并有本次服务调用授权。没有转写依赖时可直接使用用户提供的同源时间戳计划。
