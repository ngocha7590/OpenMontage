// Backlot library — OpenMontage Studio design (RenewVideo docs/design/mockups/Main.dc.html).
import { el, getJSON, subscribe, thumbURL } from "/ui/lib.js";

document.documentElement.dataset.theme = "dark"; // docs/design is dark-only

const grid = document.getElementById("grid");
const params = new URLSearchParams(location.search);
const isStatic = params.has("static");
let filter = "all";
let query = "";
let projects = [];

const STATUS = {
  run: { label: "Đang chạy", c: "#6EA8FF", bg: "rgba(110,168,255,0.14)", bar: "run" },
  wait: { label: "Chờ duyệt", c: "#F5B544", bg: "rgba(245,181,68,0.14)", bar: "wait" },
  queue: { label: "Xếp hàng", c: "#C3C9D4", bg: "rgba(179,186,198,0.14)", bar: "idle" },
  done: { label: "Hoàn tất", c: "#4CC38A", bg: "rgba(76,195,138,0.14)", bar: "" },
  fail: { label: "Lỗi", c: "#FF6B6B", bg: "rgba(255,107,107,0.14)", bar: "fail" },
  idle: { label: "Đang nghỉ", c: "#9AA3B2", bg: "rgba(154,163,178,0.12)", bar: "idle" },
};
const FILTERS = [["all", "Tất cả"], ["run", "Đang chạy"], ["wait", "Chờ duyệt"], ["done", "Hoàn tất"], ["fail", "Lỗi"]];

function libURL(q = {}) {
  const u = new URLSearchParams(q);
  if (isStatic) u.set("static", "1");
  const s = u.toString();
  return s ? `/?${s}` : "/";
}

function label(name) {
  const t = String(name || "").replace(/[_-]+/g, " ").trim();
  return t ? t.charAt(0).toUpperCase() + t.slice(1) : "";
}

function ago(epoch) {
  if (!epoch) return "";
  const d = Math.max(0, Date.now() / 1000 - epoch);
  if (d < 90) return "vừa xong";
  if (d < 3600) return `${Math.round(d / 60)} phút trước`;
  if (d < 86400) return `${Math.round(d / 3600)} giờ trước`;
  return `${Math.round(d / 86400)} ngày trước`;
}

function statusOf(p) {
  if (p.awaiting_human || p.studio_status === "waiting_approval") return "wait";
  if (p.studio_status === "running" || (p.live && p.active_stage)) return "run";
  if (p.studio_status === "queued") return "queue";
  if (p.studio_status === "failed") return "fail";
  const st = p.stage_states || [];
  if ((st.length && st.every((x) => x.status === "completed")) || p.studio_status === "succeeded") return "done";
  if (p.live) return "run";
  return "idle";
}

function badge(key) {
  const b = STATUS[key];
  return el("span", { class: "ds-badge", style: `color:${b.c};background:${b.bg}` }, b.label);
}

function poster(p, tags) {
  const box = el("div", { class: "ds-lib-poster" });
  if (p) box.append(el("img", { src: thumbURL(p.project_id, p.poster, 640), loading: "lazy", alt: "" }));
  if (tags[0]) box.append(el("span", { class: "tag l" }, tags[0]));
  if (tags[1]) box.append(el("span", { class: "tag r" }, tags[1]));
  return box;
}

function card(p) {
  const key = statusOf(p);
  const st = p.stage_states || [];
  const pct = st.length ? Math.round((p.completed_count / st.length) * 100) : 0;
  const stage = key === "done" ? "Publish" : label(p.active_stage) || (p.completed_count ? `${p.completed_count}/${st.length} bước` : "—");
  return el("a", { class: "ds-lib-card", href: `/p/${encodeURIComponent(p.project_id)}${isStatic ? "?static=1" : ""}` },
    poster(p.poster ? p : null, [p.scene_count ? `${p.scene_count} cảnh` : null, p.render_count ? `${p.render_count} bản render` : null]),
    el("div", { class: "ds-lib-body" },
      el("span", { class: "top" }, el("span", {}, p.pipeline_type && p.pipeline_type !== "unknown" ? label(p.pipeline_type) : "Chưa rõ pipeline"), badge(key)),
      el("h3", {}, p.title || p.project_id),
      el("div", { class: "ds-bar" }, el("i", { class: STATUS[key].bar, style: `width:${pct}%` })),
      el("span", { class: "foot" }, el("span", {}, `Stage: ${stage}`), el("span", {}, ago(p.last_activity))),
    ));
}

function seriesCard(name, members) {
  const lead = members.find((p) => p.poster);
  const live = members.filter((p) => statusOf(p) === "run").length;
  const waiting = members.filter((p) => statusOf(p) === "wait").length;
  const latest = Math.max(...members.map((p) => p.last_activity || 0));
  return el("a", { class: "ds-lib-card", href: libURL({ series: name }) },
    poster(lead || null, [`${members.length} project`, null]),
    el("div", { class: "ds-lib-body" },
      el("span", { class: "top" }, el("span", {}, "Series"),
        waiting ? badge("wait") : live ? badge("run") : null),
      el("h3", {}, name),
      el("span", { class: "foot" }, el("span", {}, `${members.length} project`), el("span", {}, ago(latest))),
    ));
}

