// Backlot project board — renders BoardState and stays live via SSE.

import {
  el, fmtAgo, fmtClock, fmtDuration, fmtMoney,
  getJSON, mediaURL, subscribe, thumbURL, waveBars,
} from "/ui/lib.js";

const rawProjectPath = location.pathname.split("/p/")[1] || "";
const projectId = decodeURIComponent(rawProjectPath);
const encodedProjectId = encodeURIComponent(projectId);
const app = document.getElementById("app");
const modal = document.getElementById("modal");
const player = document.getElementById("player");

let state = null;
let selectedStage = null;   // stage drawer open for this stage name
let activeRender = 0;
let replay = null;          // {t0, t1, t, playing} — replay mode when non-null
let firstPaint = true;
let activeTab = "overview"; // ds tabs: overview | script | storyboard | media | video
let mediaKind = null;       // media tab filter
let asideView = "feed";     // aside: feed | log

// docs/design is dark-only — the board always renders the dark design.
document.documentElement.dataset.theme = "dark";

// ---------------------------------------------------------------------------
// header slate
// ---------------------------------------------------------------------------

const STATUS_BADGE = {
  run: { label: "Đang chạy", c: "#6EA8FF", bg: "rgba(110,168,255,0.14)" },
  wait: { label: "Chờ bạn duyệt", c: "#F5B544", bg: "rgba(245,181,68,0.14)" },
  queue: { label: "Xếp hàng", c: "#B3BAC6", bg: "rgba(179,186,198,0.14)" },
  done: { label: "Xong", c: "#4CC38A", bg: "rgba(76,195,138,0.14)" },
  fail: { label: "Lỗi", c: "#FF6B6B", bg: "rgba(255,107,107,0.14)" },
  stall: { label: "Im lặng", c: "#FF6B6B", bg: "rgba(255,107,107,0.14)" },
  idle: { label: "Đang nghỉ", c: "#9AA3B2", bg: "rgba(154,163,178,0.12)" },
};

function boardStatus(s) {
  const run = s.studio_run;
  const declared = s.stages.filter((x) => !x.undeclared);
  if ((run && run.status === "waiting_approval") || s.stages.some((x) => x.status === "awaiting_human")) return "wait";
  if (run && run.status === "running") return run.stalled_minutes ? "stall" : "run";
  if (run && run.status === "queued") return "queue";
  const inProgress = s.stages.some((x) => x.status === "in_progress");
  if (run && run.status === "failed" && !inProgress) return "fail";
  if (s.stages.some((x) => x.stalled)) return "stall";
  if (inProgress || s.live) return "run";
  if ((declared.length && declared.every((x) => x.status === "completed")) || (run && run.status === "succeeded")) return "done";
  return "idle";
}

function statusBadge(key, id) {
  const b = STATUS_BADGE[key] || STATUS_BADGE.idle;
  const badge = el("span", { class: "ds-badge", style: `color:${b.c};background:${b.bg}` }, el("i", {}), b.label);
  if (id) badge.id = id;
  return badge;
}

// Status badge in the aside header — stable id so a status-only update can
// swap it in place without rebuilding the board (see refresh()).
function renderLivePill(s) {
  return statusBadge(boardStatus(s), "live-pill");
}

const LOGO_SVG = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="9" width="18" height="12" rx="2"></rect><path d="M3 9l3-6h14l-2 6"></path><path d="M9 3l-2 6M15 3l-2 6"></path></svg>';

function navIcon(path) {
  const span = el("span", { "aria-hidden": "true", style: "display:flex" });
  span.innerHTML = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${path}</svg>`;
  return span;
}

function studioHome(run) {
  try { return run && run.studio_url ? `${new URL(run.studio_url).origin}/projects` : null; } catch { return null; }
}

function renderSide(s) {
  const run = s.studio_run;
  const logo = el("span", { class: "ds-logo" });
  logo.innerHTML = LOGO_SVG;
  const home = studioHome(run);
  const libHref = s.series ? `/?${new URLSearchParams({ series: s.series })}` : "/";
  return el("nav", { class: "ds-side", "aria-label": "Điều hướng chính" },
    el("a", { class: "ds-brand", href: "/" }, logo,
      el("span", { class: "ds-brand-name" }, el("b", {}, "OpenMontage"), el("span", {}, "Backlot"))),
    el("a", { class: "ds-nav-link", href: libHref },
      navIcon('<rect x="3" y="3" width="7" height="7" rx="1.5"></rect><rect x="14" y="3" width="7" height="7" rx="1.5"></rect><rect x="3" y="14" width="7" height="7" rx="1.5"></rect><rect x="14" y="14" width="7" height="7" rx="1.5"></rect>'),
      el("span", { class: "ds-nav-text" }, s.series ? `Thư viện · ${s.series}` : "Thư viện")),
    el("a", { class: "ds-nav-link", href: location.pathname, "aria-current": "page", title: s.title },
      navIcon('<rect x="2" y="5" width="20" height="14" rx="2"></rect><path d="M10 9l5 3-5 3z"></path>'),
      el("span", { class: "ds-nav-text" }, s.title)),
    home ? el("a", { class: "ds-nav-link", href: home, target: "_blank", rel: "noopener" },
      navIcon('<path d="M14 4h6v6"></path><path d="M20 4l-9 9"></path><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"></path>'),
      el("span", { class: "ds-nav-text" }, "Mở Studio")) : null,
    el("div", { class: "ds-side-foot" },
      run ? el("div", { class: "ds-side-box" },
        el("span", { class: "ds-row" }, el("span", {}, "Run"), el("strong", {}, `#${run.run_number ?? "?"}`)),
        el("span", {}, STUDIO_STATUS_TEXT[run.status] || run.status)) : null,
      run && (run.agent_label || run.agent_id) ? el("div", { class: "ds-side-box" },
        el("span", {}, "Agent"),
        el("span", { class: "ds-agent" }, el("i", {}), [run.agent_label || run.agent_id, run.model].filter(Boolean).join(" · "))) : null,
    ),
  );
}

function renderHead(s) {
  const run = s.studio_run;
  const board = s.storyboard;
  const chips = [
    stageLabel(s.pipeline.pipeline_type || "pipeline"),
    board && board.scenes.length
      ? `${board.scenes.length} cảnh${board.total_duration_seconds ? ` · ${fmtDuration(board.total_duration_seconds)}` : ""}` : null,
    s.style_playbook,
    run && (run.agent_label || run.agent_id) ? [run.agent_label || run.agent_id, run.model].filter(Boolean).join(" · ") : null,
  ].filter(Boolean).map((t) => el("span", { class: "ds-chip" }, t));

  const spent = s.cost ? (s.cost.total_spent_usd ?? 0) : 0;
  const hasBudget = Boolean(s.cost && s.cost.budget_remaining_usd != null);
  const budget = hasBudget ? spent + s.cost.budget_remaining_usd : null;
  const pct = hasBudget && budget > 0 ? Math.min(100, (spent / budget) * 100) : 0;
  const spentBox = el("div", { class: "ds-spent" },
    el("span", { class: "ds-spent-row" }, el("span", {}, "Đã chi"),
      el("span", {}, el("strong", {}, fmtMoney(spent)), hasBudget ? ` / ${fmtMoney(budget)}` : "")),
    el("div", { class: "ds-bar" }, el("i", { class: pct > 90 ? "crit" : pct > 75 ? "warn" : "", style: `width:${pct}%` })));

  const canReplay = !replay && replayBounds(state);
  return el("header", { class: "ds-head" },
    el("div", { class: "ds-head-left" },
      el("span", { class: "ds-crumbs" }, el("a", { href: s.series ? `/?${new URLSearchParams({ series: s.series })}` : "/" }, "Thư viện"), ` / ${s.title}`),
      el("div", { class: "ds-title-row" },
        el("h1", {}, s.title),
        el("span", { class: "ds-id" }, `${s.project_id}${run && run.run_number ? ` · run #${run.run_number}` : ""}`)),
      chips.length ? el("div", { class: "ds-chips" }, chips) : null,
    ),
    el("div", { class: "ds-head-right" },
      spentBox,
      canReplay ? el("button", { type: "button", class: "ds-btn blue", onclick: startReplay }, "▶ Xem lại run") : null,
      run && run.studio_url ? el("a", { class: "ds-btn", href: run.studio_url, target: "_blank", rel: "noopener" }, "Mở trong Studio") : null,
    ),
  );
}

// ---------------------------------------------------------------------------
// stage rail
// ---------------------------------------------------------------------------

// Stage rail — docs/design/mockups/Board.dc.html ("Tiến trình pipeline"):
// card, one thin line, dot + label + status word. ✓ Xong/Đã duyệt, ● Đang
// chạy (blue pulse), ◆ Chờ bạn duyệt, … Xếp hàng, empty dot Cần duyệt/—.
function stageLabel(name) {
  const t = String(name || "").replace(/[_-]+/g, " ").trim();
  return t ? t.charAt(0).toUpperCase() + t.slice(1) : "";
}

// Kind per stage from checkpoints; Studio run state fills the gaps a
// checkpoint-less agent leaves (queued run → next stage "Xếp hàng", Studio
// gate → its stage "Chờ bạn duyệt"; current step inferred server-side).
function railKinds(s) {
  const declared = s.stages.filter((x) => !x.undeclared);
  const run = s.studio_run;
  const kinds = new Map();
  for (const st of s.stages) {
    kinds.set(st.name,
      st.status === "completed" ? "done"
        : st.status === "awaiting_human" ? "wait"
        : st.status === "failed" ? "fail"
        : st.status === "in_progress" ? (st.stalled ? "stall" : "run")
        : st.current ? "run"
        : "todo");
  }
  const busy = [...kinds.values()].some((k) => k === "wait" || k === "run" || k === "stall");
  if (run && !busy) {
    if (run.status === "waiting_approval" && kinds.get(run.pending_gate) === "todo") {
      kinds.set(run.pending_gate, "wait");
    } else if (run.status === "queued") {
      const lastDone = declared.reduce((acc, x, i) => (kinds.get(x.name) === "done" ? i : acc), -1);
      const next = declared.slice(lastDone + 1).find((x) => kinds.get(x.name) === "todo");
      if (next) kinds.set(next.name, "queue");
    }
  }
  return kinds;
}

const RAIL_MARK = { done: "✓", wait: "◆", run: "●", queue: "…", fail: "✕", stall: "!", todo: "" };

function railSub(st, kind) {
  switch (kind) {
    case "done": return st.gated && st.human_approved ? "Đã duyệt" : "Xong";
    case "wait": return "Chờ bạn duyệt";
    case "run": return "Đang chạy";
    case "queue": return "Xếp hàng";
    case "fail": return "Lỗi";
    case "stall": return `Im lặng ${st.stalled_minutes || "?"} phút`;
    default: return st.gated ? "Cần duyệt" : "—";
  }
}

function railTitle(st) {
  const bits = [st.name];
  if (st.timestamp) bits.push(fmtClock(st.timestamp));
  if (st.versions > 1) bits.push(`${st.versions} phiên bản`);
  if (st.current_tool) bits.push(st.current_tool);
  if (st.error) bits.push(String(st.error).slice(0, 160));
  return bits.join(" · ");
}

function renderRail(s) {
  const rail = el("ol", { class: "rail", "aria-label": "Tiến trình pipeline" },
    el("div", { class: "rail-line", "aria-hidden": "true" }));
  const kinds = railKinds(s);
  for (const st of s.stages) {
    const kind = kinds.get(st.name) || "todo";
    const current = kind === "wait" || kind === "run" || kind === "queue" || kind === "stall";
    rail.append(el("li", {
      class: `stage k-${kind}${selectedStage === st.name ? " selected" : ""}${st.undeclared ? " undeclared" : ""}`,
      title: railTitle(st),
      "aria-current": current ? "step" : null,
      tabindex: "0",
      onclick: () => toggleDrawer(st.name),
      onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggleDrawer(st.name); } },
    },
      el("span", { class: "node", "aria-hidden": "true" }, RAIL_MARK[kind]),
      el("span", { class: "name" }, stageLabel(st.name)),
      el("span", { class: "sub" }, railSub(st, kind)),
    ));
  }
  return rail;
}

