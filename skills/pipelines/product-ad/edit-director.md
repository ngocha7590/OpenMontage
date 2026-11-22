# Edit Director — Product Ad Pipeline

Follow `skills/pipelines/cinematic/edit-director.md` with these product-ad rules:

- Trim each clip to its scene length (image-to-video clips are often fixed at ~5 s); keep the shot's event and cut on a settled frame, never mid-morph.
- The packshot (`packshot_still`) is the approved still with a restrained move (slow push-in or drift of 3–5 % over the shot, never fully static) and the copy composed on top: product name and `brief.cta`, in the space the storyboard reserved. Hold ≥ 2 s on the readable CTA.
- Joins: compatible lid/stand state, direction of movement and product scale between adjacent shots; use a deliberate cut when the state changes.
- Narration (if any) lines up with its shot; music lands its accent on the hero reveal or the packshot.
- No generated text inside clips is accepted as copy; all copy is overlay.

## End card copy

The end card is what the viewer remembers, so the product name is the headline, not a caption.

- **Hierarchy.** Show the product name first, then the CTA, then an optional small supporting line such as delivery or price. Use at most three text elements.
- **Size.** At delivery size the product name is at least 6 % of the frame height (≥ 64 px at 1080p, ≥ 115 px at 1920p vertical), in a bold or semibold weight. The CTA is about 60–70 % of the name's size. The supporting line is no smaller than 3 % of the frame height. A one-line name of about 30 px at 1080p fails.
- **Placement.** Put the copy in the empty side reserved by the storyboard, left-aligned or centred as a block, inside the 5 % title-safe margin. Neither the copy nor its panel may overlap the product silhouette. If the packshot leaves too little room, send the frame back to the storyboard instead of covering the product.
- **Motion of layers.** Apply the packshot push-in to the photo layer only. The copy and panel stay fixed in title-safe for the whole shot. The composition length equals the edit length, with no empty frames at the end.
- **Contrast.** Text against the background behind it has a contrast of at least 4.5:1. On pastel backgrounds use a dark tone of the brand colour for the text, or put the CTA on a solid pill or button. The packshot is a straight crop of a real photo, so the copy side may hold other objects. If it does, set the copy on a panel: a solid or frosted rounded rectangle (≥ 85 % opacity, or a backdrop blur of ≥ 24 px) that covers them. Do not add a vignette that darkens the product.
- **Motion.** The name enters first; the CTA follows about 0.3–0.5 s later. Use fades or short slides of ≤ 0.4 s. Everything is fully settled and readable for the last ≥ 2 s.
- **Check.** Export the last frame and view it at 25 % scale, roughly phone-feed size. The name and CTA must still read at that scale; if not, enlarge them.

Produce schema-valid `edit_decisions` covering the full runtime.
