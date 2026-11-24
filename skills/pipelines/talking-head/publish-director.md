# Publish Director — Talking Head Pipeline

## When to Use

You have a render report with the final video. Your job is to prepare metadata, thumbnails, and an export package for publishing.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/publish_log.schema.json` | Artifact validation |
| Prior artifacts | Render report, Brief | Video file and context |

## Process

### Step 1: Generate Metadata

Create platform-specific metadata:
- **Title**: Based on the brief's title and hook
- **Description**: Summary of the content with relevant keywords
- **Tags**: Derived from brief's key_points
- **Chapters**: From script section timestamps

### Step 2: Thumbnail Concept

Describe or generate a thumbnail:
- Extract a compelling frame from the footage (if frame_sampler available)
- Add text overlay concept (title or key stat)

### Step 3: Package Export

Create the export directory:
- Video file
- Metadata JSON
- Description text file
- Chapter markers
- Thumbnail concept

### Step 4: Build Publish Log

Document the publish event with platform, status (draft), and export path.

### Step 5: Self-Evaluate

| Criterion | Question |
|-----------|----------|
| **Metadata quality** | Is the title compelling and description informative? |
| **Completeness** | Is the export package complete? |

### Step 6: Submit

Validate the publish_log against the schema and persist via checkpoint.

---

## Thumbnail And YouTube Export

Before packaging, make one thumbnail for the delivered video. Background: prefer a strong, well-lit frame from the source or the final render (extract with ffmpeg; face or key action clear, not mid-blink or mid-transition). AI image generation (`image_selector`) is optional. `upscale` if narrower than 1280 px. Compose with `thumbnail_compose` (3-6 word headline in the brief's language, `accent_words`, playbook `palette`, `layout` opposite the subject), check it at about 320 px wide, then pass `thumbnail_path`, `category_id` (default `"27"`) and `default_language` to `export_bundle` so `metadata/youtube.json` is written. Text is always drawn by code, never by the image model. Follow Step 3 of `skills/pipelines/explainer/publish-director.md` for the details.

## Gate Reminder (Binding)

This stage gates on human approval (`human_approval_default: true`). After review passes:
checkpoint with `status="awaiting_human"`, present the summary (the Backlot board renders
the artifact), and **END YOUR TURN**. Do not start the next stage in the same response.
Approval is per-gate — an earlier "go ahead" does not cover this gate.
