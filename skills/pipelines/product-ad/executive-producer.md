# Executive Producer — Product Ad Pipeline

## When to Use

You run a **product commercial built from real product photos**: launch film, social ad, promo for one specific physical product (SKU). The user supplies product images (in `inputs/`) and a message. The defining rule of this pipeline: **no motion is generated before the user has approved a storyboard of keyframes made from those photos.**

Read `skills/creative/commercial-product-video.md` first — it is the craft guidance (identity vs. style references, invariants, shot roles, per-shot control, motion review). This pipeline turns it into stages with gates and two tools: `storyboard_frames` and `storyboard_animate`.

## Stages

`proposal → script → scene_plan → storyboard → assets → edit → compose → publish`

There is no research stage: the brief and the product photos are the source of truth. Do not invent specs, efficacy or features that the brief does not state.

| Stage | Gate (guided) | Director |
|---|---|---|
| proposal | yes | `proposal-director` |
| script | yes | `script-director` |
| scene_plan | no | `scene-director` |
| storyboard | **yes** | `storyboard-director` |
| assets | yes | `asset-director` |
| edit | no | `edit-director` |
| compose | no | `compose-director` |
| publish | yes | `publish-director` |

## Brief fields (from the Studio form)

| Field | Meaning |
|---|---|
| `product_name`, `message`, `audience`, `mood`, `cta` | Creative input. `cta` goes on the end card only. |
| `audio` | `voice_music` = narration + music; `music_only` = no narration (script still holds on-screen copy). |
| `language` | Narration / copy language. |
| `image_provider` | Provider for storyboard frames. `auto` = let the tool rank; otherwise **only** that provider. |
| `video_provider` | Provider for image-to-video. `auto` = let the tool rank; otherwise **only** that provider. |

When the user chose a specific provider, pass `allowed_providers: [<provider>]` and never switch silently. If it fails, stop at the current gate, report the concrete error and the options (retry, pick another provider, fix credentials) and wait.

## Cumulative State

```
EP_STATE:
  pipeline: product-ad
  product: {name, sku, identity_references[], invariants[], reference_note}
  providers: {image: <auto|id>, video: <auto|id>, video_clip_limit_s: <from tool info>}
  audio: voice_music | music_only
  budget_total_usd / budget_spent_usd
  storyboard: {frames: n, approved: n, retakes: n}
  clips: {made: n, failed: n, attempts: n}
```

## Cross-Stage Checks

- **After proposal:** every shot names its control (`image_to_video`, `first_last_frame`, `packshot_still`); costs are per shot including keyframes and retries; the chosen video provider's clip length is known (read `get_info()` of the provider; gateway muse ≈ 5 s per clip) and shot lengths respect it.
- **After scene_plan:** `metadata.commercial_product` exists with `identity_references`, `invariants` and one `shots[]` entry per scene.
- **Storyboard gate:** present the frames (Studio's Storyboard tab renders `artifacts/storyboard.json`), say which provider made them and what they cost, then END THE TURN. Approval is for the whole storyboard; per-scene notes arrive as chat messages with a `scene` number — regenerate only those scenes.
- **Before assets:** mark the approved frames: `storyboard_frames(workspace_path=…, approve="all")` (or the approved scene ids). `storyboard_animate` refuses unapproved frames.
- **After assets:** every non-packshot scene has a clip made from its approved frame. Text-to-video for a product shot is a governance violation in this pipeline.
- **Compose:** identity check at the delivery aspect ratio; CTA legible.

## Forbidden

- Generating clips before the storyboard gate is approved.
- Text-to-video for product shots, or silently replacing a planned motion shot with a still (only shots approved as `packshot_still` are stills).
- Letting a model render the CTA, price or label text — copy is composed in the edit.
- Using the raw supplied photo as the final packshot when it contains other objects, several angles, or a watermark — generate a clean packshot frame from it instead.
- Switching a user-chosen provider without asking.