function toggleDrawer(stageName) {
  selectedStage = selectedStage === stageName ? null : stageName;
  render();
}

const STAGE_ARTIFACTS = {
  research: ["research_brief"],
  proposal: ["proposal_packet"],
  idea: ["brief"],
  script: ["script"],
  scene_plan: ["scene_plan"],
  assets: ["asset_manifest"],
  edit: ["edit_decisions"],
  compose: ["render_report", "final_review"],
  publish: ["publish_log"],
};

function artifactNamesForStage(st) {
  const declared = Array.isArray(st.produces) ? st.produces : [];
  const fallback = STAGE_ARTIFACTS[st.name] || [];
  return [...new Set([...declared, ...fallback].filter(Boolean))];
}

function reviewMetrics(review) {
  const nested = review && review.summary && typeof review.summary === "object"
    ? review.summary : {};
  return {
    critical: Number((review && review.critical) ?? nested.critical ?? 0),
    suggestions: Number((review && review.suggestions) ?? nested.suggestions ?? 0),
    nitpicks: Number((review && review.nitpicks) ?? nested.nitpicks ?? 0),
  };
}

function reviewSummaryText(review) {
  if (!review) return "";
  if (typeof review.summary === "string") return review.summary;
  const nested = review.summary && typeof review.summary === "object" ? review.summary : {};
  const counts = reviewMetrics(review);
  return [
    review.decision,
    `${counts.critical} lỗi nặng`,
    `${counts.suggestions} gợi ý`,
    nested.review_focus_met ? `trọng tâm ${nested.review_focus_met}` : null,
    nested.schema_validation,
  ].filter(Boolean).join(" · ");
}

function renderDrawer(s) {
  if (!selectedStage) return null;
  const st = s.stages.find((x) => x.name === selectedStage);
  if (!st) return null;

  const body = el("div", { class: "drawer-body" });

  if (st.review) {
    const metrics = reviewMetrics(st.review);
    const summary = reviewSummaryText(st.review);
    body.append(el("div", { class: "findings", style: "margin-bottom:12px" },
      el("span", { class: `f ${metrics.critical ? "crit" : ""}` }, `${metrics.critical} lỗi nặng`),
      el("span", { class: `f ${metrics.suggestions ? "sugg" : ""}` }, `${metrics.suggestions} gợi ý`),
      el("span", { class: "f" }, `${metrics.nitpicks} góp ý nhỏ`),
      summary ? el("span", { style: "font-size:calc(11.5px * var(--fs-scale));color:var(--text-2);margin-left:8px" }, summary) : null,
    ));
  }

  if (st.name === "character_design" || st.name === "rig_plan") {
    const cast = renderCast(s);
    if (cast) body.append(cast);
  }
  const names = artifactNamesForStage(st);
  let shown = false;
  for (const name of names) {
    const artifact = s.artifacts[name];
    if (!artifact) continue;
    shown = true;
    body.append(
      el("div", { class: "d-cat", style: "font-family:var(--mono);font-size:calc(9.5px * var(--fs-scale));color:var(--text-3);letter-spacing:.1em;text-transform:uppercase;margin:6px 0 4px" }, name),
      el("pre", {}, JSON.stringify(artifact, null, 2)),
    );
  }
  if (!shown) {
    body.append(el("div", { class: "hint" },
      st.status === "pending" ? "Stage này chưa chạy." : "Không tìm thấy artifact của stage này trên đĩa."));
  }

  return el("div", { class: "drawer" },
    el("div", { class: "drawer-head" },
      el("h3", {}, `${stageLabel(st.name)} — ${{ completed: "xong", in_progress: "đang chạy", awaiting_human: "chờ bạn duyệt", failed: "lỗi", pending: "chưa chạy" }[st.status] || st.status}`),
      st.gate_skipped ? el("span", { class: "gate-chip" }, "⚑ Bỏ qua gate") : null,
      st.versions > 1 ? el("span", { class: "ver-chip" }, `${st.versions} phiên bản`) : null,
      st.timestamp ? el("span", { class: "meta", style: "font-size:12px" }, fmtClock(st.timestamp)) : null,
      el("span", { class: "close", onclick: () => toggleDrawer(st.name) }, "Đóng ✕"),
    ),
    body,
  );
}

// ---------------------------------------------------------------------------
// script card
// ---------------------------------------------------------------------------

function scriptSections(script, limit) {
  const sections = script.sections || [];
  const shown = limit ? sections.slice(0, limit) : sections;
  const nodes = [];
  for (const sec of shown) {
    nodes.push(el("div", { class: "sp-slug" },
      `${(sec.id || "").toUpperCase()} — ${sec.label || "Section"} `,
      el("span", { class: "tc" }, `${fmtDuration(sec.start_seconds)} – ${fmtDuration(sec.end_seconds)}`)));
    if (sec.text) nodes.push(el("div", { class: "sp-action" }, sec.text));
    if (sec.speaker_directions) nodes.push(el("div", { class: "sp-paren" }, `(${sec.speaker_directions})`));
    const cues = sec.enhancement_cues || [];
    if (cues.length) {
      nodes.push(el("div", { style: "margin-left:42px" },
        cues.map((c) => el("span", { class: "sp-cue" }, `▸ ${c.type} · ${String(c.description || "").slice(0, 60)}`))));
    }
  }
  if (limit && sections.length > limit) {
    nodes.push(el("div", { class: "sp-fade" }, `… ${sections.length - limit} more sections`));
  }
  return nodes;
}

