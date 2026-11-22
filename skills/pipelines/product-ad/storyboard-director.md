# Storyboard Director — Product Ad Pipeline

## Goal

One reviewable keyframe per scene (plus an end frame for `first_last_frame` shots), generated from the real product photos, recorded in `artifacts/storyboard.json`. This is the cheap gate: the user fixes composition and identity here, before any video is paid for.

## Clean the references first

Models copy whatever is in the reference — including a generator watermark (e.g. the Gemini ✦ sparkle in a corner), stamps or UI overlays. If a product photo carries one, **crop it away** instead of painting it out: cut off the strip at the right and/or bottom edge that holds the mark, save the result to `inputs/clean/<name>.png`, and pass that as `identity_references`. Open the cropped corner at 100 % to confirm the mark is gone. Never alter the product itself; if the crop would cut a product view, accept losing that view.

Generated frames can still copy a mark into the same corner. Ask for plain background in the outer ~10 % on the right and bottom (add it to `notes`), then crop exactly 10 % off the right and 10 % off the bottom of every generated frame. Cropping both sides by the same fraction keeps the aspect ratio. Record the crop in the frame's `notes`.

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

The packshot is held on screen for seconds at full delivery size, so it must be the sharpest frame in the ad. Make it a **straight crop of a real product photo** at the delivery aspect. Do not composite: no pasting a cut-out onto a filled canvas, no extended background, no feathered masks. Those produce seams and cut-off edges.

1. **Source.** Use a real photo at native resolution (the cleaned copy when the original has a watermark). A generated frame below the delivery height (e.g. muse returns ~500 px wide) is never a packshot.
2. **Window.** Choose one rectangle at the exact delivery aspect (16:9, 9:16, 1:1) around one product. Choose it so that:
   - the whole product is inside, with its base or shadow and all its parts (knobs, stand or handle, cable if it explains a feature); no edge of the window cuts through the product;
   - the product fills about 45–60 % of the window height and sits on one third, so that at least 40 % of the window width on the other side is free of the product. The copy panel goes there and must not cover any part of the product;
   - prefer the side that is calmest in the photo for the copy.
   Other objects from a composite may sit at the window's edges. The copy panel or a soft vignette can cover them; never paint them out.
3. **Scale.** Resize the window to the delivery size with a single resize, upscaling at most 1.5×. If the window is smaller than 1/1.5 of the delivery size, choose a larger window with a smaller product share.
4. **Record.** Save to `assets/storyboard/<scene>_start.png` with `source: provided`. In `notes`, give the photo, the window box `(x0, y0, x1, y1)` and the upscale factor. Measure the product box on the output with code and give its height as a % of the frame.

Before presenting, check at 100 %: no product part cut by the frame edge, no upscale blur, and a copy side with no detail that would fight the text.

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
