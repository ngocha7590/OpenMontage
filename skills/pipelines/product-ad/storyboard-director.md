# Storyboard Director — Product Ad Pipeline

## Goal

One reviewable keyframe per scene (plus an end frame for `first_last_frame` shots), generated from the real product photos, recorded in `artifacts/storyboard.json`. This is the cheap gate: the user fixes composition and identity here, before any video is paid for.

## Clean the references first

Models copy whatever is in the reference — including a generator watermark (e.g. the Gemini ✦ sparkle in a corner), stamps or UI overlays. If a product photo carries one, make a cleaned copy in `inputs/clean/` and pass that as `identity_references`. Paint the mark out from the neighbouring background with Pillow: mask the bright mark pixels, interpolate each row from the unmasked pixels, then a light blur. Do not crop away product parts. Look at the cleaned crop before using it. Never alter the product itself.

## Generate

```
storyboard_frames(
  scene_plan_path = "<project>/artifacts/scene_plan.json",
  workspace_path  = "<project>",
  treatment       = <treatment sentence from the proposal>,
  reference_note  = <metadata.commercial_product.reference_note>,
  aspect_ratio    = <delivery aspect, e.g. "9:16" or "16:9">,
  allowed_providers = [] if brief.image_provider == "auto" else [brief.image_provider],
)
```

- References default to `metadata.commercial_product.identity_references`, else every image in `inputs/` (max 4 are sent).
- The tool writes `assets/storyboard/<scene>_start.png` (and `_end.png`), keeps existing frames unless `overwrite`, and lists `failed[]` per scene — report failures, do not hide them.

## Packshot frame (`packshot_still`)

The packshot is held on screen for seconds at full delivery size, so it must be the sharpest frame in the ad. Build it from a real product photo; use a generated frame only when no usable photo exists.

1. **Source.** Use a real photo at native resolution. A generated frame below the delivery height (e.g. muse returns ~500 px wide) is never a packshot: upscaling it 2× or more makes it soft. If the only photo is a composite, crop one product from it.
2. **Crop.** Keep the whole product with a margin of about 10 % of its size on every side: all parts (both knobs, the stand/handle, the cable if it explains a feature) stay inside the crop. Do not cut through the product or its shadow. Exclude other products, devices and accessories from the composite.
3. **Canvas without seams.** Never paste a hard-edged crop onto a flat colour fill: the step at the crop edge shows as a line across the frame. Instead:
   - fill the canvas with the photo's own background: stretch or mirror the crop's edge strips outward, blur them heavily (radius ≥ 5 % of the canvas width), or use a gradient sampled from the crop's border pixels;
   - feather the crop into that fill with a soft mask (≥ 60 px at 1080p, or ~6 % of the crop width), so there is no hard edge;
   - match brightness and colour across the join.
4. **Layout.** Use the delivery aspect and size. The product fills about 45–60 % of the frame height and sits on one third. Keep the other side empty and calm for the product name and CTA, inside a 5 % title-safe margin. Upscale the crop by at most 1.5×; if the crop needs more, use a smaller product scale.
5. **Record.** Write the result to `assets/storyboard/<scene>_start.png` with `source: provided`. In `notes`, say which part of which photo was used and how the canvas was extended.

Check it at 100 % zoom before presenting:
- no visible vertical or horizontal edge, and no colour step;
- no product part cut off;
- the edges of the product are sharp, not upscaled mush;
- the copy area holds no detail that would fight the text.

## Review before presenting

Open every frame next to the product photos and check, per scene:
- silhouette and proportions; colour and transparency/finish;
- part count (knobs, buttons, ports, LED, earbuds) and closure state;
- label/logo layout; no generated text, captions or watermark; no stray devices;
- for `first_last_frame`: start and end differ **only** by the planned change;
- composition leaves space for copy where the plan says so.

Regenerate a failing scene yourself (`scenes=[id], overwrite=true, notes={id: "<defect>"}`) at most twice before presenting; the old frame is kept in `takes`.

## Gate

Checkpoint `storyboard` with `status="awaiting_human"`, list frames, provider, cost and any known weakness, and **END YOUR TURN**. Studio shows the frames in the Storyboard tab; the user comments per scene (chat messages carrying `scene` = 1-based scene number).

On a change request: map each note to its scene id, run `storyboard_frames(scenes=[…], overwrite=true, notes={scene_id: note})` for those scenes only, re-check, and present again.

On approval: run `storyboard_frames(workspace_path=…, approve="all")` so every frame is `approved`, then continue to assets.

---

## Gate Reminder (Binding)

This stage gates on human approval (`human_approval_default: true`). Approval is per gate — an earlier "go ahead" does not cover this gate.