function renderScriptTab(s) {
  const script = s.artifacts.script;
  if (!script) {
    return [el("div", { class: "ds-empty" }, "Chưa có script.",
      el("small", {}, "Script xuất hiện ở đây sau khi agent viết xong stage Script."))];
  }
  const sections = script.sections || [];
  const board = s.storyboard;
  const out = [el("div", { class: "ds-script-head" },
    el("h2", {}, `Script · ${sections.length} cảnh${script.total_duration_seconds ? ` · ${fmtDuration(script.total_duration_seconds)}` : ""}`),
    el("span", {}, "artifacts/script.json"))];
  sections.forEach((sec, i) => {
    const card = board && board.scenes.find((c) => c.section_id === sec.id || c.script_section_id === sec.id);
    const audio = card && card.audio.find((a) => a.exists && (a.type === "narration" || a.type === "audio"));
    out.push(el("article", { class: "ds-scene-row" },
      el("div", { class: "when" }, el("b", {}, `Cảnh ${i + 1}`),
        el("span", {}, `${fmtDuration(sec.start_seconds)}–${fmtDuration(sec.end_seconds)}`)),
      el("div", { class: "what" },
        sec.label ? el("span", {}, el("span", { class: "lbl" }, "Hình: "), sec.label) : null,
        el("div", { class: "vo" }, el("span", { class: "lbl" }, "Lời dẫn:"),
          sec.text ? el("em", {}, sec.text) : el("span", { class: "lbl" }, "Không lời.")),
        sec.speaker_directions ? el("span", { class: "dir" }, `(${sec.speaker_directions})`) : null,
        audio ? el("button", { type: "button", class: "ds-listen", onclick: () => {
          player.src = mediaURL(s.project_id, audio.path); player.play();
        } }, "▶ Nghe lời dẫn") : null,
      ),
    ));
  });
  return out;
}

function humanize(value) {
  return String(value || "artifact").replaceAll("_", " ");
}

function shortText(value, limit = 180) {
  const text = String(value || "").trim();
  return text.length > limit ? `${text.slice(0, limit - 1)}…` : text;
}

function reviewFact(label, value) {
  if (value == null || value === "") return null;
  return el("div", { class: "approval-fact" },
    el("span", {}, label),
    el("b", {}, value),
  );
}

function reviewFacts(items) {
  const facts = items.filter(Boolean);
  return facts.length ? el("div", { class: "approval-facts" }, facts) : null;
}

function titledItems(items, selectedId = null) {
  const rows = (items || []).slice(0, 4).map((item, index) => {
    if (item == null) return null;
    if (typeof item !== "object") {
      return el("li", {}, shortText(item));
    }
    const id = item.id || item.concept_id || item.option_id;
    const title = item.title || item.name || item.display_name || item.label || id || item.path || item.platform || item.description || `Item ${index + 1}`;
    const detail = item.hook || item.why_this_works || item.summary || item.description || item.silhouette_notes;
    return el("li", { class: id && id === selectedId ? "selected" : "" },
      el("div", { class: "approval-item-title" }, shortText(title, 100),
        id && id === selectedId ? el("span", { class: "approval-selected" }, "ĐÃ CHỌN") : null),
      detail && detail !== title ? el("p", {}, shortText(detail)) : null,
    );
  }).filter(Boolean);
  return rows.length ? el("ul", { class: "approval-items" }, rows) : null;
}

function genericArtifactSummary(artifact) {
  const facts = [];
  const items = [];
  for (const [key, value] of Object.entries(artifact || {})) {
    if (["version", "decision_log_ref"].includes(key)) continue;
    if (["string", "number", "boolean"].includes(typeof value)) {
      facts.push(reviewFact(humanize(key), shortText(value, 90)));
    } else if (Array.isArray(value)) {
      facts.push(reviewFact(humanize(key), `${value.length} item${value.length === 1 ? "" : "s"}`));
      if (!items.length && value.length) items.push(titledItems(value));
    }
    if (facts.length >= 6) break;
  }
  return [reviewFacts(facts), ...items].filter(Boolean);
}

function artifactReviewContent(name, artifact) {
  if (name === "character_design" && state && Array.isArray(artifact.characters)) {
    const cast = renderCast(state);
    return cast ? [cast] : genericArtifactSummary(artifact);
  }
  if (name === "brief") {
    return [
      artifact.hook ? el("p", { class: "approval-lead" }, artifact.hook) : null,
      reviewFacts([
        reviewFact("Nền tảng", artifact.target_platform),
        reviewFact("Thời lượng", artifact.target_duration_seconds != null ? fmtDuration(artifact.target_duration_seconds) : null),
        reviewFact("Giọng điệu", artifact.tone),
        reviewFact("Phong cách", artifact.style),
      ]),
      titledItems(artifact.key_points),
    ].filter(Boolean);
  }
  if (name === "proposal_packet") {
    const selected = (artifact.selected_concept || {}).concept_id;
    const plan = artifact.production_plan || {};
    const cost = artifact.cost_estimate || {};
    return [
      reviewFacts([
        reviewFact("Renderer", plan.render_runtime),
        reviewFact("pipeline", plan.pipeline),
        reviewFact("Ước tính chi phí", cost.total_estimated_usd != null ? fmtMoney(cost.total_estimated_usd) : null),
        reviewFact("Số concept", Array.isArray(artifact.concept_options) ? artifact.concept_options.length : null),
      ]),
      titledItems(artifact.concept_options, selected),
      (artifact.selected_concept || {}).rationale
        ? el("p", { class: "approval-rationale" },
          el("b", {}, "VÌ SAO CHỌN  "), shortText(artifact.selected_concept.rationale))
        : null,
    ].filter(Boolean);
  }
  if (name === "research_brief") {
    return [
      artifact.topic ? el("p", { class: "approval-lead" }, artifact.topic) : null,
      reviewFacts([
        reviewFact("Nguồn", Array.isArray(artifact.sources) ? artifact.sources.length : null),
        reviewFact("Số liệu", Array.isArray(artifact.data_points) ? artifact.data_points.length : null),
        reviewFact("Góc kể", Array.isArray(artifact.angles_discovered) ? artifact.angles_discovered.length : null),
      ]),
      titledItems(artifact.angles_discovered),
    ].filter(Boolean);
  }
  if (name === "script") {
    const first = (artifact.sections || [])[0];
    return [
      reviewFacts([
        reviewFact("Thời lượng", fmtDuration(artifact.total_duration_seconds)),
        reviewFact("Số cảnh", (artifact.sections || []).length),
      ]),
      first && first.text ? el("p", { class: "approval-lead" }, shortText(first.text, 220)) : null,
      el("p", { class: "approval-guidance" }, "Toàn bộ script ở tab Script."),
    ].filter(Boolean);
  }
  if (name === "scene_plan") {
    const scenes = artifact.scenes || [];
    const end = scenes.reduce((max, scene) => Math.max(max, Number(scene.end_seconds) || 0), 0);
    return [
      reviewFacts([
        reviewFact("Số cảnh", scenes.length),
        reviewFact("Thời lượng", end ? fmtDuration(end) : null),
      ]),
      titledItems(scenes),
      el("p", { class: "approval-guidance" }, "Xem nhịp và khung hình từng cảnh ở tab Storyboard."),
    ].filter(Boolean);
  }
  if (name === "asset_manifest") {
    const assets = artifact.assets || [];
    const types = [...new Set(assets.map((asset) => asset.type).filter(Boolean))];
    return [
      reviewFacts([
        reviewFact("Số file", assets.length),
        reviewFact("Loại", types.join(", ")),
        reviewFact("Chi phí tạo", artifact.total_cost_usd != null ? fmtMoney(artifact.total_cost_usd) : null),
      ]),
      titledItems(assets),
      el("p", { class: "approval-guidance" }, "Xem từng file ở tab Storyboard và Media trước khi duyệt dựng video."),
    ].filter(Boolean);
  }
  if (name === "edit_decisions") {
    return [
      reviewFacts([
        reviewFact("Số cắt", Array.isArray(artifact.cuts) ? artifact.cuts.length : null),
        reviewFact("Renderer", artifact.render_runtime || (artifact.metadata || {}).render_runtime),
      ]),
      titledItems(artifact.cuts),
    ].filter(Boolean);
  }
  if (name === "render_report") {
    return [
      reviewFacts([
        reviewFact("Bản xuất", Array.isArray(artifact.outputs) ? artifact.outputs.length : null),
        reviewFact("Thời lượng", artifact.duration_seconds != null ? fmtDuration(artifact.duration_seconds) : null),
      ]),
      titledItems(artifact.outputs),
    ].filter(Boolean);
  }
  if (name === "publish_log") {
    return [
      reviewFacts([reviewFact("Nơi đăng", Array.isArray(artifact.entries) ? artifact.entries.length : null)]),
      titledItems((artifact.entries || []).map((entry) => ({
        title: entry.platform || entry.destination || "Publish destination",
        description: [entry.status, entry.url].filter(Boolean).join(" · "),
      }))),
    ].filter(Boolean);
  }
  return genericArtifactSummary(artifact);
}

function artifactReviewTitle(name, artifact, s) {
  if (name === "proposal_packet") {
    const selected = (artifact.selected_concept || {}).concept_id;
    const concept = (artifact.concept_options || []).find((item) => item.id === selected);
    return (concept && concept.title) || "Production proposal";
  }
  if (name === "research_brief") return artifact.topic || "Research brief";
  if (name === "scene_plan") return "Scene plan";
  if (name === "asset_manifest") return "Generated assets";
  if (name === "edit_decisions") return "Edit decisions";
  if (name === "render_report") return "Render report";
  if (name === "publish_log") return "Publish plan";
  return artifact.title || artifact.name || s.title;
}

