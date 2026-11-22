# Script Director — Product Ad Pipeline

## Goal

A `script` whose sections map one-to-one to the approved shots.

## Rules

- **Narration** only when `brief.audio = voice_music`. Pace ~2.3 words/second (Vietnamese ~3 syllables/second); leave the hero reveal and the packshot breathing room. For `music_only`, keep sections with empty narration text and put the on-screen copy in the section notes.
- Every claim must come from the brief (`message`, `product_name`, `cta`). No invented specs, materials, certifications or efficacy.
- On-screen copy is short (≤ 6 words per line) and lives on the packshot/end card; the CTA (`brief.cta`) appears once, at the end, long enough to read (≥ 2 s).
- Language: `brief.language`.

## Output

Schema-valid `script`; one section per shot in order, each naming its shot role.

---

## Gate Reminder (Binding)

This stage gates on human approval (`human_approval_default: true`). After review passes: checkpoint with `status="awaiting_human"`, present the summary, and **END YOUR TURN**.
