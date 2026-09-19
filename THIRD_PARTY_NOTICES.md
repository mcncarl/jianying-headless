# Third-party notices and provenance

This private preview preserves provenance. It is not a claim that all files,
schemas, techniques, external libraries, or generated videos are original to
this project or covered by one license.

The project's original contributions are subject to the Personal Learning and
Non-Commercial Use License in [LICENSE](LICENSE). Existing third-party notices
and permissions remain in effect for their respective portions; this project's
custom license does not relicense or restrict the upstream MIT / Apache-2.0 materials.

## pyJianYingDraft 0.3.0 — Apache-2.0

- Upstream: <https://github.com/GuanYixuan/pyJianYingDraft>
- Exact version license: <https://github.com/GuanYixuan/pyJianYingDraft/blob/0.3.0/LICENSE>
- Copyright 2024 Gary Guan.
- Full retained text: [licenses/pyJianYingDraft-0.3.0-Apache-2.0.txt](licenses/pyJianYingDraft-0.3.0-Apache-2.0.txt).

Earlier 11.x project adapters directly used this package to construct text
objects and inspect media. Those historical adapters and the installed package
are not included in this preview. The selected current engine does not directly
import pyJianYingDraft; that observation is not a clean-room originality claim.
The project history and reference relationship remain acknowledged here.

## jy-draftc macOS sample — MIT

- Upstream: <https://github.com/wenshui330/jy-draftc>
- Source interface: <https://github.com/wenshui330/jy-draftc/blob/main/jy-draftc-mac/jy-draftc-mac/EncryptUtil.h>
- Copyright 2026 wenshui330.
- Full retained text: [licenses/jy-draftc-MIT.txt](licenses/jy-draftc-MIT.txt).

`bridge/EncryptUtil.h` adapts the sample's method declarations. The project
codec implements bounded file/pipe handling around those declarations. The
underlying encryption/decryption implementation is supplied by the separately
installed Jianying application; it is not implemented or redistributed here.
The MIT notice is retained; this release does not remove attribution by
renaming the adapter or mechanically rewriting code.

## Existing project implementation and native structure captures

The current timeline builder, editing/export wrappers, tests and speech-plan
helpers were developed in this project's local workflow. `bridge/runtime_io.py`
is a mechanically selected subset of the project's prior native IO helper;
its source hash and included definitions are recorded in
`bridge/SOURCE_MANIFEST.json`. No independent clean-room provenance is claimed.

`engine/blueprint.json`, `compound-blueprint.json`, and the resource catalog
contain sanitized, experimentally observed native structures and identities.
They are compatibility data, not a distribution of the editor, original source
media, native effect packages, fonts, bookmarks, or account data. Home-relative
paths replace the capture machine's personal directory. Native resource IDs,
hashes and observed usage restrictions remain intact.

## Jianying Professional — proprietary external runtime

Users must separately install the exact supported application from an
authorized source. This repository does not contain the application,
`libvideoeditor.dylib`, its built-in resources, or an application installer.
Calling internal interfaces is not equivalent to using a documented official
SDK or obtaining integration permission.

- User agreement: <https://lv.ulikecam.com/clause/agreement/pro>
- Material license: <https://lf9-cdn-tos.draftstatic.com/obj/ies-hotsoon-draft/faceu/Commercial_Material_User_Agreement_Cn.html>

Licensing project code does not grant rights to the editor, override the user
agreement, or establish commercial/redistribution rights for native materials.
Private storage and successful rendering are not legal clearance. Native
material availability, account entitlement, permitted use, and integration
authorization must be assessed separately before any relevant use or release.

## FFmpeg / ffprobe — separately installed command-line tools

- Upstream and licensing: <https://ffmpeg.org/legal.html>

These executables are invoked for media inspection, audio analysis and output
validation; their binaries are not distributed in this repository. Their
applicable license depends on how they were built. The development environment
used a GPL-enabled FFmpeg build. If distributing binaries or changing the
integration approach, review the actual build and its separate obligations.

## fontTools 4.60.2 — MIT, optional external dependency

- Upstream: <https://github.com/fonttools/fonttools>
- Exact version license: <https://github.com/fonttools/fonttools/blob/4.60.2/LICENSE>
- Package: <https://pypi.org/project/fonttools/4.60.2/>

Explicit local-font assignment uses fontTools to parse font metadata and
tables. Install it separately using [requirements-fonts.txt](requirements-fonts.txt);
its package, font files and binaries are not included in this source preview.
Default builds and existing snapshot verification do not require the parser.
The project license does not replace the dependency's MIT license or grant rights
to font files supplied by users.

## Python, macOS and Xcode toolchain

Python and Apple's developer tools are external prerequisites, not bundled
assets. Their own terms continue to apply. Optional ASR executors and services
are also separate dependencies; this repository contains no service key and
does not grant service access or paid inference permission.