function renderApprovalReview(s) {
  const awaiting = s.stages.find((item) => item.status === "awaiting_human");
  if (!awaiting) return null;

  const names = artifactNamesForStage(awaiting);
  const entries = names
    .filter((name) => name !== "decision_log")
    .map((name) => [name, s.artifacts[name]])
    .filter(([, artifact]) => artifact && typeof artifact === "object");
  const stageIndex = s.stages.findIndex((item) => item.name === awaiting.name);
  const nextStage = stageIndex >= 0 ? s.stages[stageIndex + 1] : null;
  const review = awaiting.review || {};
  const reviewSummary = reviewSummaryText(review);

  const artifacts = entries.map(([name, artifact]) => el("article", {
    class: "approval-artifact",
    "data-artifact": name,
  },
    el("div", { class: "approval-artifact-kicker" }, humanize(name)),
    el("h2", {}, artifactReviewTitle(name, artifact, s)),
    ...artifactReviewContent(name, artifact),
  ));

  if (!artifacts.length) {
    artifacts.push(el("div", { class: "approval-missing", role: "alert" },
      el("b", {}, "Không có gì để duyệt. "),
      names.length
        ? `Checkpoint ${stageLabel(awaiting.name)} khai báo ${names.map(humanize).join(", ")} nhưng Backlot không đọc được file.`
        : `Checkpoint ${stageLabel(awaiting.name)} không khai báo artifact nào.`,
    ));
  }

  return el("section", { class: "approval-review", "data-stage": awaiting.name },
    el("div", { class: "approval-review-head" },
      el("span", { class: "approval-eyebrow" }, `${stageLabel(awaiting.name)} — chờ bạn duyệt`),
      el("span", { class: "approval-status" }, "Đã dừng · chưa tốn thêm tiền cho tới khi bạn duyệt"),
    ),
    el("div", { class: "approval-review-body" },
      reviewSummary ? el("div", { class: "approval-review-note" },
        el("b", {}, "TỰ ĐÁNH GIÁ  "), shortText(reviewSummary, 260)) : null,
      el("div", { class: "approval-artifacts" }, artifacts),
    ),
    el("div", { class: "approval-review-foot" },
      el("span", {}, nextStage
        ? `Duyệt xong sẽ chạy tiếp ${stageLabel(nextStage.name)}. Duyệt hoặc yêu cầu sửa trong Studio.`
        : "Đây là bước duyệt cuối. Duyệt hoặc yêu cầu sửa trong Studio."),
      el("span", { style: "display:flex;gap:10px;flex-wrap:wrap" },
        el("button", { type: "button", class: "ds-btn small", onclick: () => toggleDrawer(awaiting.name) }, "Xem artifact đầy đủ"),
        s.studio_run && s.studio_run.studio_url
          ? el("a", { class: "ds-btn small primary", href: s.studio_run.studio_url, target: "_blank", rel: "noopener" }, "Duyệt trong Studio") : null),
    ),
  );
}

function openScriptModal() {
  const script = state && state.artifacts.script;
  if (!script) return;
  modal.innerHTML = "";
  modal.append(
    el("span", { class: "modal-close", onclick: closeModal }, "ESC · CLOSE"),
    el("div", { class: "modal-page" },
      el("div", { class: "script-card", style: "cursor:default" },
        el("div", { class: "sp-title" }, script.title || state.title),
        el("div", { class: "sp-meta" },
          `script · ${fmtDuration(script.total_duration_seconds)} · ${(script.sections || []).length} sections`),
        ...scriptSections(script, 0),
        el("div", { class: "sp-fade" }, "END"),
      )),
  );
  modal.classList.add("open");
}

function openNarrModal(card) {
  modal.innerHTML = "";
  const meta = [sceneLabel(card.id), card.section_label, fmtDuration(card.duration_seconds)]
    .filter(Boolean).join(" · ");
  modal.append(
    el("span", { class: "modal-close", onclick: closeModal }, "ESC · CLOSE"),
    el("div", { class: "modal-page" },
      el("div", { class: "script-card", style: "cursor:default" },
        el("div", { class: "sp-meta" }, meta),
        card.narration ? el("div", { class: "sp-action", style: "margin-left:0" }, card.narration) : null,
        card.shot_intent ? el("div", { class: "sp-paren", style: "margin-left:0" }, `Intent — ${card.shot_intent}`) : null,
        card.description ? el("div", { class: "sp-paren", style: "margin-left:0" }, card.description) : null,
      )),
  );
  modal.classList.add("open");
}

function closeModal() { modal.classList.remove("open"); }
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });
modal.addEventListener("click", (e) => { if (e.target === modal) closeModal(); });

// ---------------------------------------------------------------------------
// right rail: decisions, activity
// ---------------------------------------------------------------------------

function currentDecisions(s) {
  const log = s.artifacts.decision_log;
  const decisions = (log && log.decisions) || [];
  // Collapse by category+subject: a decision that changed mid-run is
  // superseded by the later entry — show the CURRENT choice, marked revised.
  const current = new Map();
  decisions.forEach((d, i) => {
    const key = `${d.category || "decision"}::${d.subject || ""}`;
    const prev = current.get(key);
    current.set(key, { d, order: i, revised: prev ? prev.revised + 1 : 0 });
  });
  return [...current.values()].sort((a, b) => b.order - a.order).slice(0, 8);
}

function activityRows(s) {
  const events = s.events || [];
  // A start is "running" only until a later finish/error for the same
  // tool+scene closes it; unmatched starts render as live.
  const open = new Map();
  const rows = [];
  for (const ev of events) {
    const key = `${ev.tool}:${ev.scene_id || ""}`;
    if (ev.event === "start") {
      const slot = open.get(key) || { count: 0, ev };
      slot.count += 1; slot.ev = ev; open.set(key, slot);
    } else {
      const slot = open.get(key);
      if (slot) { slot.count -= 1; if (slot.count <= 0) open.delete(key); }
      rows.push(ev);
    }
  }
  for (const slot of open.values()) rows.push(slot.ev);
  rows.sort((a, b) => String(a.ts).localeCompare(String(b.ts)));
  return rows;
}

function eventText(ev) {
  const scene = ev.scene_id ? ` · ${sceneLabel(ev.scene_id)}` : "";
  if (ev.event === "finish") {
    const dur = ev.duration_s != null ? ` · ${Number(ev.duration_s).toFixed(1)}s` : "";
    return { text: `${ev.tool}${scene} ${ev.success === false ? "✕ lỗi" : "✓"}${dur}${ev.cost_usd ? ` · ${fmtMoney(ev.cost_usd)}` : ""}`, cls: ev.success === false ? "err" : "ok" };
  }
  if (ev.event === "error") return { text: `${ev.tool}${scene} ✕ lỗi`, cls: "err" };
  return { text: `${ev.tool}${scene} · đang chạy`, cls: "run" };
}

const isoClock = (iso) => (iso ? fmtClock(iso).replace(/:\d\d(\s|$)/, "$1").trim() : "");

function renderAside(s) {
  const run = s.studio_run;
  const rows = activityRows(s);
  const head = el("div", { class: "ds-aside-head" },
    el("span", { class: "ds-aside-title" }, "Hoạt động của agent"), renderLivePill(s));
  const views = el("div", { class: "ds-views", role: "group", "aria-label": "Kiểu xem" },
    [["feed", "Hội thoại"], ["log", "Log kỹ thuật"]].map(([k, label]) => el("button", {
      type: "button", "aria-pressed": String(asideView === k), onclick: () => { asideView = k; render(); },
    }, label)));

  let body;
  if (asideView === "log") {
    body = el("div", { class: "ds-log" });
    for (const ev of rows.slice(-40)) {
      const { text, cls } = eventText(ev);
      body.append(el("div", {}, el("span", { class: "t" }, fmtClock(ev.ts)), el("span", { class: `m ${cls}` }, text)));
    }
    if (!rows.length) body.append(el("div", {}, el("span", { class: "m" }, "Chưa có sự kiện nào trong events.jsonl.")));
  } else {
    body = el("ol", { class: "ds-feed", "aria-live": "polite" });
    const msg = (who, t, node) => el("li", { class: "ds-msg" }, el("span", { class: "who" }, `${who}${t ? ` · ${t}` : ""}`), node);
    if (run && run.queued_at && run.status === "queued") {
      body.append(msg("Studio", isoClock(run.queued_at), el("span", { class: "studio" },
        `Run #${run.run_number ?? "?"} xếp hàng${run.queue_position ? `, vị trí ${run.queue_position}` : ""}`)));
    }
    if (run && run.started_at) {
      body.append(msg("Studio", isoClock(run.started_at), el("span", { class: "studio" },
        `Run #${run.run_number ?? "?"} bắt đầu · ${[run.agent_label || run.agent_id, run.model].filter(Boolean).join(" · ")}`)));
    }
    for (const st of s.stages.filter((x) => x.status === "completed" && x.timestamp)
      .sort((a, b) => String(a.timestamp).localeCompare(String(b.timestamp))).slice(-6)) {
      body.append(msg("Agent", isoClock(st.timestamp), el("div", { class: "bubble" },
        `Xong ${stageLabel(st.name)}${st.gated && st.human_approved ? " · đã duyệt" : ""}.`)));
    }
    if (run && run.last_event && (run.status === "running" || run.status === "queued")) {
      body.append(msg("Agent", isoClock(run.last_event_at), el("div", { class: "bubble" }, run.last_event)));
    }
    if (run && run.status === "waiting_approval") {
      body.append(msg("Studio", isoClock(run.ended_at || run.updated_at), el("span", { class: "studio" },
        `Dừng ở ${stageLabel(run.pending_gate || run.stage || "gate")}, chờ bạn duyệt.`)));
    }
    if (run && run.status === "failed") {
      body.append(msg("Studio", isoClock(run.ended_at || run.updated_at), el("div", { class: "bubble err" },
        `Run #${run.run_number ?? "?"} lỗi${run.error ? `: ${run.error}` : "."}`)));
    }
    if (run && run.status === "succeeded") {
      body.append(msg("Studio", isoClock(run.ended_at || run.updated_at), el("span", { class: "studio" },
        `Run #${run.run_number ?? "?"} kết thúc.`)));
    }
    if (!body.children.length) {
      body.append(el("li", { class: "ds-msg" }, el("span", { class: "studio" }, "Chưa có hoạt động nào.")));
    }
  }

  const note = el("p", { class: "ds-aside-note" },
    run && run.studio_url ? "Backlot chỉ để xem. Muốn nhắn agent hoặc duyệt, mở project trong Studio." : "Backlot chỉ để xem — đọc trực tiếp từ thư mục project.");

  const decisions = currentDecisions(s);
  const dlog = decisions.length ? el("div", { class: "ds-decisions" },
    el("span", {}, "Decision log"),
    el("ul", {}, decisions.map(({ d, revised }) => {
      const opt = (d.options_considered || []).find((o) => (o.option_id ?? o.label) === d.selected);
      const pick = (opt && opt.label) || d.selected || "";
      return el("li", {},
        el("b", {}, `${d.subject || humanize(d.category || "decision")}: ${pick}`),
        d.reason ? el("span", {}, d.reason) : null,
        revised ? el("em", {}, "đã đổi") : null);
    }))) : null;

  return el("aside", { class: "ds-aside", "aria-label": "Hoạt động của agent" }, head, views, body, note, dlog);
}

