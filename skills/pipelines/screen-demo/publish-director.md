# Publish Director - Screen Demo Pipeline

## When To Use

Package the finished demo so the user can publish it quickly and so the metadata reflects the actual task, result, and tools involved.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/publish_log.schema.json` | Artifact validation |
| Prior artifacts | `state.artifacts["compose"]["render_report"]`, `state.artifacts["idea"]["brief"]`, `state.artifacts["script"]["script"]` | Video, brief, and sections |
| Playbook | Active style playbook | Thumbnail and copy tone |

## Process

### 1. Build Searchable Metadata

Screen-demo titles work best when they combine:

- task,
- tool,
- outcome.

Good patterns:

- `How to deploy on Vercel from Next.js`
- `Fix CORS in React + Express`
- `Set up GitHub Actions for Python tests`

Pull keywords from:

- software names,
- frameworks,
- commands,
- exact error text,
- outcome words such as `deploy`, `fix`, `connect`, `publish`, `ship`.

### 2. Use Chapter Markers As Navigation

Use script sections as the basis for chapter markers and packaging bullets. A good screen-demo package makes the workflow skimmable before the user even presses play.

### 3. Thumbnail Strategy

If a thumbnail concept is needed, it should show:

- the result state, not a generic setup screen,
- the recognizable tool surface,
- 2-4 words of value text.

Store the concept in `publish_log.metadata.thumbnail_concepts`.

### 4. Package By Platform

Prepare:

- video file,
- title and description/caption,
- chapter markers where relevant,
- keyword list,
- thumbnail concept notes.

For developer or product-demo content, also package:

- commands shown,
- software/version mentions,
- error terms if it is a troubleshooting demo.

### 5. Quality Gate

- metadata names the real tool and task,
- chapters match the actual rendered flow,
- export folders are clean and reusable,
- copy is tailored to the platform instead of duplicated.

## Thumbnail And YouTube Export

Before packaging, make one thumbnail for the delivered video. Background: prefer a strong, well-lit frame from the source or the final render (extract with ffmpeg; face or key action clear, not mid-blink or mid-transition). AI image generation (`image_selector`) is optional. `upscale` if narrower than 1280 px. Compose with `thumbnail_compose` (3-6 word headline in the brief's language, `accent_words`, playbook `palette`, `layout` opposite the subject), check it at about 320 px wide, then pass `thumbnail_path`, `category_id` (default `"27"`) and `default_language` to `export_bundle` so `metadata/youtube.json` is written. Text is always drawn by code, never by the image model. Follow Step 3 of `skills/pipelines/explainer/publish-director.md` for the details.

## Common Pitfalls

- Publishing with generic titles that omit the actual software or task.
- Using the same caption for YouTube, LinkedIn, and short-form social.
- Building chapter markers from the script without checking the render.

---

## Gate Reminder (Binding)

This stage gates on human approval (`human_approval_default: true`). After review passes:
checkpoint with `status="awaiting_human"`, present the summary (the Backlot board renders
the artifact), and **END YOUR TURN**. Do not start the next stage in the same response.
Approval is per-gate — an earlier "go ahead" does not cover this gate.
