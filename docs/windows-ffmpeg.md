# Windows FFmpeg export

Windows export is an isolated renderer. It reads a verified `build`, copies
only build-owned media into a new work directory, and writes a playable
`render.mp4`. The result is not a Jianying-native editable draft.

Install an FFmpeg build containing `libx264`, AAC, `drawtext`, and `overlay`.
Executables are resolved in this order: `--ffmpeg`/`--ffprobe`,
`JIANYING_FFMPEG`/`JIANYING_FFPROBE`, then `PATH`.

```powershell
python skills\yichen-jianying-edit\scripts\headless_draft.py export `
  --backend windows-ffmpeg `
  --build D:\path\to\work\build `
  --out D:\path\to\work\windows-export `
  --ffmpeg C:\tools\ffmpeg\bin\ffmpeg.exe `
  --ffprobe C:\tools\ffmpeg\bin\ffprobe.exe
```

On Windows, omitting `--backend` selects `windows-ffmpeg`. On macOS the
existing `native` backend remains the default. The job retains
`filter_complex.txt`, FFmpeg/FFprobe logs, `ffprobe.json`, `render.mp4`, and
`result.json`; failures retain partial output.

The current renderer supports the main video track, cuts, concatenated
segments, video speed, original and separate audio, volume, audio speed,
multiple audio tracks, still images/GIF inputs, and basic text subtitles.
Text uses `--font` or the standard Chinese Windows fonts. Unsupported native
effect/filter tracks and native transitions are rejected explicitly.

```powershell
python -m unittest engine.test_windows_ffmpeg -v
python -m unittest discover -v
```