// ---------------------------------------------------------------------------
// storyboard filmstrip
// ---------------------------------------------------------------------------

function sceneLabel(id) {
  // "sc4" → "SC 04", "scene-11" → "SC 11", anything else → uppercased id
  const m = String(id).match(/(\d+)\s*$/);
  if (m) return `SC ${m[1].padStart(2, "0")}`;
  return String(id).toUpperCase().slice(0, 10);
}

function sceneThumb(s, card) {
  if (card.generating) {
    return el("div", { class: "thumb generating" }, `Đang tạo${card.generating_tool ? ` · ${card.generating_tool}` : ""}…`);
  }
  const v = card.visual;
  if (v && v.exists) {
    if (v.type === "video") {
      const thumb = el("div", { class: "thumb" },
        el("video", { src: mediaURL(s.project_id, v.path), muted: "", preload: "metadata", playsinline: "" }),
        el("span", { class: "play" }, "▶"));
      thumb.onclick = () => { const vid = thumb.querySelector("video"); if (vid.paused) vid.play(); else vid.pause(); };
      return thumb;
    }
    const img = el("img", { src: thumbURL(s.project_id, v.path, 640), loading: "lazy", alt: "" });
    img.onerror = () => { const t = img.closest(".thumb"); if (t) { t.className = "thumb missing"; t.textContent = "Không mở được ảnh"; } };
    return el("div", { class: "thumb" }, img, v.snapshot ? el("span", { class: "badge" }, "snapshot") : null);
  }
  if (v && !v.exists) return el("div", { class: "thumb missing" }, "Có trong manifest, thiếu file");
  // No per-scene asset (e.g. rigged character scenes rendered straight to
  // video): show the scene's middle frame from the final render instead.
  const frame = sceneFrame(s, card);
  if (frame) {
    const img = el("img", { src: `${thumbURL(s.project_id, frame.render.path, 640)}&t=${frame.mid.toFixed(2)}`, loading: "lazy", alt: "" });
    img.onerror = () => { const t = img.closest(".thumb"); if (t) { t.className = "thumb missing"; t.textContent = "Không lấy được khung hình"; } };
    return el("div", { class: "thumb" }, img, el("span", { class: "badge" }, `Khung từ ${frame.render.path.split("/").pop()}`));
  }
  if (card.required_assets.length) return el("div", { class: "thumb missing" }, "Chưa tạo — tạo ở stage Assets");
  return el("div", { class: "thumb" }, el("div", { class: "spec-in" },
    el("div", { class: "spec-desc" }, (card.description || "").slice(0, 80))));
}

// The render a scene can be cut from: final.mp4 when present, else the
// newest render — only once compose has run (replay hides renders before).
function sceneFrame(s, card) {
  const renders = s.media.renders || [];
  if (!renders.length || card.start_seconds == null || card.end_seconds == null) return null;
  const render = renders.find((r) => /(^|\/)final\.mp4$/i.test(r.path)) || renders[0];
  const start = Number(card.start_seconds);
  const end = Number(card.end_seconds);
  if (!(end > start)) return null;
  return { render, start, end, mid: start + (end - start) / 2 };
}

function playScene(s, card, button) {
  const frame = sceneFrame(s, card);
  const thumb = button.closest(".ds-sb-card").querySelector(".thumb");
  if (!frame || !thumb) return;
  const video = el("video", {
    src: `${mediaURL(s.project_id, frame.render.path)}#t=${frame.start},${frame.end}`,
    controls: "", autoplay: "", playsinline: "", preload: "auto",
  });
  thumb.className = "thumb";
  thumb.replaceChildren(video);
}

function sceneTime(card) {
  if (card.start_seconds != null && card.end_seconds != null) return `${fmtDuration(card.start_seconds)}–${fmtDuration(card.end_seconds)}`;
  return fmtDuration(card.duration_seconds);
}

function sceneMethod(card) {
  const v = card.visual;
  if (v && v.exists) return v.type === "video" ? "Clip" : "Ảnh";
  if (card.type === "character_scene") return "Nhân vật diễn hoạt";
  if (card.type === "text_card") return "Chữ động";
  if (card.type === "animation") return "Hoạt hình";
  return card.type ? humanize(card.type) : null;
}

function renderStoryboardTab(s) {
  const board = s.storyboard;
  if (!board || !board.scenes.length) {
    return [el("div", { class: "ds-empty" }, "Chưa có storyboard.",
      el("small", {}, "Storyboard xuất hiện ở đây sau stage Scene plan; ảnh và clip hiện dần khi agent tạo ở stage Assets."))];
  }
  const grid = el("div", { class: "ds-sb-grid" });
  board.scenes.forEach((card, i) => {
    const narr = card.audio.find((a) => a.exists && (a.type === "narration" || a.type === "audio"));
    const method = sceneMethod(card);
    grid.append(el("article", { class: "ds-sb-card" },
      sceneThumb(s, card),
      el("div", { class: "ds-sb-body" },
        el("span", { class: "top" }, el("strong", {}, `Cảnh ${i + 1}`), el("span", {}, sceneTime(card))),
        card.description || card.narration ? el("span", { class: "desc" }, card.description || card.narration) : null,
        method ? el("span", { class: "method" }, method) : null,
        narr || (!(card.visual && card.visual.exists) && sceneFrame(s, card)) ? el("span", { class: "acts" },
          !(card.visual && card.visual.exists) && sceneFrame(s, card)
            ? el("button", { type: "button", onclick: (e) => playScene(s, card, e.currentTarget) }, "▶ Phát cảnh") : null,
          narr ? el("button", { type: "button", onclick: () => { player.src = mediaURL(s.project_id, narr.path); player.play(); } }, "♪ Nghe lời dẫn") : null) : null,
      )));
  });
  return [grid];
}

function collectMedia(s) {
  const seen = new Set();
  const items = { image: [], video: [], narration: [], music: [] };
  const add = (kind, path, meta) => {
    if (!path || seen.has(path)) return;
    seen.add(path);
    items[kind].push({ path, meta });
  };
  const board = s.storyboard;
  (board ? board.scenes : []).forEach((card, i) => {
    const label = `Cảnh ${i + 1}`;
    for (const t of [card.visual, ...(card.takes || [])].filter((x) => x && x.exists && x.path)) {
      add(t.type === "video" ? "video" : "image", t.path, [label, t.source_tool || t.model].filter(Boolean).join(" · "));
    }
    for (const a of (card.audio || []).filter((x) => x.exists && x.path)) {
      add(a.type === "music" ? "music" : "narration", a.path, [label, a.source_tool, a.duration_seconds ? fmtDuration(a.duration_seconds) : null].filter(Boolean).join(" · "));
    }
  });
  const design = s.artifacts.character_design;
  for (const c of (design && Array.isArray(design.characters) ? design.characters : [])) {
    for (const r of (c.reference_images || []).filter((x) => x && x.path)) {
      add("image", r.path, ["Nhân vật", c.display_name || c.id, r.provider || r.source_tool].filter(Boolean).join(" · "));
    }
  }
  for (const m of s.media.music || []) add("music", m.path, "Nhạc");
  for (const snap of s.media.snapshots || []) add("image", snap.path, "Snapshot");
  return items;
}

const MEDIA_KINDS = [["image", "Ảnh"], ["video", "Clip"], ["narration", "Lời dẫn"], ["music", "Nhạc"]];

