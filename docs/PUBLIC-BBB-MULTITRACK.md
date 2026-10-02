# 开放影片多轨复核案例

此案例用于 PR #22 的 App Store 11.4.0 build 481 新建草稿验收，替代维护者允许的
原 IG 案例。它使用真实动画画面和影片原声，并新增剪辑轨道；不是《Big Buck Bunny》
原始分层工程，也不是原 IG 工程。彩条边界测试继续保留。

## 素材与许可

来源为 Blender Foundation 官方公开的《Big Buck Bunny》影片：
[官方下载目录](https://download.blender.org/peach/bigbuckbunny_movies/)、
[官方许可说明](https://peach.blender.org/about/)、
[CC BY 3.0](https://creativecommons.org/licenses/by/3.0/)。

署名：**(c) copyright 2008, Blender Foundation / www.bigbuckbunny.org**。
本案例进行了影片切片、抽帧、原声拆分、变速、画中画与新增中文文字。
素材来自影片本身，不采用站点标志。分享本案例及生成素材时保留此署名、许可链接和修改说明。

公开测试材料由以下三部分组成，无需私人视频、官方程序文件或私人草稿：

- `tools/prepare_public_multitrack_case.py`：验证固定来源压缩包，再生成完整素材和可直接构建的本地计划；不联网、不登记草稿。
- `examples/public-bbb-multitrack.plan.json`：可阅读的相对路径计划，`ASSETS/` 为占位。实际命令使用生成的 `plan.json`。
- `docs/evidence/public-bbb-materials.json`：39 份素材的切片位置、生成参数、大小、SHA-256、完整来源与许可。

生成的 MP4 字节可能随 FFmpeg/libx264 版本不同而改变。manifest 中的素材哈希记录本次验收
文件；来源 ZIP 哈希必须一致。复核者应保留自己的生成 manifest，并由 build/verify 校验自己的素材。

## 覆盖关系

| 原方案规模或场景 | 替代案例 |
| --- | --- |
| 约 50 秒、23 轨、154 段 | 50.233333 秒、23 轨、154 段 |
| 39 份独立素材 | 24 个影片视频切片、8 张影片抽帧、7 个原声音频切片；来自同一开放影片，不代表 39 个独立拍摄来源 |
| 8 视频轨、38 视频/图片段 | 主轨 12 段，加 7 条画中画轨的 26 段；包括图片与素材复用 |
| 14 文字轨、109 文字段 | 原生可编辑中文文字，白色填充、黑色描边、不同位置和错开的时间区间 |
| 1 音频轨、7 段 | 独立原声 WAV，音量 0.7；不是复刻原片叙事或同步配音 |
| 变速、裁切、缩放和时间映射 | 0.75/1/1.5/2 倍速、源区间截取、画中画缩放与位移；无特效缓存依赖 |
| 字体和效果 | 使用系统字体；不宣称覆盖原案例的每种字体、特效或转场 |

变速主轨的前 11 段各 124 帧，末段 143 帧且正常速度；124 可被 4 整除，
0.75/1.5/2 倍的源区间也落在完整帧边界。首次等分微秒的测试在原生保存时发现速度重新计算，
被原有严格校验拒绝；本案例修正时间分配，没有放宽校验器。

## 复现

以下命令在仓库根目录执行。先确保已合法安装匹配的官方剪映及已固定的本地桥接组件。
使用新的工作目录；发布前保存工作并完全退出剪映。

```sh
mkdir -p work/public-bbb
curl --fail --location https://download.blender.org/peach/bigbuckbunny_movies/BigBuckBunny_640x360.m4v.zip --output work/public-bbb/source.zip
python3 tools/prepare_public_multitrack_case.py --source-zip work/public-bbb/source.zip --out work/public-bbb/case
python3 engine/jy14_headless.py doctor
python3 tools/build_native_codec.py --rebuild-check
python3 engine/jy14_headless.py build --plan work/public-bbb/case/plan.json --out work/public-bbb/build
python3 engine/jy14_headless.py verify-build --build work/public-bbb/build
python3 engine/jy14_headless.py publish --build work/public-bbb/build --audit work/public-bbb/publish
```

在剪映首页打开测试草稿，从头播放到 `00:00:50:07`，检查主画面、叠加画面、文字和音频电平。
选择文字确认内容框可编辑，临时添加字符再撤销；选择视频检查原生缩放与变速控件，选择
声音检查音量与波形。保存、完全退出，运行 `verify`。重新启动剪映，从首页冷重开同一草稿，
确认素材完整、轨道和时长保留，播放并保存退出，再执行：

```sh
python3 engine/jy14_headless.py verify --build work/public-bbb/build
python3 -m unittest discover -s tests -q
python3 tools/check_package.py
```

原始日志、首页索引、属性原值和本地绝对路径只留本机。公开报告只保留必要身份、结果、
哈希和匿名计数，参见 [本机验收报告](APPSTORE-1140-B481.md)。这仍属于贡献者单机报告；
维护端和其他机器需自行复核，不涉及已有草稿无界面修改或原生 MP4 导出兼容。
