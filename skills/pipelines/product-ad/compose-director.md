# Compose Director — Product Ad Pipeline

Follow `skills/pipelines/cinematic/compose-director.md` (ffprobe validation, `final_review`).

## Runtime routing

Read `edit_decisions.render_runtime` (locked at proposal) and pass `proposal_packet` to `video_compose.execute()` so `runtime_swap_detected` can compare against `proposal_packet.production_plan.render_runtime`:

- **`render_runtime="remotion"`** — default: approved clips via `<OffthreadVideo>`, packshot still, copy overlay.
- **`render_runtime="hyperframes"`** — kinetic/typographic end card or GSAP transitions; `hyperframes check` must pass before render (`skills/core/hyperframes.md`).

If the locked runtime is unavailable, escalate to the user — never substitute.

## Product-ad checks

- Review at the **delivery aspect ratio**, not the source frames: the product must stay recognisable after crop, grade, transitions and compression (`skills/creative/commercial-product-video.md` §6 rubric — identity and literal-operation failures are blocking).
- The end card's product name and CTA are legible at delivery size and on screen long enough to read.
- Grade must not shift the product colour away from the reference photos.

Produce `render_report` and `final_review`.
