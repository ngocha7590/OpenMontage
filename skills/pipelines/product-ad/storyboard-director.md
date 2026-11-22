# Storyboard Director — Product Ad Pipeline

## Goal

One reviewable keyframe per scene (plus an end frame for `first_last_frame` shots), generated from the real product photos, recorded in `artifacts/storyboard.json`. This is the cheap gate: the user fixes composition and identity here, before any video is paid for.

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
