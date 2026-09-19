# Local fonts for new drafts and Hypit handoff

New `jy14-headless-plan/v1` text segments accept an optional `font_path`:

```json
{"text":"Editable text","start_us":0,"duration_us":2000000,
 "font_path":"/absolute/path/font.otf"}
```

The builder sets both the material's `font_path` and its serialized
`content.styles[*].font.path` before the first encode. Omitting the parameter
keeps the original default; an invalid explicit parameter fails instead of
falling back. The per-project Hypit conversion should supply the actual font
file used by the selected completed build, not a CSS family name.

Existing ordinary text can use `jy14-edit-plan/v1`:

```json
{"op":"set_text_font","id":"EXISTING_TEXT_MATERIAL_ID","source":"/absolute/path/font.otf"}
```

Both entrypoints share `engine/native_fonts.py`. Standalone static OTF/TTF
files are parsed with fontTools and copied unchanged into
`Resources/headless-fonts/<sha256>.<format>`.
Source paths may pass through directory symlinks, such as macOS `/tmp`.
The font file itself must be a regular file, not a symlink.
Different source paths with identical bytes share a copied file while keeping
their plan bindings in the `font_assets` inventory. Fonts never become
video/audio media-library items. Verification uses the copied bytes, so deleting
the original source font after a successful build does not break the draft.
Export staging copies these dependencies and remaps both outer and serialized
paths, including the native relative/placeholder spellings.

Scope: one font file per text segment, ordinary local font identities, no
online font lookup. Font collections, variable fonts and registered/online
font replacement are rejected. The implementation does not rasterize text or
claim identical line wrapping, glyph fallback, CSS sizes or pixel output.
Runtime profiles, application/codec pins and retired-effect rejection remain
unchanged.

## Optional font parser

Only assigning a new local font requires the extra Python dependency. From the
project root, install it into a virtual environment and use that interpreter:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-fonts.txt
.venv/bin/python skills/yichen-jianying-edit/scripts/headless_draft.py --help
```

The pinned fontTools 4.60.2 supports the project's Python 3.9 minimum. Use this
interpreter for `build` or `edit build` plans that assign fonts. Missing fontTools
produces an installation error; no dependency is installed automatically.
Default-font builds, unrelated edits, and verification/export of completed
snapshots remain usable with the standard library alone.

The parser decodes the required font tables and outline containers; malformed structures
fail with the source path and underlying error. Historical internal sfnt
checksums and search hints are not admission gates. Copying and subsequent
snapshot verification still compare exact SHA-256 hashes. Font files are never
repaired, converted, registered with the system, or bundled with the source.
The native font engine remains responsible for interpreting glyph drawing
instructions and rendering; parsing is not a native-render acceptance test.

These restrictions apply when explicitly assigning a font. Unrelated edits
preserve existing font files and each style's identity and binding, including
empty styles and font formats outside the new-font input scope. Discovery hashes
each existing file once, regardless of caption count; snapshot verification
checks the copied bytes. The existing isolated-export resource limits still apply.

For edits, the font inventory follows the final material and style references.
Fully replaced fonts remain in the source snapshot but are no longer export
dependencies; a missing old font can be replaced if no references to it remain.
Fonts still referenced after editing retain the existing file and export checks.
Repeated assignments of the same source share one parse within each new-build
or edit plan. The new-build validator passes its font inventory to copying and
timeline binding; every material's binding and the bytes copied into the draft
are still checked.

Older build records without `font_assets` keep their original file/resource and
structure verification. A present inventory, including an empty list, is checked
strictly. Plans using `font_path` or `set_text_font` require the inventory. Legacy
records are not rewritten and do not gain new isolated-export resource support.

## Verification

```sh
.venv/bin/python engine/test_native_fonts.py --font /absolute/path/font.otf --work /fresh/work/font-tests
python3 -m unittest discover -s tests -v
python3 tools/check_package.py
```

The focused suite uses a real supplied font for parsing, hashing and copying.
Build and live-verification fixtures mock the codec, media probe and cover
generation; they are **not native drafts** and must never be published.
It covers direct creation, default compatibility, rejection of invalid fonts,
copy deduplication, loss of the original font, binding drift, export staging,
unrelated edits of existing text and native path spelling normalization. New-build
regressions cover one parse per source and changed bytes before copying. Edit
regressions cover replaced and still-referenced fonts, missing old files, nested
timelines, per-plan parse reuse, and source changes before copying.
Additional regressions cover legacy edit evidence with draft-local fonts,
missing/invalid new inventories, static macOS system fonts, truncated outlines,
variable fonts, and complete standard-library-only subprocess workflows.
Directory-alias regressions cover new builds and font edits, copy deduplication,
and verification/export staging after removing the input font.
Platform-font cases report a skip if their required local fixture is unavailable.
No font or private project is bundled.

### Native acceptance (2026-09-20)

A separate acceptance checkout of this font patch on base `bbc46d3` was tested
on Apple Silicon, macOS 26.0 and Jianying 11.5.0. The codec was compiled from
unchanged upstream C++/header sources with Apple clang 16.0.0 and SDK 15.0;
its SHA-256 was
`ac202d8d28f15a4c4d512c7bdefa6e961320a6718407bbedc1338cb6ce62c0b0`.
That checkout recorded the local codec identity and corresponding source pins.
Application identity, deep signature verification, resource checks and export
sandbox remained enabled. The font implementation and native export C++ were
unchanged. This is evidence for that local build, not a run of the upstream
pinned codec binary. Codec/toolchain changes are not part of this font patch.

- Real native builds and homepage registration succeeded for creation and
  `set_text_font`, using Monaco static TTF and Source Han Serif CN Light static
  CFF OTF, with a default-font control.
- The operator confirmed distinct fonts, editable text, save, reopen and full
  editor exit for both test drafts. Post-save checks preserved text, material
  and rich-text font bindings, font bytes and all four active mirrors.
- The original input font paths were removed before both creation and font-swap
  exports. Draft-owned copies remained sufficient for validation and rendering.
- Creation, font swap, a Chinese CFF comparison, and a fresh snapshot of the
  UI-saved edited draft each exported 3 seconds at 1280x720 / 30 fps: exactly
  90 frames, with full decoding successful. The saved-draft export's decoded
  video frames matched the pre-save font-swap export across all 90 frames.

Full new-draft readback passed. Full edited-draft readback did **not** pass:
Jianying omitted default `speed: 1`, which the existing `native_edit.preserved()`
reported as `Preserved field disappeared: /materials/speeds/<id>/speed`.
That function is identical in base `bbc46d3` and this font patch, and the
before/after speed nodes independently reproduce the failure. Font-specific
post-save checks passed; this is not evidence that every verification entrypoint
passed. Both live test drafts were saved, so the older edit record's separate
source-unchanged precondition also became stale. No records or live data were
rewritten to suppress either check; the subsequent render used a fresh snapshot.

Default speed/FPS readback compatibility and the separate 11.5.0 X-only
keyframe/static-Y export issue are reserved for a compatibility fix PR. Neither
fix is included here or used to claim success for the font acceptance above.
The newer permanent local installation contains those fixes; its combined test
results are not attributed to this font-only patch. Different machines, editor
versions, fonts and real projects still require their own acceptance checks.