function renderSide() {
  const side = document.getElementById("side");
  const logo = el("span", { class: "ds-logo" });
  logo.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="9" width="18" height="12" rx="2"></rect><path d="M3 9l3-6h14l-2 6"></path><path d="M9 3l-2 6M15 3l-2 6"></path></svg>';
  const icon = el("span", { "aria-hidden": "true", style: "display:flex" });
  icon.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7" rx="1.5"></rect><rect x="14" y="3" width="7" height="7" rx="1.5"></rect><rect x="3" y="14" width="7" height="7" rx="1.5"></rect><rect x="14" y="14" width="7" height="7" rx="1.5"></rect></svg>';
  side.replaceChildren(
    el("a", { class: "ds-brand", href: libURL() }, logo,
      el("span", { class: "ds-brand-name" }, el("b", {}, "OpenMontage"), el("span", {}, "Backlot"))),
    el("a", { class: "ds-nav-link", href: libURL(), "aria-current": "page" }, icon, el("span", { class: "ds-nav-text" }, "Thư viện")),
  );
}

function renderFilters(counts) {
  document.getElementById("filters").replaceChildren(...FILTERS.map(([k, text]) => el("button", {
    type: "button", class: "ds-pill", "aria-pressed": String(filter === k),
    onclick: () => { filter = k; draw(); },
  }, k === "all" ? text : `${text}${counts[k] ? ` · ${counts[k]}` : ""}`)));
}

function renderNav(current, bySeries, view) {
  const nav = document.getElementById("nav");
  if (!bySeries.size && !current) { nav.hidden = true; return; }
  nav.hidden = false;
  const pill = (href, text, on) => el("a", { class: "ds-pill", href, "aria-pressed": String(on), style: "display:inline-flex;align-items:center;text-decoration:none" }, text);
  if (current) {
    nav.replaceChildren(pill(libURL(), "← Tất cả project", false), pill(libURL({ view: "series" }), "Mọi series", false), pill(libURL({ series: current }), current, true));
  } else {
    nav.replaceChildren(pill(libURL(), "Project", view !== "series"), pill(libURL({ view: "series" }), `Series · ${bySeries.size}`, view === "series"));
  }
}

function matches(p) {
  if (filter !== "all") {
    const k = statusOf(p);
    if (filter === "run" ? !(k === "run" || k === "queue") : k !== filter) return false;
  }
  if (!query) return true;
  const hay = `${p.title || ""} ${p.project_id} ${p.pipeline_type || ""}`.toLowerCase();
  return hay.includes(query);
}

function draw() {
  const bySeries = new Map();
  const items = [];
  for (const p of projects) {
    if (!p.series) { items.push(p); continue; }
    if (!bySeries.has(p.series)) { bySeries.set(p.series, []); items.push({ series: p.series }); }
    bySeries.get(p.series).push(p);
  }
  const current = params.get("series");
  const view = params.get("view");
  const pool = current ? (bySeries.get(current) || []) : projects;

  const counts = {};
  for (const p of pool) { const k = statusOf(p) === "queue" ? "run" : statusOf(p); counts[k] = (counts[k] || 0) + 1; }
  renderFilters(counts);
  renderNav(current, bySeries, view);

  document.getElementById("title").textContent = current || (view === "series" ? "Series" : "Thư viện");
  document.title = current ? `Backlot — ${current}` : "Backlot — Thư viện";
  const running = pool.filter((p) => statusOf(p) === "run").length;
  const lb = document.getElementById("liveBadge");
  const b = running ? STATUS.run : STATUS.idle;
  lb.style.color = b.c; lb.style.background = b.bg;
  document.getElementById("liveText").textContent = running ? `Đang chạy ${running}` : "Đang nghỉ";

  const empty = document.getElementById("empty");
  grid.replaceChildren();
  let shown = 0;
  if (view === "series" && !current) {
    for (const [name, members] of bySeries) { grid.append(seriesCard(name, members)); shown += 1; }
    empty.textContent = "Chưa có series nào — thêm nhãn \"series\" vào project.json của project.";
  } else if (current || filter !== "all" || query) {
    for (const p of pool.filter(matches)) { grid.append(card(p)); shown += 1; }
    empty.textContent = pool.length ? "Không có project nào khớp bộ lọc." : `Series "${current}" chưa có project nào.`;
  } else {
    for (const item of items) {
      grid.append(item.project_id ? card(item) : seriesCard(item.series, bySeries.get(item.series)));
      shown += 1;
    }
    empty.textContent = "Chưa có project nào — chạy một production là project sẽ hiện ở đây.";
  }
  empty.style.display = shown ? "none" : "flex";
}

async function load() {
  projects = await getJSON("/api/projects");
  draw();
}

renderSide();
document.getElementById("q").addEventListener("input", (e) => { query = e.target.value.trim().toLowerCase(); draw(); });
load().catch(console.error);
if (!isStatic) subscribe("/api/library/events", () => load().catch(console.error));
