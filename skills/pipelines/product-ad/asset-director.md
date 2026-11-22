# Asset Director — Product Ad Pipeline

## Goal

Clips made **only** from approved storyboard frames, plus narration and music, registered in `asset_manifest`.

## 1. Clips — image-to-video

Confirm every frame you will animate is `approved` in `artifacts/storyboard.json` (the storyboard gate approval marks them; if not, run `storyboard_frames(approve="all")` only if the user did approve).

```
storyboard_animate(
  workspace_path = "<project>",
  allowed_providers = [] if brief.video_provider == "auto" else [brief.video_provider],
  max_attempts_per_scene = 2,
  budget_usd = <remaining budget for clips>,
)
```

- The tool calls `video_selector` with `operation: image_to_video`, the start frame as `reference_image_path`, the end frame as `last_image_path` for `first_last_frame` shots, and a **motion-only** prompt (camera, light, `allowed_change`). Pass `motion_prompts={scene_id: …}` when you need a sharper motion description — never re-describe the product.
- Clips land in `assets/video/<scene>.mp4`; `frames[].clip` records provider, model, cost and whether the end frame was used. A warning means the provider ignored the end frame — check that shot carefully.
- `packshot_still` scenes are skipped: the edit places the still with a restrained move.

## 2. Review each clip in motion

Use `skills/creative/video-understand-usage.md` / frame sampling at start, peak, end and around any contact or state change. Compare with the product photos (skill §5). Identity or literal-operation defects block the clip. Fix the smallest cause and re-run that scene only:

```
storyboard_animate(scenes=[id], overwrite=true, notes={id: "<defect>"}, motion_prompts={id: "<simpler motion>"})
```

Stop at the attempt cap or the budget; present the defect and options instead of looping. Never fall back to text-to-video or a still for a planned motion shot without the user's approval.

## 3. Audio

- `brief.audio = voice_music`: narration via `tts_selector` (language `brief.language`), music via the planned source.
- `music_only`: music only.

## 4. Manifest

Register every clip (`type: video`, `scene_id`), the packshot frame (`type: image`, `scene_id`), narration and music in `asset_manifest`; paths project-relative and present on disk.

---

## Gate Reminder (Binding)

This stage gates on human approval (`human_approval_default: true`). Present each clip with its provider, attempts and cost, then **END YOUR TURN**.
