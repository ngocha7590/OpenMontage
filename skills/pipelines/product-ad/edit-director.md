# Edit Director — Product Ad Pipeline

Follow `skills/pipelines/cinematic/edit-director.md` with these product-ad rules:

- Trim each clip to its scene length (image-to-video clips are often fixed at ~5 s); keep the shot's event and cut on a settled frame, never mid-morph.
- The packshot (`packshot_still`) is the approved still with a restrained move (slow push-in or drift of 3–5 % over the shot, never fully static) and the copy composed on top: product name and `brief.cta`, in the space the storyboard reserved. Hold ≥ 2 s on the readable CTA.

## End card copy

The end card is what the viewer remembers, so the product name is the headline, not a caption.

- **Hierarchy.** Show the product name first, then the CTA, then an optional small supporting line such as delivery or price. Use at most three text elements.
- **Size.** At delivery size the product name is at least 6 % of the frame height (≥ 64 px at 1080p, ≥ 115 px at 1920p vertical), in a bold or semibold weight. The CTA is about 60–70 % of the name's size. The supporting line is no smaller than 3 % of the frame height. A one-line name of about 30 px at 1080p fails.
- **Placement.** Put the copy in the empty side reserved by the storyboard, left-aligned or centred as a block, inside the 5 % title-safe margin. Keep it away from the product silhouette.
- **Contrast.** Text against the background behind it has a contrast of at least 4.5:1. On pastel backgrounds use a dark tone of the brand colour for the text, or put the CTA on a solid pill or button. Do not add a vignette that darkens the product or the copy.
- **Motion.** The name enters first; the CTA follows about 0.3–0.5 s later. Use fades or short slides of ≤ 0.4 s. Everything is fully settled and readable for the last ≥ 2 s.
- **Check.** Export the last frame and view it at 25 % scale, roughly phone-feed size. The name and CTA must still read at that scale; if not, enlarge them.
- Joins: compatible lid/stand state, direction of movement and product scale between adjacent shots; use a deliberate cut when the state changes.
- Narration (if any) lines up with its shot; music lands its accent on the hero reveal or the packshot.
- No generated text inside clips is accepted as copy; all copy is overlay.

Produce schema-valid `edit_decisions` covering the full runtime.
