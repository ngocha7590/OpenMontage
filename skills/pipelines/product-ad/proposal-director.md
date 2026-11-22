# Proposal Director — Product Ad Pipeline

## Goal

Produce a `proposal_packet` the user can approve before any money is spent: the product as you understand it, a visual treatment, a shot list with a generation control per shot, providers, and a per-shot cost.

Read `skills/creative/commercial-product-video.md` §1–3 before writing.

## 1. Inventory the product photos

Open every file in `inputs/` (and `source_media_review` if present). For each image note:
- what it shows (which angles, open/closed states, accessories),
- whether it is a **composite** (several angles in one image), contains **other objects** (phone, charger, props) or a **watermark**,
- what it cannot show (missing back view, inside, mechanism).

Write the **invariants**: silhouette, proportions, colour, transparency/finish, part count (knobs, buttons, ports, LED), closure, label/logo layout. Write the **creative variables** separately (setting, light, camera, effects).

If the brief asks for something the photos cannot support (e.g. a mechanism nobody photographed), say so and propose a treatment using the known views.

## 2. Treatment and shots

One treatment sentence that describes an observable visual event and the material (e.g. "a soft light sweep travels across the translucent pink shell on pale glass steps"). Then 3–5 shots for 15–30 s (more only for longer ads), each with:

| Field | Notes |
|---|---|
| role | hero / detail / use / effect / packshot (skill §2) |
| what happens | one event per shot |
| control | `image_to_video` (default), `first_last_frame` (the shot changes state: lid opens, stand folds out), `packshot_still` (end card) |
| duration | ≤ the video provider's clip length; split longer beats into two shots |
| reference view | which view of the photos anchors the shot |

## 3. Providers and cost

- Image provider for keyframes: `brief.image_provider` (`auto` → say "auto: best available with reference images").
- Video provider for image-to-video: `brief.video_provider`. Read its `get_info()` for clip length, end-frame support (`last_image_path` / `first_last_frame`), cost and licence note, and put them in the proposal. If the provider has no end-frame input, `first_last_frame` shots will use only the start frame — say so.
- Cost per shot = keyframe(s) + clip × expected attempts (default cap 2). Sum must fit the budget.
- Licence: carry the provider's `license_note` (e.g. muse: "no clear licence for commercial use") into the proposal so the user decides knowingly.

## 4. Render runtime (required field — `render_runtime`)

Follow `AGENT_GUIDE.md` → "Present Both Composition Runtimes (HARD RULE)" and `skills/meta/animation-runtime-selector.md`:

1. Query `video_compose.get_info()["render_engines"]`.
2. If both `remotion` and `hyperframes` are available, **present both** with a line on fit for this ad:
   - **Remotion** — clip-led cut: `<OffthreadVideo>` per approved clip, packshot still with a restrained move, copy overlay on the end card.
   - **HyperFrames** — when the end card or copy is typographic/kinetic (GSAP title motion, registry transitions).
3. Recommend one, wait for approval, then write `render_runtime` into `proposal_packet.production_plan` and log a `render_runtime_selection` decision with both runtimes in `options_considered`.

Motion is required in this pipeline: a silent downgrade to a Ken Burns or still-led cut is forbidden.

## 5. Audio

`brief.audio = voice_music` → narration + music; `music_only` → no narration. State the music source.

## Output

Schema-valid `proposal_packet` with the delivery promise `motion_required: true`, the shot list in the concept, the provider choices and per-shot cost. Product notes may go in its `metadata.commercial_product`.

---

## Gate Reminder (Binding)

This stage gates on human approval (`human_approval_default: true`). After review passes: checkpoint with `status="awaiting_human"`, present the summary, and **END YOUR TURN**.
