# Scene Director — Product Ad Pipeline

## Goal

A `scene_plan` that the storyboard and image-to-video tools can execute shot by shot.

## Scenes

One scene per approved shot, `type: "generated"`, with `description`, `start_seconds` / `end_seconds`, `shot_intent`, `narrative_role`, `hero_moment` and `shot_language` (`shot_size`, `lens_mm`, `camera_movement`, `lighting_key`, `depth_of_field`). Keep each scene within the video provider's clip length.

## `metadata.commercial_product` (required)

The scene schema rejects per-scene extra fields, so product controls live at the top level — exactly the shape in `skills/creative/commercial-product-video.md` §4, plus two fields the tools read:

```json
"metadata": {
  "commercial_product": {
    "sku": "Pink translucent AirPods Pro case with swivel ring",
    "identity_references": ["inputs/airpods-case-pink.png"],
    "reference_note": "one image showing six angles of the same case (closed, open with earbuds, side ports, ring stand)",
    "invariants": ["translucent pink shell", "side hinge knobs", "front status LED", "white inner case"],
    "shots": [
      {"scene_id": "hero", "control": "image_to_video", "depiction": "expressive",
       "reference_view": "closed case, top-left of the reference",
       "start_state": "closed case, three-quarter view on glass step",
       "allowed_change": "light sweep and slow push-in only",
       "acceptance": ["shell stays translucent pink", "two hinge knobs visible"]},
      {"scene_id": "open_lid", "control": "first_last_frame", "depiction": "literal",
       "start_state": "lid closed", "end_state": "lid open, two white earbuds visible",
       "allowed_change": "lid rotates open on its hinge", "acceptance": ["earbud count = 2"]},
      {"scene_id": "packshot", "control": "packshot_still",
       "start_state": "case centred, clean background, space on the right for copy"}
    ]
  }
}
```

- `reference_view` (optional) tells `storyboard_frames` which view of a composite reference to use.
- `use_provided` (optional, packshot only) = a clean supplied photo to use as-is. Do **not** set it when the photo has other objects, several angles or a watermark.
- `control` must be one of `image_to_video`, `first_last_frame`, `packshot_still`.

## Output

Schema-valid `scene_plan` with one `shots[]` entry per scene.