function renderMediaTab(s) {
  const items = collectMedia(s);
  const total = MEDIA_KINDS.reduce((n, [k]) => n + items[k].length, 0);
  if (!total) {
    return [el("div", { class: "ds-empty" }, "Chưa có file nào.",
      el("small", {}, "Ảnh, clip, lời dẫn và nhạc xuất hiện ở đây khi agent tạo ở stage Assets."))];
  }
  if (!mediaKind || !items[mediaKind].length) mediaKind = (MEDIA_KINDS.find(([k]) => items[k].length) || ["image"])[0];
  const filter = el("div", { class: "ds-filter" }, MEDIA_KINDS.filter(([k]) => items[k].length).map(([k, label]) =>
    el("button", { type: "button", class: "ds-pill", "aria-pressed": String(mediaKind === k), onclick: () => { mediaKind = k; render(); } },
      `${label} · ${items[k].length}`)));
  const list = el("ul", { class: "ds-media" });
  for (const m of items[mediaKind]) {
    const url = mediaURL(s.project_id, m.path);
    let thumb;
    if (mediaKind === "image") thumb = el("span", { class: "mt" }, el("img", { src: thumbURL(s.project_id, m.path, 160), loading: "lazy", alt: "" }));
    else if (mediaKind === "video") thumb = el("span", { class: "mt" }, el("video", { src: url, muted: "", preload: "metadata" }));
    else thumb = el("button", { type: "button", class: "mt", style: "border:0;cursor:pointer", title: "Nghe", onclick: () => { player.src = url; player.play(); } }, "▶");
    list.append(el("li", {}, thumb,
      el("span", { class: "mi" }, el("b", {}, m.path), m.meta ? el("span", {}, m.meta) : null),
      el("a", { href: url, target: "_blank", rel: "noopener" }, "Mở")));
  }
  return [filter, list];
}

