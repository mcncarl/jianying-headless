# Token-efficient talking-head workflow

## Problem

Two real five-minute iPhone talking-head edits exposed a cost mismatch. Semantic editing benefits from model judgment, but subtitle generation, source compatibility checks, and verbose media progress are deterministic work. Repeating those tasks in an agent loop spends context on capabilities already available cheaply in the editor.

One tested workflow also required temporary subtitles because `edit_plan.py` rejected subtitle-free plans. The subtitles were compiled only to satisfy validation, converted into a text track, and then removed before the native draft was built. That workaround was correct but wasteful.

## Product boundary

Use the model for decisions that require meaning:

- choose complete takes over false starts and repeated takes;
- preserve qualifications, figures, product names, and core claims;
- review the edited transcript and ambiguous cuts;
- decide when a draft is ready for native acceptance.

Use deterministic local tools for mechanical work:

- inspect and normalize unsupported iPhone media without modifying the original;
- compile source-to-timeline ranges;
- render review audio;
- build and verify the editable draft;
- report compact structured results.

Use Jianying's own speech-to-text when the user chooses it. In the observed workflow, the user paid CNY 15.8 for one month of Jianying SVIP. This is a personal purchase price, not a stable market-price claim. For 10 to 20 videos per day, delegating mature caption recognition to the editor is cheaper than repeatedly asking an agent to carry caption text and track structure through every step.

## Chosen scope

### Optional subtitles

`jianying-edit-plan/v1` accepts either a subtitle list or `false`. A subtitle-free compiled plan remains valid, can render review audio, and converts to a video-only headless plan. Existing plans with subtitle lists remain compatible.

### Source preparation

Add a deterministic `prepare_source.py` command with two operations:

- `inspect` returns a compact JSON compatibility report;
- `normalize` creates a new 1080x1920 or 1920x1080 H.264/AAC file only when needed or explicitly requested.

The command never replaces or deletes the input. It rejects ambiguous multi-video inputs and reports rotation, codec, pixel format, dimensions, duration, and the output fingerprint.

### Compact agent-facing output

The helper scripts continue to emit concise JSON on success. Media conversion runs FFmpeg with error-only logging; failures return the relevant error instead of frame-by-frame progress. Documentation instructs agents to keep verbose progress out of the model context.

### Case study

Add a Chinese postmortem describing the two real edits, the token-cost problem, the Jianying SVIP division of labor, the discovered implementation gaps, and the changes in this pull request. It must distinguish observed facts, estimates, and the user's personal purchase price.

## Error handling

- Subtitle mode must be explicit through a list or `false`; malformed values fail.
- Subtitle-free plans must not generate SRT or transcript artifacts and must not create an empty text track.
- Source normalization writes only to a new path and refuses an existing output.
- Unknown codecs, missing audio, multiple video streams, invalid rotation, and unsupported dimensions fail with actionable messages.
- Existing subtitle workflows and native verification invariants remain unchanged.

## Verification

- Unit tests cover captioned and subtitle-free compilation.
- Unit tests cover video-only conversion from compiled plans.
- Unit tests cover compatible-source inspection and normalization command construction.
- Existing package, engine, and smoke tests remain green.
- The two observed workflows provide the behavioral evidence; private source media and private transcripts are not added to the repository.

## Out of scope

- automatic semantic keep/delete decisions;
- batch publishing 10 to 20 live drafts;
- automatic use of Jianying SVIP or account-bound online services;
- pricing guarantees;
- background music, effects, or new visual templates.
