# Windows FFmpeg export

Windows uses an isolated FFmpeg renderer. It produces a verified MP4, not a
Jianying-native editable draft, and never imports the macOS codec, registration,
account or resource-cache modules.

## Requirements

- Python 3.9 or newer.
- FFmpeg and FFprobe containing H.264, AAC, overlay and drawtext support.
- An explicit local TTF, OTF or TTC file when the plan contains text.

Executables resolve from the command options first, the JIANYING_FFMPEG and
JIANYING_FFPROBE environment variables second, and PATH last.

## Build, verify and export

~~~powershell
python skills\yichen-jianying-edit\scripts\headless_draft.py doctor --backend windows-ffmpeg
python skills\yichen-jianying-edit\scripts\headless_draft.py build --backend windows-ffmpeg --plan D:\input\plan.json --out D:\jianying-headless\work\windows-build
python skills\yichen-jianying-edit\scripts\headless_draft.py verify-build --backend windows-ffmpeg --build D:\jianying-headless\work\windows-build
python skills\yichen-jianying-edit\scripts\headless_draft.py export --backend windows-ffmpeg --build D:\jianying-headless\work\windows-build --out D:\jianying-headless\work\windows-export --font C:\Windows\Fonts\msyh.ttc
~~~

The FFmpeg backend must be selected explicitly. Without `--backend windows-ffmpeg`,
the Windows `doctor`/`build`/`verify-build` commands target the native editable-draft
path, which requires a reviewed Jianying installation. This portable build stores a deterministic
render timeline and all media below the render directory; build.json binds the
plan, timeline and every resource by SHA-256. Absolute timeline media paths,
parent traversal, symlinks, unknown dependencies and post-build changes are
rejected. The supplied font is separately snapshotted into the export job and
recorded by name, size and SHA-256.

The renderer supports ordered video, overlay video, target-time gaps, cuts,
speed, original audio, separate audio tracks, volume, still inputs and basic
text. Audio is placed on the absolute target timeline, so silent clips do not
collapse later audio. Sequential and overlapping text tracks form one connected
filter chain. Native effects, filters, transitions, keyframes and editable
Windows Jianying drafts are explicitly unsupported.

Successful output retains render.mp4, result.json, the filter graph, FFprobe
evidence and FFmpeg/full-decode logs. The result asserts duration, dimensions,
frame rate, frame count, H.264/yuv420p output, complete decoding and an unchanged
source build. An exact frame-aligned timeline requires the exact frame count;
only a genuinely fractional duration accepts its adjacent integer counts.
The audio mix does not normalize against its silent timing bed, so an explicit
volume such as 0.5 retains its requested gain.

## Verification

~~~powershell
python tools\check_package.py
python -m unittest engine.test_windows_ffmpeg -v
python tools\windows_ci_evidence.py
~~~

The GitHub workflow uses a normal pull_request event with read-only permissions.
Its FFmpeg archive is selected by immutable release asset ID and verified by
SHA-256. Evidence uses the repository's reviewed, redistributable public-media
fixture; private drafts, account data, system fonts and source paths are not
uploaded. The public IG case also checks the rendered audio mean level against
its requested 0.5 gain and retains a path-free `audio-gain.json` report.
