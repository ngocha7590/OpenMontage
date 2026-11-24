# Publish Director - Localization Dub Pipeline

## When To Use

Package the completed localization outputs so downstream teams can find the right video, subtitle, and metadata bundle for each language without manual cleanup.

## Process

### 1. Package By Locale

Each language package should clearly separate:

- video output,
- subtitle files,
- transcript or approved script copy,
- review notes,
- metadata.

### 2. Keep Naming Precise

Recommended metadata keys:

- `locale`
- `language_name`
- `deliverable_mode`
- `subtitle_included`
- `review_owner`

### 3. Preserve Review Context

If a language output has pronunciation caveats, timing warnings, or missing lip sync, keep that note in the published package.

### 4. Quality Gate

- locale packages are clearly labeled,
- metadata matches the actual treatment,
- supporting text assets are present,
- warnings and review notes are not lost.

## Thumbnail And YouTube Export

Before packaging, make one thumbnail for the delivered video. Background: prefer a strong, well-lit frame from the source or the final render (extract with ffmpeg; face or key action clear, not mid-blink or mid-transition). AI image generation (`image_selector`) is optional. `upscale` if narrower than 1280 px. Compose with `thumbnail_compose` (3-6 word headline in the brief's language, `accent_words`, playbook `palette`, `layout` opposite the subject), check it at about 320 px wide, then pass `thumbnail_path`, `category_id` (default `"27"`) and `default_language` to `export_bundle` so `metadata/youtube.json` is written. Text is always drawn by code, never by the image model. Follow Step 3 of `skills/pipelines/explainer/publish-director.md` for the details. With several outputs, one thumbnail for the main/final output is enough; pass it to `export_bundle` for that output.

## Common Pitfalls

- Shipping localized videos without the matching subtitle or transcript files.
- Mixing audio-dub and subtitle-only variants under the same generic filename.
- Removing the QA notes that explain known issues.

---

## Gate Reminder (Binding)

This stage gates on human approval (`human_approval_default: true`). After review passes:
checkpoint with `status="awaiting_human"`, present the summary (the Backlot board renders
the artifact), and **END YOUR TURN**. Do not start the next stage in the same response.
Approval is per-gate — an earlier "go ahead" does not cover this gate.
