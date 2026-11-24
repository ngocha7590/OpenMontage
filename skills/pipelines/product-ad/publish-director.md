# Publish Director — Product Ad Pipeline

Follow `skills/pipelines/cinematic/publish-director.md` with these additions:

- Poster frame: the packshot or the approved hero storyboard frame.
- Record in `publish_log` which image and video providers made the frames and clips, with their licence notes (e.g. gateway muse: "no clear licence for commercial use"), so the owner can judge commercial use.
- Title and description use only claims from the brief.

---

## Thumbnail And YouTube Export

Before packaging, make one thumbnail for the delivered video. Background: the packshot or approved hero frame, or a 16:9 image from the project's image provider. `upscale` if narrower than 1280 px. Use only claims from the brief in the headline. Compose with `thumbnail_compose` (3-6 word headline in the brief's language, `accent_words`, playbook `palette`, `layout` opposite the subject), check it at about 320 px wide, then pass `thumbnail_path`, `category_id` (default `"27"`) and `default_language` to `export_bundle` so `metadata/youtube.json` is written. Text is always drawn by code, never by the image model. Follow Step 3 of `skills/pipelines/explainer/publish-director.md` for the details.

## Gate Reminder (Binding)

This stage gates on human approval (`human_approval_default: true`). Checkpoint with `status="awaiting_human"`, present the export, and **END YOUR TURN**.