function renderVideoTab(s) {
  const renders = s.media.renders;
  if (!renders.length) {
    return [el("div", { class: "ds-empty" }, "Chưa có bản render.",
      el("small", {}, "Video xuất hiện ở đây sau stage Compose: trình phát, nút Tải MP4, dung lượng."))];
  }
  if (activeRender >= renders.length) activeRender = 0;
  const current = renders[activeRender];
  const src = mediaURL(s.project_id, current.path);
  // Carry playback position over a rebuild so a watch is never reset.
  const prev = document.querySelector(".ds-video video");
  const video = el("video", { src, controls: "", preload: "metadata" });
  if (prev && prev.getAttribute("src") === src && (prev.currentTime > 0 || !prev.paused)) {
    const t = prev.currentTime;
    const wasPlaying = !prev.paused && !prev.ended;
    video.addEventListener("loadedmetadata", () => { video.currentTime = t; }, { once: true });
    if (wasPlaying) video.autoplay = true;
  }
  const when = current.mtime ? new Date(current.mtime * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : null;
  return [el("div", { class: "ds-video" },
    video,
    el("div", { class: "ds-video-meta" },
      el("span", {}, el("strong", {}, current.path), ` · ${(current.size / 1048576).toFixed(1)} MB${when ? ` · ${when}` : ""}`),
      el("a", { class: "ds-btn primary", style: "min-height:40px", href: src, download: current.path.split("/").pop() }, "Tải MP4")),
    renders.length > 1 ? el("div", { class: "ds-filter" }, renders.map((r, i) => el("button", {
      type: "button", class: "ds-pill", "aria-pressed": String(i === activeRender), onclick: () => { activeRender = i; render(); },
    }, `${r.path.split("/").pop()}${r.at_root ? " · root" : ""}`))) : null)];
}

// Studio run strip — OpenMontage Studio writes studio_run.json next to the
// checkpoints, so the board knows a run is queued/running/at a gate/failed
// even before the agent writes its first checkpoint.
const STUDIO_STATUS_TEXT = {
  queued: "đang xếp hàng",
  running: "đang chạy",
  waiting_approval: "chờ bạn duyệt",
  succeeded: "đã xong",
  failed: "lỗi",
  cancelled: "đã huỷ",
};

function fmtSince(epoch) {
  if (!epoch) return "";
  const d = Math.max(0, Math.round(Date.now() / 1000 - epoch));
  if (d < 60) return `${d} giây`;
  if (d < 3600) return `${Math.floor(d / 60)} phút`;
  return `${Math.floor(d / 3600)} giờ ${Math.floor((d % 3600) / 60)} phút`;
}

function currentStageName(s) {
  const kinds = railKinds(s);
  const st = s.stages.find((x) => ["run", "stall", "wait", "queue"].includes(kinds.get(x.name)));
  return st ? st.name : (s.studio_run && s.studio_run.stage) || null;
}

// Overview run card (Board.dc.html: "Agent đang …" / "Đang xếp hàng" /
// "Xong"); lives in #studio-run-slot so status-only updates swap it in place.
function renderStudioRun(s) {
  const run = s.studio_run;
  const st = boardStatus(s);
  const who = run ? [run.agent_label || run.agent_id, run.model].filter(Boolean).join(" · ") : "";
  const stage = currentStageName(s);
  if (run && run.status === "running") {
    const quiet = run.stalled_minutes;
    const log = el("ol", { class: "ds-runlog" });
    for (const ev of activityRows(s).slice(-3)) {
      const { text, cls } = eventText(ev);
      log.append(el("li", { class: cls === "err" ? "err" : "" }, el("span", { class: "t" }, isoClock(ev.ts)), el("span", { class: "x" }, text)));
    }
    if (run.last_event) {
      log.append(el("li", { class: "now" }, el("span", { class: "t" }, isoClock(run.last_event_at)), el("span", { class: "x" }, run.last_event)));
    }
    return el("article", { class: `ds-run ${quiet ? "fail" : "run"}`, "aria-label": "Agent đang chạy" },
      el("div", { class: "ds-run-head" },
        quiet ? null : el("span", { class: "ds-pulse" }),
        el("h2", {}, quiet ? `Agent im lặng ${quiet} phút` : `Agent đang chạy${stage ? ` ${stageLabel(stage)}` : ""}…`),
        el("span", { class: "meta" }, [`run #${run.run_number ?? "?"}`, run.started_at_epoch ? fmtSince(run.started_at_epoch) : null, who].filter(Boolean).join(" · "))),
      log.children.length ? log : null,
      el("p", { class: "note" }, quiet
        ? "Không có hoạt động mới. Mở log trong Studio để xem agent có bị treo không."
        : "Backlot cập nhật khi agent ghi file. Muốn nhắn agent, mở project trong Studio."),
    );
  }
  if (run && run.status === "queued") {
    return el("article", { class: "ds-run queue", "aria-label": "Đang xếp hàng" },
      el("div", { class: "ds-run-head" },
        el("h2", {}, `Đang xếp hàng${run.queue_position ? ` · vị trí ${run.queue_position}` : ""}`),
        el("span", { class: "meta" }, `Run #${run.run_number ?? "?"} bắt đầu khi có chỗ trống${run.queued_at_epoch ? ` · chờ ${fmtSince(run.queued_at_epoch)}` : ""}.`)),
      el("p", { class: "note" }, "Chưa tốn thêm tiền khi xếp hàng."));
  }
  if (run && run.status === "failed") {
    return el("article", { class: "ds-run fail", "aria-label": "Run lỗi" },
      el("div", { class: "ds-run-head" },
        el("span", { class: "kicker", style: "color:var(--ds-red)" }, "Lỗi"),
        el("h2", {}, `Run #${run.run_number ?? "?"} dừng vì lỗi`),
        who ? el("span", { class: "meta" }, who) : null),
      run.error ? el("p", { class: "note" }, run.error) : null);
  }
  if (run && run.status === "waiting_approval" && !s.stages.some((x) => x.status === "awaiting_human")) {
    const gate = stageLabel(run.pending_gate || run.stage || "gate");
    return el("section", { class: "approval-review", "data-stage": run.pending_gate || "" },
      el("div", { class: "approval-review-head" },
        el("span", { class: "approval-eyebrow" }, `${gate} — chờ bạn duyệt`),
        el("span", { class: "approval-status" }, "Đã dừng · chưa tốn thêm tiền cho tới khi bạn duyệt")),
      el("div", { class: "approval-review-body" },
        el("p", {}, `Agent đã dừng ở ${gate}. Duyệt hoặc yêu cầu sửa trong Studio.`)),
      run.studio_url ? el("div", { class: "approval-review-foot" }, el("span", {}),
        el("a", { class: "ds-btn small primary", href: run.studio_url, target: "_blank", rel: "noopener" }, "Duyệt trong Studio")) : null);
  }
  if (st === "done" && s.media.renders.length) {
    const r = s.media.renders[0];
    const src = mediaURL(s.project_id, r.path);
    return el("article", { class: "ds-run done", "aria-label": "Video đã xong" },
      el("div", { class: "ds-run-head" },
        el("span", { class: "kicker", style: "color:var(--ds-green)" }, "Xong"),
        el("h2", {}, `${r.path.split("/").pop()} · ${(r.size / 1048576).toFixed(1)} MB`),
        el("span", { class: "meta" }, [run ? `${run.run_number ?? "?"} run` : null, s.cost ? `đã chi ${fmtMoney(s.cost.total_spent_usd ?? 0)}` : null].filter(Boolean).join(" · "))),
      el("video", { src, controls: "", preload: "metadata" }),
      el("div", { class: "ds-run-actions" },
        el("a", { class: "ds-btn green", style: "min-height:40px", href: src, download: r.path.split("/").pop() }, "Tải MP4"),
        replayBounds(state) ? el("button", { type: "button", class: "ds-btn", style: "min-height:40px", onclick: startReplay }, "▶ Xem lại cả quá trình") : null,
        el("button", { type: "button", class: "ds-link-btn", onclick: () => { activeTab = "video"; render(); } }, "Mở tab Video →")));
  }
  return null;
}

function renderNoState(s) {
  if (s.has_pipeline_state) return null;
  const run = s.studio_run;
  const active = run && (run.status === "running" || run.status === "queued");
  return el("div", { class: "ds-empty" },
    active ? "Chưa có checkpoint nào." : "Project này chưa có trạng thái pipeline.",
    el("small", {}, active
      ? "Các bước trên thanh tiến trình sẽ sáng dần khi agent ghi kết quả từng stage."
      : "Backlot đang hiện những gì tìm thấy trên đĩa. Run theo checkpoint protocol sẽ có đủ bảng."));
}

// Character design is a text spec in rig pipelines; the cast is shown as
// cards (role, silhouette, emotions, actions, palette) plus a few stills
// of the rendered rig — the only actual picture of the characters.
function renderCast(s, { compact = false } = {}) {
  const design = s.artifacts.character_design;
  const chars = (design && Array.isArray(design.characters)) ? design.characters : [];
  const stills = (s.media.rig_stills || []);
  if (!chars.length && !stills.length) return null;
  const palette = ((design && design.style && design.style.palette) || []).filter((c) => /^#[0-9a-f]{3,8}$/i.test(String(c)));
  const wrap = el("div", { class: "ds-cast" },
    el("div", { class: "ds-cast-head" },
      el("h3", { class: "ds-section-h" }, `Nhân vật${chars.length ? ` · ${chars.length}` : ""}`),
      palette.length ? el("span", { class: "ds-swatches", title: "Bảng màu" },
        palette.map((c) => el("i", { style: `background:${c}`, title: c }))) : null,
      design && design.style && design.style.visual_style ? el("span", { class: "ds-cast-style" }, design.style.visual_style) : null));
  if (stills.length) {
    wrap.append(el("div", { class: "ds-cast-stills" }, stills.slice(0, 4).map((f) =>
      el("figure", {},
        el("img", { src: thumbURL(s.project_id, f.path, 640), loading: "lazy", alt: "Khung hình rig nhân vật" }),
        el("figcaption", {}, f.path.split("/").pop())))));
  } else if (!chars.some((c) => (c.reference_images || []).length)) {
    wrap.append(el("p", { class: "ds-p", style: "font-size:13px;color:var(--ds-muted)" },
      "Chưa có hình: agent chưa tạo ảnh tham chiếu nhân vật, và rig chỉ có hình khi được vẽ ở stage Compose."));
  }
  if (chars.length) {
    wrap.append(el("div", { class: "ds-cast-grid" }, chars.map((c) => el("article", { class: "ds-cast-card" },
      castRef(s, c),
      el("span", { class: "top" }, el("strong", {}, c.display_name || c.id), c.id && c.display_name ? el("span", {}, c.id) : null),
      c.role ? el("span", { class: "role" }, c.role) : null,
      !compact && c.silhouette_notes ? el("span", { class: "notes" }, c.silhouette_notes) : null,
      (c.required_emotions || []).length ? el("span", { class: "chips" }, el("b", {}, "Cảm xúc"),
        (c.required_emotions || []).slice(0, compact ? 4 : 8).map((e) => el("span", { class: "method" }, humanize(e)))) : null,
      !compact && (c.required_actions || []).length ? el("span", { class: "chips" }, el("b", {}, "Hành động"),
        (c.required_actions || []).slice(0, 8).map((e) => el("span", { class: "method" }, humanize(e)))) : null,
    ))));
  }
  return wrap;
}

// Reference image made at character_design (character_reference_sheet).
function castRef(s, c) {
  const ref = (c.reference_images || []).find((r) => r && r.path);
  if (!ref) return null;
  const img = el("img", { src: thumbURL(s.project_id, ref.path, 640), loading: "lazy", alt: `Ảnh tham chiếu ${c.display_name || c.id}` });
  img.onerror = () => { const f = img.closest(".ref"); if (f) f.remove(); };
  return el("a", { class: "ref", href: mediaURL(s.project_id, ref.path), target: "_blank", rel: "noopener",
    title: [ref.view, ref.provider || ref.source_tool].filter(Boolean).join(" · ") }, img);
}

function renderBrief(s) {
  const brief = s.artifacts.brief || {};
  const research = s.artifacts.research_brief || {};
  const text = brief.hook || brief.topic || brief.summary || research.topic;
  if (!text) return null;
  const facts = [brief.target_platform, brief.target_duration_seconds ? fmtDuration(brief.target_duration_seconds) : null, brief.tone].filter(Boolean);
  return el("div", { style: "display:flex;flex-direction:column;gap:10px" },
    el("h3", { class: "ds-section-h" }, "Brief"),
    el("p", { class: "ds-p" }, text),
    facts.length ? el("div", { class: "ds-chips" }, facts.map((f) => el("span", { class: "ds-chip outline" }, f))) : null);
}

// ---------------------------------------------------------------------------
// replay — scrub a completed run from its timestamps
// ---------------------------------------------------------------------------

// Python writers emit tz-aware UTC isoformat, but treat tz-naive strings as
// UTC too — mixing local-parsed and UTC-parsed timestamps would skew replay
// ordering by the user's UTC offset.
const ts = (iso) => {
  if (!iso) return null;
  let s = String(iso);
  if (!/(Z|[+-]\d{2}:?\d{2})$/.test(s)) s += "Z";
  const t = Date.parse(s);
  return Number.isFinite(t) ? t : null;
};

function replayBounds(s) {
  const moments = [];
  for (const st of s.stages) {
    for (const h of st.history_entries || []) {
      const t = ts(h.timestamp);
      if (t) moments.push(t);
    }
  }
  for (const ev of s.events || []) {
    const t = ts(ev.ts);
    if (t) moments.push(t);
  }
  if (moments.length < 2) return null;
  return { t0: Math.min(...moments), t1: Math.max(...moments) };
}

function stateAt(s, T) {
  const view = structuredClone(s);
  for (const st of view.stages) {
    const past = (st.history_entries || []).filter((h) => ts(h.timestamp) != null && ts(h.timestamp) <= T);
    if (!past.length) {
      st.status = "pending"; st.review = null; st.timestamp = null;
      st.gate_skipped = false; st.partial_progress = null;
    } else {
      const cur = past[past.length - 1];
      st.status = cur.status || "pending";
      st.timestamp = cur.timestamp;
    }
  }
  view.events = (view.events || []).filter((ev) => ts(ev.ts) != null && ts(ev.ts) <= T);

  // Storyboard: visuals appear as their scene finishes (events) or when the
  // assets stage has completed as of T (legacy runs without events).
  if (view.storyboard) {
    const assetsStage = view.stages.find((x) => x.name === "assets");
    const assetsDone = assetsStage && assetsStage.status === "completed";
    const finished = new Set();
    const startedNow = new Map();
    for (const ev of view.events) {
      if (!ev.scene_id) continue;
      if (ev.event === "finish") { finished.add(ev.scene_id); startedNow.delete(ev.scene_id); }
      else if (ev.event === "start") startedNow.set(ev.scene_id, ev);
      else if (ev.event === "error") startedNow.delete(ev.scene_id);
    }
    const scenePlanStage = view.stages.find((x) => x.name === "scene_plan");
    const scenePlanDone = scenePlanStage && ["completed", "awaiting_human"].includes(scenePlanStage.status);
    if (!scenePlanDone) {
      view.storyboard = null;
    } else {
      for (const card of view.storyboard.scenes) {
        const visible = assetsDone || finished.has(card.id);
        if (!visible) { card.visual = null; card.takes = []; card.audio = []; }
        card.generating = startedNow.has(card.id);
        card.generating_tool = (startedNow.get(card.id) || {}).tool;
      }
    }
  }
  // Final artifacts hide until their stage happened — for every project
  // shape, storyboard or not (a degraded run must not show the finished
  // movie before its stages ran).
  const scriptStage = view.stages.find((x) => x.name === "script");
  if (!(scriptStage && ["completed", "awaiting_human"].includes(scriptStage.status))) {
    delete view.artifacts.script;
  }
  const composeStage = view.stages.find((x) => x.name === "compose");
  if (!(composeStage && composeStage.status === "completed")) {
    view.media.renders = [];
    view.media.rig_stills = [];
  }
  return view;
}

function renderReplayBar(s) {
  if (!replay) return null;
  const pos = (replay.t - replay.t0) / Math.max(1, replay.t1 - replay.t0);
  const fmt = (t) => new Date(t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const timeLabel = el("span", { class: "rp-time" }, fmt(replay.t));
  const setT = (value) => {
    replay.t = replay.t0 + (Number(value) / 1000) * (replay.t1 - replay.t0);
    timeLabel.textContent = fmt(replay.t);
  };
  return el("div", { class: "replay-bar", role: "region", "aria-label": "Xem lại run" },
    el("button", { type: "button", class: "rp-btn", onclick: toggleReplayPlay, "aria-label": replay.playing ? "Tạm dừng" : "Phát" }, replay.playing ? "❚❚" : "▶"),
    el("input", {
      type: "range", min: "0", max: "1000", value: String(Math.round(pos * 1000)), "aria-label": "Thời điểm",
      // A full render() would destroy this slider mid-drag: while dragging,
      // only pause + track the time label; re-render the board on release.
      onpointerdown: () => { replay.playing = false; },
      oninput: (e) => setT(e.target.value),
      onchange: (e) => { setT(e.target.value); render(); },
    }),
    timeLabel,
    el("button", { type: "button", class: "rp-btn", onclick: stopReplay }, "Thoát xem lại"),
    el("span", { class: "rp-note" }, `Đang xem trạng thái lúc ${fmt(replay.t)}. Bấm Thoát xem lại để về trạng thái hiện tại.`),
  );
}

let replayTimer = null;

function startReplay() {
  const bounds = replayBounds(state);
  if (!bounds) return;
  replay = { ...bounds, t: bounds.t0, playing: true };
  document.body.classList.add("replaying");
  scheduleTick();
  render();
}

function stopReplay() {
  replay = null;
  clearTimeout(replayTimer);
  document.body.classList.remove("replaying");
  render();
}

function toggleReplayPlay() {
  if (!replay) return;
  replay.playing = !replay.playing;
  if (replay.playing) scheduleTick();
  render();
}

function scheduleTick() {
  // Single pending tick, ever — rapid pause/play must not stack chains.
  clearTimeout(replayTimer);
  replayTimer = setTimeout(tickReplay, 100);
}

function tickReplay() {
  if (!replay || !replay.playing) return;
  // A full run replays in ~20 seconds regardless of real duration
  // (10 renders/second — full re-render per tick, keep it modest).
  const step = (replay.t1 - replay.t0) / 200;
  replay.t = Math.min(replay.t1, replay.t + step);
  if (replay.t >= replay.t1) replay.playing = false;
  render();
  if (replay.playing) scheduleTick();
}

// ---------------------------------------------------------------------------
// page assembly
// ---------------------------------------------------------------------------

const TABS = [["overview", "Tổng quan"], ["script", "Script"], ["storyboard", "Storyboard"], ["media", "Media"], ["video", "Video"]];

function tabCount(s, key) {
  if (key === "script") return (s.artifacts.script && (s.artifacts.script.sections || []).length) || 0;
  if (key === "storyboard") return (s.storyboard && s.storyboard.scenes.length) || 0;
  if (key === "media") { const m = collectMedia(s); return MEDIA_KINDS.reduce((n, [k]) => n + m[k].length, 0); }
  if (key === "video") return s.media.renders.length;
  return 0;
}

function renderTabCard(s) {
  const tabs = el("div", { class: "ds-tabs", role: "tablist", "aria-label": "Chế độ xem" },
    TABS.map(([key, label]) => {
      const n = tabCount(s, key);
      return el("button", {
        type: "button", role: "tab", class: "ds-tab", "aria-selected": String(activeTab === key),
        onclick: () => { activeTab = key; render(); },
      }, label, n ? el("span", { class: "n" }, String(n)) : null);
    }));
  const body = el("div", { class: "ds-tabbody", role: "tabpanel" });
  if (activeTab === "overview") {
    const slot = el("div", { id: "studio-run-slot", style: "display:contents" });
    const runCard = replay ? null : renderStudioRun(s);
    if (runCard) slot.append(runCard);
    body.append(slot);
    const review = renderApprovalReview(s);
    if (review) body.append(review);
    const noState = renderNoState(s);
    if (noState) body.append(noState);
    const brief = renderBrief(s);
    if (brief) body.append(brief);
    const cast = renderCast(s, { compact: true });
    if (cast) body.append(cast);
    if (!body.textContent.trim() && !body.querySelector("video")) {
      body.append(el("div", { class: "ds-empty" }, "Chưa có gì để hiện.",
        el("small", {}, "Xem từng bước ở thanh tiến trình, hoặc mở các tab Script, Storyboard, Video.")));
    }
  } else if (activeTab === "script") body.append(...renderScriptTab(s));
  else if (activeTab === "storyboard") body.append(...renderStoryboardTab(s));
  else if (activeTab === "media") body.append(...renderMediaTab(s));
  else if (activeTab === "video") body.append(...renderVideoTab(s));
  return el("section", { class: "ds-card", "aria-label": "Nội dung project" }, tabs, body);
}

function render() {
  if (!state) return;
  const s = replay ? stateAt(state, replay.t) : state;
  document.title = `Backlot — ${s.title}`;
  document.body.classList.toggle("first", firstPaint);
  firstPaint = false;
  const main = el("main", { class: "ds-main" }, renderHead(s), renderRail(s));
  const replayBar = renderReplayBar(state);
  if (replayBar) main.append(replayBar);
  const drawer = renderDrawer(s);
  if (drawer) main.append(drawer);
  main.append(renderTabCard(s));
  app.replaceChildren(el("div", { class: "ds-shell" }, renderSide(s), main, renderAside(s)));
}

// Defensive normalization (F-02): the server contract guarantees these
// fields, but a sparse/legacy payload must degrade, never crash the board.
function normalize(s) {
  s.pipeline = s.pipeline || { pipeline_type: "unknown", stages: [], known: false };
  s.stages = Array.isArray(s.stages) ? s.stages : [];
  for (const stage of s.stages) {
    stage.produces = Array.isArray(stage.produces) ? stage.produces : [];
  }
  s.artifacts = s.artifacts || {};
  s.media = s.media || {};
  s.media.renders = Array.isArray(s.media.renders) ? s.media.renders : [];
  s.media.snapshots = Array.isArray(s.media.snapshots) ? s.media.snapshots : [];
  s.media.music = Array.isArray(s.media.music) ? s.media.music : [];
  s.media.rig_stills = Array.isArray(s.media.rig_stills) ? s.media.rig_stills : [];
  s.events = Array.isArray(s.events) ? s.events : [];
  if (s.storyboard && Array.isArray(s.storyboard.scenes)) {
    for (const c of s.storyboard.scenes) {
      c.takes = Array.isArray(c.takes) ? c.takes : [];
      c.audio = Array.isArray(c.audio) ? c.audio : [];
      c.required_assets = Array.isArray(c.required_assets) ? c.required_assets : [];
    }
  } else {
    s.storyboard = null;
  }
  return s;
}

// Live updates without rebuilding the whole board on every disk write:
// - nothing but run status/activity changed → swap the header pill and the
//   Studio strip in place (Studio rewrites studio_run.json every few seconds);
// - board content changed → full render, but never while a clip/track on
//   the board is playing (a rebuild would reset it) — deferred until it
//   pauses or ends.
let renderedSig = null;
let pendingFull = false;

function contentSig(s) {
  const { studio_run, last_activity, live, ...rest } = s;
  const run = studio_run ? { ...studio_run } : null;
  if (run) {
    for (const k of ["last_event", "last_event_at", "last_event_at_epoch", "updated_at", "updated_at_epoch", "stalled_minutes"]) delete run[k];
  }
  return JSON.stringify([rest, run]);
}

function boardMediaPlaying() {
  return Array.from(app.querySelectorAll("video, audio")).some((m) => !m.paused && !m.ended);
}

function patchStatus(s) {
  const pill = document.getElementById("live-pill");
  if (pill) pill.replaceWith(renderLivePill(s));
  const slot = document.getElementById("studio-run-slot");
  if (slot) {
    const strip = replay ? null : renderStudioRun(s);
    slot.replaceChildren(...(strip ? [strip] : []));
  }
}

function fullRender() {
  pendingFull = false;
  renderedSig = contentSig(state);
  render();
}

async function refresh() {
  state = normalize(await getJSON(`/api/project/${encodeURIComponent(projectId)}/state`));
  const changed = contentSig(state) !== renderedSig;
  if (replay) {
    if (changed) fullRender();
    return;
  }
  if (!changed && !pendingFull) {
    patchStatus(state);
  } else if (boardMediaPlaying()) {
    pendingFull = true;
    patchStatus(state);
  } else {
    fullRender();
  }
}

for (const type of ["pause", "ended"]) {
  document.addEventListener(type, () => {
    if (pendingFull && !boardMediaPlaying()) fullRender();
  }, true);
}

refresh().catch((err) => {
  app.replaceChildren(el("div", { class: "ds-main", style: "max-width:720px;margin:80px auto" },
    el("div", { class: "ds-empty" }, "Không tìm thấy project.",
      el("small", {}, String(err)),
      el("a", { class: "ds-link-btn", href: "/" }, "← Về thư viện"))));
});
// ?static=1 disables the live feed (screenshots, static exports).
if (!new URLSearchParams(location.search).has("static")) {
  subscribe(`/api/project/${encodeURIComponent(projectId)}/events`, () => refresh().catch(console.error));
  // Elapsed/quiet timers on the Studio strip only change with the clock, not
  // with disk events — refetch every 30 s while a Studio run is active.
  setInterval(() => {
    const st = state && state.studio_run && state.studio_run.status;
    if (!replay && (st === "running" || st === "queued")) refresh().catch(console.error);
  }, 30000);
}
