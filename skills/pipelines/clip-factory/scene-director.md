# Scene Director - Clip Factory Pipeline

## When To Use

You are planning how each selected clip will be framed and packaged for its destination platform. This is where clip viability gets proven or disproven.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/scene_plan.schema.json` | Artifact validation |
| Prior artifacts | `state.artifacts["script"]["script"]`, `state.artifacts["idea"]["brief"]` | Selected clips and platform goals |
| Tools | `frame_sampler`, `scene_detect` | Visual checks and boundary inspection |
| Playbook | Active style playbook | Layout language and safe zones |

## Process

### 1. Choose The Right Frame For Each Clip

Do not default every clip to `9:16`.

Use:

- `9:16` when a face-first crop works,
- `1:1` when speaker plus context both matter,
- `16:9` when slides, demos, or multi-speaker width are essential.

**Horizontal source -> vertical (9:16) output: use `shorts_layout`** (one ffmpeg pass, finished 1080x1920 Shorts frame: big title on top, content box in the middle, burned captions below).

- Give every clip a hook `title` in the brief's language: 3-10 words, no clickbait filler, plus `accent_words` (the 1-2 phrases that carry the hook; multi-word phrases stay on one line).
- Pass the clip's SRT as `captions_srt`; do not burn captions again later (no second caption pass on these clips).
- `text_fx` (default `"pop"`; `"subtle"` = colour only, `"none"` = static): staggered title slide-up + gold marker bar under the accent words, word-by-word caption highlight (active word gold, numbers gold), 120 ms cue fades. Pass `words_json` (`[{word, start, end}]`, clip-relative seconds) whenever the transcriber produced word timestamps; otherwise the tool runs faster-whisper on the clip audio and aligns it to the SRT, falling back to character-proportional timing (`<output>.layout.json` records `word_timing.source`).
- `strategy`: `auto` (default; face-centred window for talking heads, per-shot content bbox otherwise). `bg`: `auto` (solid for near-black graphics, blurred source otherwise).
- Content box is 1080 x <=760, fixed per clip; zoom is capped so on-screen text/diagrams are never cut. Review a contact sheet and `<output>.layout.json` (zones, per-shot zoom) before moving on.
- Sources that are **already vertical** skip `shorts_layout`; use them as-is (or `auto_reframe` only to fix the aspect).

Other conversions (square, 4:5, or when no title/captions layout is wanted) use the `auto_reframe` tool; pick its `strategy` by source type:

- Talking heads / people: `face` (face-tracked crop).
- Graphics, slides, screen recordings, diagrams, wide shots (no faces): `content_aware`. It decides per shot: a crop window centred on the measured content when ALL of it fits the 9:16 window, otherwise `fit_blur` (full source width scaled onto a blurred, darkened copy of itself). `auto` (default) picks `face` if faces exist, else `content_aware`.
- Never centre-crop away on-screen text, numbers, labels or diagrams. Full-width fit over a blurred background is the fallback, not black bars.
- Captions and hook text go in the free area below (or above) the fitted content, inside the Shorts safe zone (keep ~12 % clear at the top, ~20 % at the bottom for platform UI). With `fit_blur`, pass `fit_y` around 0.35 to lift the content and leave room for captions; `fit_scale` 1.0-1.15 zooms slightly when the source has wide empty margins.
- The tool writes `<output>.reframe.json` with the per-shot decisions; record it in `scene_plan.metadata.crop_variants` and review a contact sheet of the output before moving on.

### 2. Plan First-Second Composition

For each clip, define:

- what the viewer sees on frame 1,
- where hook text can safely appear,
- where subtitles can live,
- whether the speaker needs a punch-in or whether the original framing is already good.

### 3. Standardize The Batch

Use the scene plan to lock series consistency:

- same top hook zone,
- same subtitle zone,
- same watermark / brand area,
- same lower-third logic.

### 4. Store Reframe Detail In Metadata

The schema is generic, so store richer layout notes in `scene_plan.metadata`.

Recommended metadata keys:

- `clip_layouts`
- `safe_zones`
- `crop_variants`
- `speaker_positions`
- `platform_variants`

### 5. Use Scenes To Represent Deliverables

Each scene should map to one clip variant or one clip family deliverable. Keep `description` human-readable and use `required_assets` for hook overlays, lower thirds, or branded frames.

### 6. Quality Gate

- every clip has a platform-aware framing plan,
- hook and subtitle zones do not collide,
- weak vertical crops are downgraded honestly,
- the batch will feel visually consistent when rendered together.

## Common Pitfalls

- Center-cropping a wide shot and calling it vertical optimization.
- Ignoring slide or screen-share content while focusing only on faces.
- Letting each clip invent its own layout.
- Forgetting that the first frame determines whether a viewer keeps watching.

---

## Gate Reminder (Binding)

This stage gates on human approval (`human_approval_default: true`). After review passes:
checkpoint with `status="awaiting_human"`, present the summary (the Backlot board renders
the artifact), and **END YOUR TURN**. Do not start the next stage in the same response.
Approval is per-gate — an earlier "go ahead" does not cover this gate.
