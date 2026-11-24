# Publish Director - Avatar Spokesperson Pipeline

## When To Use

Package the finished spokesperson outputs for delivery. This stage should make it obvious which file is the hero cut, which are derivatives, and what message or audience each version serves.

## Process

### 1. Label Deliverables Clearly

Distinguish:

- hero cut,
- vertical cutdown,
- square cutdown,
- language variants,
- watermark or review versions.

### 2. Keep Metadata Message-Led

Recommended metadata keys:

- `audience_segment`
- `cta_copy`
- `offer_name`
- `locale`
- `thumbnail_concept`

### 3. Package Review Notes

If the avatar path has limitations such as visible lip-sync risk, retain that note in the package instead of hiding it.

### 4. Quality Gate

- exports are clearly named,
- metadata matches the intended message,
- poster frame or thumbnail concept features the presenter cleanly,
- review notes stay attached to the package.

## Thumbnail And YouTube Export

Before packaging, make one thumbnail for the delivered video. Background: prefer a strong, well-lit frame from the source or the final render (extract with ffmpeg; face or key action clear, not mid-blink or mid-transition). AI image generation (`image_selector`) is optional. `upscale` if narrower than 1280 px. Compose with `thumbnail_compose` (3-6 word headline in the brief's language, `accent_words`, playbook `palette`, `layout` opposite the subject), check it at about 320 px wide, then pass `thumbnail_path`, `category_id` (default `"27"`) and `default_language` to `export_bundle` so `metadata/youtube.json` is written. Text is always drawn by code, never by the image model. Follow Step 3 of `skills/pipelines/explainer/publish-director.md` for the details. Use the presenter cleanly; no unapproved likeness changes.

## Common Pitfalls

- Mixing hero and derivative exports without clear naming.
- Reusing generic metadata that ignores the spokesperson offer.
- Dropping risk notes that matter for downstream publishing teams.

---

## Gate Reminder (Binding)

This stage gates on human approval (`human_approval_default: true`). After review passes:
checkpoint with `status="awaiting_human"`, present the summary (the Backlot board renders
the artifact), and **END YOUR TURN**. Do not start the next stage in the same response.
Approval is per-gate — an earlier "go ahead" does not cover this gate.
