// Parent app. Code-calculated facts render immediately; AI text fills in where the
// feature is available, otherwise a short note says why it is missing.
"use strict";

const $ = (id) => document.getElementById(id);
const PAGES = ["ask", "overview", "alerts", "week", "topics", "papers", "settings"];
// Pages that moved onto Overview: their old links open Overview at that part.
const MOVED = { advice: "ov-advice" };
const DEFAULT_PAGE = "overview";
const CAUSES = {
  understanding: "Understanding", effort: "Effort", mixed: "Effort and understanding",
  broad_understanding: "Broad understanding", outside_school: "Something outside school", on_track: "On track",
};
const STATUS = { graded: "Graded", missing: "Missing", awaiting_grade: "Awaiting grade", upcoming: "Upcoming", excused: "Excused" };
const PROVIDERS = { huggingface: "Hugging Face", anthropic: "Claude" };
const KIND_LABELS = { homework: "Everyday work", test: "Assessment", other: "Other" };
// Used when the child's data is not loaded, or does not suggest enough questions of its own.
const SUGGESTIONS = [
  "What is missing right now?",
  "Which subject needs the most help?",
  "What changed this week?",
  "Is this about effort or understanding?",
  "What tests are coming up?",
];

// paperFiles: previews of files uploaded in this browser session, by paper id. Never sent back to the server.
const state = { me: null, childId: null, dash: null, loaded: {}, papers: [], paper: null, paperFiles: {} };

// ---------- small helpers ----------

function el(tag, attrs = {}, children = []) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "text") n.textContent = v; else if (k === "class") n.className = v; else n.setAttribute(k, v === true ? "" : v);
  }
  for (const c of [].concat(children)) if (c !== null && c !== undefined) n.append(c);
  return n;
}
const td = (text, cls) => el("td", { text: text ?? "–", class: cls });
// Each course keeps one colour everywhere. Distinct colours are handed out in name order,
// so two courses of one child never share a colour (until there are more than ten).
const COURSE_COLORS = ["#6366f1", "#0ea5e9", "#10b981", "#f59e0b", "#f43f5e", "#a855f7", "#14b8a6", "#f97316", "#84cc16", "#ec4899"];
let courseColors = new Map();
function setCourseColors(names) {
  courseColors = new Map([...new Set(names.filter(Boolean))].sort().map((n, i) => [n, COURSE_COLORS[i % COURSE_COLORS.length]]));
}
function courseDot(name) {
  const dot = el("span", { class: "course-dot" });
  dot.style.background = courseColors.get(name) || "var(--muted)";
  return dot;
}
const courseCell = (name) => el("td", {}, el("span", { class: "course-cell" }, [courseDot(name), el("span", { text: name ?? "–" })]));
const num = (v, suffix = "") => (v === null || v === undefined ? "–" : `${v}${suffix}`);
const pct = (r) => (r === null || r === undefined ? "–" : Math.round(r * 100) + "%");
// Date-only values ("2026-09-28") are read at local noon so they never shift to the previous day.
const toDate = (iso) => new Date(/^\d{4}-\d{2}-\d{2}$/.test(iso) ? iso + "T12:00:00" : iso);
const date = (iso) => (iso ? toDate(iso).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" }) : "–");
const when = (iso) => (iso ? new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "never");
function change(v) {
  if (v === null || v === undefined) return td("–", "num");
  return td((v > 0 ? "+" : "") + v, "num " + (v < 0 ? "down" : v > 0 ? "up" : ""));
}
function emptyRow(tbody, cols, text) { tbody.replaceChildren(el("tr", {}, el("td", { colspan: cols, class: "empty", text }))); }
function say(text, kind = "warn") { const m = $("message"); m.textContent = text || ""; m.className = "banner " + kind; m.hidden = !text; }
function busy(button, on, label) {
  if (on) { button.dataset.label = button.textContent; button.textContent = label; button.disabled = true; }
  else { button.textContent = button.dataset.label || button.textContent; button.disabled = false; }
}

async function api(path, options = {}) {
  const init = { ...options };
  if (options.json !== undefined) {
    init.body = JSON.stringify(options.json);
    init.headers = { "Content-Type": "application/json" };
    delete init.json;
  }
  const r = await fetch(path, init);
  if (r.status === 401 && path === "/api/me") { location.replace("/"); throw new Error("signed out"); }
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(body.detail || r.statusText), { status: r.status });
  return body;
}
const childUrl = (path) => `/api/children/${state.childId}${path}`;

// Why an AI text is absent, in words a parent can read.
function aiNote(error, action = "write an explanation") {
  if (!error) return "";
  if (error === "not_implemented") return `This AI feature isn't switched on yet, so it can't ${action}.`;
  if (error.startsWith("not_configured")) return `AI isn't set up yet, so it can't ${action}.`;
  return `The AI couldn't ${action} right now: ${error}`;
}
function aiBlock(text, error, label = "AI explanation", action) {
  if (text) return el("div", { class: "ai" }, [el("span", { class: "ai-label", text: label }), el("p", { text })]);
  return el("div", { class: "ai missing", text: aiNote(error, action) || "No explanation yet." });
}

// ---------- shell: profile, children, navigation ----------

function renderShell() {
  const { me } = state;
  const child = me.children.find((c) => c.id === state.childId);
  const picker = $("child-picker");
  picker.hidden = me.children.length < 2;
  picker.replaceChildren(...me.children.map((c) => new Option(c.name, c.id)));
  if (child) picker.value = String(child.id);
  $("child-name").textContent = me.children.length === 1 && child ? child.name : "";
  document.querySelectorAll(".child-name-inline").forEach((n) => { n.textContent = child ? child.name : "your child"; });
  $("no-children").hidden = me.children.length > 0;

  const s = me.schoology;
  $("reconnect").hidden = s.state !== "reconnect";
  $("reconnect-text").textContent = s.reason || "";
  $("sync").hidden = s.state === "reconnect";
}

function route() {
  const [page, param] = location.hash.replace(/^#\/?/, "").split("/");
  if (MOVED[page]) return { page: "overview", param: MOVED[page] };
  return { page: PAGES.includes(page) ? page : DEFAULT_PAGE, param: param || null };
}
const currentPage = () => route().page;

function showPage() {
  const page = currentPage();
  say("");
  document.querySelectorAll("[data-view]").forEach((v) => { v.hidden = v.dataset.view !== page || !state.childId && page !== "settings"; });
  document.querySelectorAll(".tabs-nav a").forEach((a) => {
    if (a.dataset.page === page) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
  loadPage(page).then(() => {
    if (page === "papers") showPaperRoute();
    const part = page === "overview" && route().param && $(route().param);
    if (part) { if (part.tagName === "DETAILS") part.open = true; part.scrollIntoView({ block: "start" }); }
  });
}

// Each page loads once per child; Sync and actions reload what they change.
async function loadPage(page, force = false) {
  if (page !== "settings" && !state.childId) return;
  if (state.loaded[page] && !force) return;
  state.loaded[page] = true;
  try {
    await ({ overview: loadOverviewPage, alerts: loadAlerts, week: loadWeek, topics: loadTopics, papers: loadPapers,
             ask: loadChat, settings: loadSettings })[page]();
  } catch (e) {
    state.loaded[page] = false;
    if (e.message !== "signed out") say("Could not load this page: " + e.message, "bad");
  }
}

async function dashboard(force = false) {
  if (!state.dash || force) {
    if (!state.dashLoading || force) state.dashLoading = api(childUrl("/dashboard")).finally(() => { state.dashLoading = null; });
    state.dash = await state.dashLoading;
  }
  return state.dash;
}

// Overview: the numbers, this week's summary, how to help, and unread alerts. One failing part leaves the rest.
async function loadOverviewPage() {
  const parts = await Promise.allSettled([loadOverview(), loadOverviewWeek(), loadAdvice(), loadOverviewAlerts()]);
  const failed = parts.find((p) => p.status === "rejected");
  if (failed) throw failed.reason;
}

// ---------- Overview (code only) ----------

async function loadOverview() {
  const d = await dashboard();
  const o = d.overview;
  setCourseColors([...o.courses.map((c) => c.title), ...d.assignments.map((a) => a.course)]);
  $("synced").textContent = "Last synced " + when(d.child.last_synced_at);
  $("tile-cause").textContent = CAUSES[d.cause.cause];
  $("tile-missing").textContent = o.missing.length;
  $("tile-late").textContent = o.late.length;
  $("tile-upcoming").textContent = o.upcoming.length;
  renderCourses();

  const item = (a) => el("li", {}, [courseDot(a.course), el("span", { text: a.title }), el("span", { class: "hint", text: ` · ${a.course} · due ${date(a.due_at)}` })]);
  $("missing").replaceChildren(...(o.missing.length ? o.missing.map(item) : [el("li", { class: "empty", text: "Nothing missing." })]));
  $("upcoming").replaceChildren(...(o.upcoming.length ? o.upcoming.map(item) : [el("li", { class: "empty", text: "Nothing due." })]));

  const filter = $("filter-course"), current = filter.value;
  const courseNames = [...new Set(d.assignments.map((a) => a.course))].sort();
  filter.replaceChildren(new Option("All courses", ""), ...courseNames.map((c) => new Option(c, c)));
  filter.value = courseNames.includes(current) ? current : "";
  renderAssignments();

  const cats = $("categories");
  if (!d.categories.length) emptyRow(cats, 4, "No grading categories.");
  else cats.replaceChildren(...d.categories.map((c) => el("tr", {}, [courseCell(c.course), td(c.title), td(KIND_LABELS[c.kind]), td(num(c.weight), "num")])));
}

function renderCourses() {
  const courses = state.dash.overview.courses;
  const showEmpty = $("show-empty-courses").checked;
  const rows = courses.filter((c) => showEmpty || c.average !== null || c.missing_count || c.awaiting_grade_count);
  const body = $("courses");
  if (!rows.length) { emptyRow(body, 7, "No graded courses yet. Try Sync."); return; }
  body.replaceChildren(...rows.map((c) => el("tr", {}, [
    courseCell(c.title),
    el("td", { class: "num", title: c.average_source === "schoology"
      ? "Schoology's grade" + (c.official_grade_label ? " for " + c.official_grade_label : "") : "Calculated from graded work" },
      [num(c.average), c.average_source === "calculated" ? el("span", { class: "hint", text: " calc." }) : null]),
    change(c.change_7_days), change(c.change_14_days),
    td(c.missing_count, "num"), td(c.late_count, "num"), td(c.awaiting_grade_count, "num"),
  ])));
}

function renderAssignments() {
  const d = state.dash;
  const course = $("filter-course").value, status = $("filter-status").value;
  const rows = d.assignments.filter((a) => (!course || a.course === course) && (!status || (status === "late" ? a.late : a.status === status)));
  if (state.dueSort) {
    // Earliest or latest first; work with no due date always goes last.
    const dir = state.dueSort === "asc" ? 1 : -1;
    const time = (a) => (a.due_at ? toDate(a.due_at).getTime() : null);
    rows.sort((x, y) => {
      const a = time(x), b = time(y);
      if (a === null || b === null) return (a === null) - (b === null);
      return (a - b) * dir;
    });
  }
  $("sort-due-arrow").textContent = { asc: "↑", desc: "↓" }[state.dueSort] || "↕";
  $("due-header").setAttribute("aria-sort", { asc: "ascending", desc: "descending" }[state.dueSort] || "none");
  const body = $("assignments");
  if (!rows.length) emptyRow(body, 7, "No assignments match.");
  else body.replaceChildren(...rows.map((a) => {
    const chips = [el("span", { class: "chip " + ({ missing: "bad", graded: "ok", awaiting_grade: "warn", upcoming: "info" }[a.status] || ""), text: STATUS[a.status] })];
    if (a.late) chips.push(" ", el("span", { class: "chip warn", text: "Late" }));
    const score = a.score === null ? "–" : `${a.score}/${a.max_points}` + (a.percent !== null ? ` (${a.percent}%)` : "");
    return el("tr", {}, [td(a.title), courseCell(a.course), td(a.category), td(date(a.due_at), "nowrap"), td(score, "num"),
      el("td", {}, chips), td(a.teacher_comment || "")]);
  }));
  $("assignment-count").textContent = `${rows.length} of ${d.assignments.length} assignments`;
}

// ---------- Alerts (rules + AI line) ----------

// What an alert says when the AI has not written its line. Numbers come from the facts.
function alertFallback(a) {
  const f = a.facts || {};
  switch (a.rule) {
    case "missing_work": return `${f.title} (${f.course}) is marked missing.`;
    case "low_test_score": return `${f.title} (${f.course}) scored ${f.percent}%, below ${f.threshold}%.`;
    case "average_drop": return `${f.course} average moved from ${f.average_7_days_ago} to ${f.average_now} in a week.`;
    case "new_weak_topic": return `New weak topic: ${f.topic}, missed in ${f.assignment_count} assignments.`;
    default: return "Something changed.";
  }
}
const ALERT_TITLES = { missing_work: "Missing work", low_test_score: "Low test score", average_drop: "Grade drop", new_weak_topic: "Weak topic" };

async function loadAlerts() {
  const alerts = await api(childUrl("/alerts"));
  updateBadge(alerts);
  renderAlerts($("alert-list"), alerts, "No alerts. New ones appear after a sync.");
}

async function loadOverviewAlerts() {
  const alerts = await api(childUrl("/alerts"));
  updateBadge(alerts);
  renderAlerts($("ov-alerts"), alerts.filter((a) => !a.read_at), "Nothing new. You're all caught up.");
}

// After marking one read, refresh both lists so they agree.
function reloadAlerts() {
  state.loaded.alerts = false;
  loadAlerts().catch(() => {});
  if (state.childId) loadOverviewAlerts().catch(() => {});
}

function renderAlerts(list, alerts, emptyText) {
  if (!alerts.length) { list.replaceChildren(el("li", { class: "empty", text: emptyText })); return; }
  list.replaceChildren(...alerts.map((a) => {
    const read = el("button", { class: "small", text: "Mark read" });
    read.addEventListener("click", async () => {
      read.disabled = true;
      await api(childUrl(`/alerts/${a.id}/read`), { method: "POST" });
      reloadAlerts();
    });
    return el("li", { class: a.read_at ? "read" : "" }, [
      el("span", { class: "dot", "aria-hidden": "true" }),
      el("div", {}, [
        el("div", { class: "alert-meta", text: `${ALERT_TITLES[a.rule] || a.rule} · ${when(a.created_at)}` }),
        el("div", { class: "alert-text", text: a.message || alertFallback(a) }),
        a.message ? null : el("div", { class: "hint", text: "Written from the rule's facts; the AI line isn't available yet." }),
      ]),
      a.read_at ? el("span") : read,
    ]);
  }));
}

function updateBadge(alerts) {
  const unread = alerts.filter((a) => !a.read_at).length;
  $("alert-badge").textContent = unread;
  $("alert-badge").hidden = unread === 0;
}

// ---------- This week (AI summary) ----------

// The summaries, writing this week's first if it is missing. Shared by Overview and the This week tab,
// so opening both never asks the AI twice.
function weekSummaries(onWriting) {
  if (!state.weekLoading) {
    state.weekLoading = (async () => {
      let summaries = await api(childUrl("/summaries"));
      let error = null;
      if (!(summaries[0] && isThisWeek(summaries[0].week_start))) {
        // A new week with no summary yet: write it now. Without force this never replaces an existing one.
        onWriting();
        error = (await api(childUrl("/summaries/generate"), { method: "POST" })).error;
        summaries = await api(childUrl("/summaries"));
      }
      return { summaries, error };
    })().finally(() => { state.weekLoading = null; });
  }
  return state.weekLoading;
}
const writingNote = () => el("div", { class: "ai missing", text: "Writing this week's summary…" });

async function loadOverviewWeek() {
  const { summaries, error } = await weekSummaries(() => $("ov-week").replaceChildren(writingNote()));
  const latest = summaries[0];
  $("ov-week").replaceChildren(latest && isThisWeek(latest.week_start)
    ? aiBlock(latest.text, null, `Week of ${date(latest.week_start)}`)
    : aiBlock(null, error, "This week's summary", "write the weekly summary"));
}

async function loadWeek() {
  const { summaries, error } = await weekSummaries(() => $("week-current").replaceChildren(writingNote()));
  const [latest, ...older] = summaries;
  const thisWeek = latest && isThisWeek(latest.week_start);
  $("week-current").replaceChildren(thisWeek
    ? aiBlock(latest.text, null, `Week of ${date(latest.week_start)}`)
    : aiBlock(null, error, "This week's summary", "write the weekly summary"));
  $("week-generate").textContent = thisWeek ? "Rewrite this week's summary" : "Write this week's summary";
  const history = thisWeek ? older : summaries;
  $("week-history").replaceChildren(...(history.length
    ? history.map((s) => el("details", {}, [el("summary", { text: `Week of ${date(s.week_start)}` }), el("p", { class: "week-text", text: s.text })]))
    : [el("p", { class: "empty", text: "No earlier summaries." })]));
}

// week_start is a Monday; it is this week's summary if that Monday was under 7 days ago.
function isThisWeek(weekStart) {
  return Date.now() - toDate(weekStart).getTime() < 7 * 24 * 60 * 60 * 1000;
}

// ---------- Topics (AI labels, code counts) ----------

async function loadTopics() {
  const w = (await dashboard()).weak_topics;
  $("topics-hint").textContent = `A topic becomes a weak topic when it is missed in ${w.threshold}+ different assignments.`;
  $("topics-label").hidden = !w.unlabelled_marks;
  $("topics-label").textContent = `Label ${w.unlabelled_marks} lost marks now`;
  const parts = [];
  if (w.unlabelled_marks) parts.push(el("div", { class: "ai missing",
    text: `${w.unlabelled_marks} lost marks are waiting for the AI to label their topic. Topics appear once they are labelled.` }));
  if (!w.topics.length && !w.unlabelled_marks) parts.push(el("p", { class: "empty", text: "No lost marks recorded yet." }));
  for (const t of w.topics) {
    const examples = t.examples.map((x) => el("div", { class: "example" }, [
      el("div", { text: x.question ? x.question : x.assignment }),
      el("div", { class: "hint", text: [x.question ? x.assignment : null, x.course,
        x.student_answer ? `answered ${x.student_answer}` : null, `lost ${x.points_lost} of ${x.points_possible}`].filter(Boolean).join(" · ") }),
      x.teacher_comment ? el("div", { class: "hint", text: `Teacher: ${x.teacher_comment}` }) : null,
    ]));
    parts.push(el("div", { class: "topic" }, [
      el("div", { class: "topic-head" }, [
        el("div", {}, [el("b", { text: t.topic }), " ", t.flagged ? el("span", { class: "chip warn", text: "Weak topic" }) : null]),
        el("span", { class: "hint", text: `${t.courses.join(", ")} · ${t.assignment_count} assignment${t.assignment_count === 1 ? "" : "s"} · ${t.points_lost} points lost` }),
      ]),
      examples.length ? el("details", {}, [el("summary", { class: "hint", text: "Example mistakes" }), ...examples]) : null,
    ]));
  }
  $("topics").replaceChildren(...parts);
}

// ---------- Advice (rules + AI explanations) ----------

async function loadAdvice() {
  const cause = (await dashboard()).cause;
  $("cause").textContent = CAUSES[cause.cause];
  $("suggestion").textContent = cause.suggestion;
  $("suggestion").hidden = cause.cause === "on_track";  // nothing to do, so the verdict says it all
  $("fold-cause").textContent = CAUSES[cause.cause];
  $("cause-explanation").replaceChildren(el("div", { class: "ai missing", text: "Writing an explanation…" }));
  $("cause-courses").replaceChildren(...cause.courses.filter((c) => c.assignments_counted).map((c) => el("tr", {}, [
    courseCell(c.course), el("td", {}, el("span", { class: "chip " + (c.cause === "on_track" ? "ok" : "warn"), text: CAUSES[c.cause] })),
    td(pct(c.missing_or_late_rate), "num"), td(num(c.completed_average), "num"), td(num(c.test_average), "num"),
    td(c.weak_topics.join(", ") || "–"),
  ])));
  $("gap").replaceChildren(el("p", { class: "hint", text: "Loading…" }));

  loadPaperAdvice();
  const [advice, gap] = await Promise.all([api(childUrl("/advice")), api(childUrl("/gap"))]);
  $("cause-explanation").replaceChildren(aiBlock(advice.explanation, advice.ai_error));

  const flagged = gap.courses.filter((g) => g.flagged).length;
  $("fold-gap").textContent = !gap.courses.length ? "" : flagged ? `${flagged} course${flagged > 1 ? "s" : ""} to look at` : "No big gaps";
  if (!gap.courses.length) {
    $("gap").replaceChildren(el("p", { class: "empty", text: "Needs grades for both everyday work and assessments in a course. Categories that are neither (for example standards-based ones) are left out." }));
    return;
  }
  const table = el("table", {}, [
    el("thead", {}, el("tr", {}, [el("th", { text: "Course" }), el("th", { class: "num", text: "Everyday work" }),
      el("th", { class: "num", text: "Assessments" }), el("th", { class: "num", text: "Gap" })])),
    el("tbody", {}, gap.courses.map((g) => el("tr", {}, [td(g.course), td(num(g.homework_average), "num"), td(num(g.test_average), "num"),
      el("td", { class: "num" }, g.flagged ? el("span", { class: "chip warn", text: String(g.gap) }) : String(g.gap))]))),
  ]);
  const explained = gap.courses.filter((g) => g.flagged).map((g) => el("div", {}, [
    el("p", { text: g.course, class: "hint" }), aiBlock(g.explanation, g.ai_error),
  ]));
  $("gap").replaceChildren(el("div", { class: "table-wrap" }, table),
    el("p", { class: "hint", text: `Everyday work is homework, classwork and performance tasks; assessments are tests, quizzes and summatives. Highlighted when everyday work is ${gap.courses[0].threshold}+ points above assessments.` }), ...explained);
}

// ---------- Papers (AI reads, parent checks, AI advises) ----------

async function loadPapers() {
  const d = await dashboard();
  const courses = [...d.overview.courses].sort((a, b) =>
    (b.average !== null) - (a.average !== null) || a.title.localeCompare(b.title));
  $("paper-course").replaceChildren(new Option("Choose a subject", ""), ...courses.map((c) => new Option(c.title, c.course_id)));
  fillAssignments();
  await loadPaperList();
}

function fillAssignments() {
  const d = state.dash, courseId = Number($("paper-course").value);
  const course = d.overview.courses.find((c) => c.course_id === courseId);
  const items = course ? d.assignments.filter((a) => a.course === course.title && a.source !== "paper") : [];
  const label = (a) => a.title + (a.percent !== null ? ` (${a.percent}%)` : "") + (a.due_at ? ` · ${date(a.due_at)}` : "");
  $("paper-assignment").replaceChildren(
    new Option(course ? "Choose an assignment" : "Choose a subject first", ""),
    ...items.map((a) => new Option(label(a), a.assignment_id)),
    ...(course ? [new Option("Not in Schoology: type a name", "new")] : []),
  );
  $("paper-assignment").disabled = !course;
  showTitleBox();
}
function showTitleBox() {
  const isNew = $("paper-assignment").value === "new";
  $("paper-title-wrap").hidden = !isNew;
  $("paper-title").required = isNew;
}

function previewTiles(items) {
  return items.map((f) => f.url
    ? el("img", { src: f.url, alt: f.name })
    : el("div", { class: "file-chip", text: f.name }));
}
function filePreviews(files) {
  return [...files].map((f) => ({
    name: f.name,
    url: f.type.startsWith("image/") && !/hei[cf]/i.test(f.type) ? URL.createObjectURL(f) : null,
  }));
}

async function loadPaperList() {
  state.papers = await api(childUrl("/papers"));
  const list = $("paper-list");
  if (!state.papers.length) { list.replaceChildren(el("p", { class: "empty", text: "No papers uploaded yet." })); return; }
  list.replaceChildren(...state.papers.map((p) => {
    const item = el("div", { class: "paper-item", role: "link", tabindex: "0" }, [
      el("div", {}, [el("b", { text: p.title }), el("div", { class: "hint", text: `${p.subject} · uploaded ${date(p.created_at)}` })]),
      el("div", {}, [
        p.totals.percent !== null ? el("span", { class: "hint", text: `${p.totals.earned}/${p.totals.possible} (${p.totals.percent}%) ` }) : null,
        el("span", { class: "chip " + (p.status === "confirmed" ? "ok" : "warn"), text: p.status === "confirmed" ? "Confirmed" : "Needs checking" }),
      ]),
    ]);
    const open = () => { location.hash = `#/papers/${p.id}`; };
    item.addEventListener("click", open);
    item.addEventListener("keydown", (e) => { if (e.key === "Enter") open(); });
    return item;
  }));
}

async function showPaperRoute() {
  const id = Number(route().param);
  $("paper-upload").hidden = $("paper-list-card").hidden = Boolean(id);
  $("paper-view").hidden = !id;
  if (!id) return;
  try { renderPaper(await api(childUrl(`/papers/${id}`))); }
  catch (e) { say("Could not open this paper: " + e.message, "bad"); location.hash = "#/papers"; }
}

function questionRow(q = {}) {
  const input = (name, value, cls, type = "text") => el("td", { class: cls },
    el("input", { name, type, value: value ?? "", step: type === "number" ? "any" : undefined, min: type === "number" ? "0" : undefined,
                  "aria-label": name.replaceAll("_", " ") }));
  const remove = el("button", { type: "button", class: "small", text: "✕", "aria-label": "Remove question" });
  const row = el("tr", {}, [
    input("label", q.label, "", "text"), input("question", q.question), input("student_answer", q.student_answer),
    input("points_earned", q.points_earned, "num", "number"), input("points_possible", q.points_possible, "num", "number"),
    input("teacher_comment", q.teacher_comment), el("td", {}, remove),
  ]);
  remove.addEventListener("click", () => { row.remove(); updateTotal(); });
  row.addEventListener("input", updateTotal);
  return row;
}

function readQuestions() {
  return [...$("paper-questions").querySelectorAll("tr")].map((tr) => {
    const v = (n) => tr.querySelector(`[name=${n}]`).value.trim();
    const n = (k) => (v(k) === "" ? null : Number(v(k)));
    return { label: v("label") || null, question: v("question") || null, student_answer: v("student_answer") || null,
             teacher_comment: v("teacher_comment") || null, points_earned: n("points_earned"), points_possible: n("points_possible") };
  });
}

// Shown while editing; the server recalculates totals when saving.
function updateTotal() {
  const qs = readQuestions().filter((q) => q.points_earned !== null && q.points_possible);
  const earned = qs.reduce((s, q) => s + q.points_earned, 0), possible = qs.reduce((s, q) => s + q.points_possible, 0);
  $("paper-total").textContent = possible ? `Total ${earned}/${possible} (${Math.round(1000 * earned / possible) / 10}%)` : "";
}

function renderPaper(p) {
  state.paper = p;
  $("paper-heading").textContent = p.title;
  $("paper-sub").textContent = `${p.subject} · ${p.page_count} page${p.page_count === 1 ? "" : "s"} · uploaded ${date(p.created_at)}`
    + (p.in_schoology ? " · in Schoology" : " · not in Schoology");
  const files = state.paperFiles[p.id];
  $("paper-view-previews").replaceChildren(...(files ? previewTiles(files)
    : [el("p", { class: "hint", text: "The original file isn't kept. Use your own copy to compare." })]));

  const note = [];
  if (p.status === "review") {
    note.push(p.read_error
      ? el("div", { class: "ai missing", text: aiNote(p.read_error, "read the paper") + " Enter the questions and marks below instead." })
      : el("div", { class: "ai missing", text: "Check every row against the paper and fix anything read wrongly, then confirm." }));
  }
  $("paper-read-note").replaceChildren(...note);

  const editable = p.status === "review";
  $("paper-questions").replaceChildren(...(p.questions.length ? p.questions : editable ? [{}] : []).map((q) => questionRow(q)));
  $("paper-questions").querySelectorAll("input").forEach((i) => { i.disabled = !editable; });
  $("paper-questions").querySelectorAll("button").forEach((b) => { b.hidden = !editable; });
  $("paper-add-row").hidden = !editable;
  updateTotal();

  const actions = [];
  const button = (text, cls, handler) => { const b = el("button", { class: "small " + cls, text }); b.addEventListener("click", () => handler(b)); return b; };
  if (editable) {
    actions.push(button("Save", "", (b) => savePaper(b)));
    actions.push(button("Confirm and get advice", "primary", (b) => confirmPaper(b)));
  } else {
    actions.push(button("Edit results", "", () => { renderPaper({ ...p, status: "review", read_error: null }); }));
    actions.push(button("Rewrite advice", "", (b) => paperAction(b, "Writing…", () => api(childUrl(`/papers/${p.id}/advice`), { method: "POST" }))));
  }
  actions.push(button("Delete paper", "", async (b) => {
    if (!confirm("Delete this paper and its results?")) return;
    await paperAction(b, "Deleting…", () => api(childUrl(`/papers/${p.id}`), { method: "DELETE" }));
    afterPaperChange(); location.hash = "#/papers";
  }));
  $("paper-actions").replaceChildren(...actions);
  $("paper-advice").replaceChildren(...(p.status === "confirmed"
    ? [el("h2", { text: "Advice for this assignment" }), aiBlock(p.advice, p.advice_error, "AI advice", "write advice")] : []));
}

async function paperAction(button, label, fn) {
  busy(button, true, label);
  try { const r = await fn(); if (r && r.id) renderPaper(r); return r; }
  catch (e) { say(e.message, "bad"); }
  finally { busy(button, false); }
}
const savePaper = (b) => paperAction(b, "Saving…", () => api(childUrl(`/papers/${state.paper.id}/questions`), { method: "PUT", json: { questions: readQuestions() } }))
  .then((r) => { if (r) { say("Saved.", "ok"); afterPaperChange(); } });
async function confirmPaper(b) {
  busy(b, true, "Confirming…");
  try {
    await api(childUrl(`/papers/${state.paper.id}/questions`), { method: "PUT", json: { questions: readQuestions() } });
    renderPaper(await api(childUrl(`/papers/${state.paper.id}/confirm`), { method: "POST" }));
    say("Confirmed. Weak topics and advice now include this paper.", "ok");
    afterPaperChange();
  } catch (e) { say(e.message, "bad"); }
  finally { busy(b, false); }
}
// Confirmed results change topics, advice and the dashboard, so those reload next time.
function afterPaperChange() { state.dash = null; ["overview", "topics"].forEach((k) => { state.loaded[k] = false; }); }

async function uploadPaper(ev) {
  ev.preventDefault();
  const files = $("paper-files").files;
  const assignment = $("paper-assignment").value;
  const form = new FormData();
  form.append("course_id", $("paper-course").value);
  if (assignment === "new") form.append("title", $("paper-title").value); else form.append("assignment_id", assignment);
  for (const f of files) form.append("files", f);
  const b = $("paper-read"); busy(b, true, "Reading the paper…");
  try {
    const r = await fetch(childUrl("/papers"), { method: "POST", body: form });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.detail || r.statusText);
    state.paperFiles[body.id] = filePreviews(files);
    $("paper-form").reset(); $("paper-previews").replaceChildren(); fillAssignments();
    state.loaded.papers = true; await loadPaperList();
    location.hash = `#/papers/${body.id}`;
  } catch (e) { say("Upload failed: " + e.message, "bad"); }
  finally { busy(b, false); }
}

async function loadPaperAdvice() {
  const list = $("paper-advice-list");
  try {
    const papers = (await api(childUrl("/papers"))).filter((p) => p.status === "confirmed");
    $("fold-papers").textContent = papers.length ? `${papers.length} paper${papers.length > 1 ? "s" : ""}` : "";
    if (!papers.length) { list.replaceChildren(el("p", { class: "empty", text: "No confirmed papers yet. Upload one on the Papers page." })); return; }
    list.replaceChildren(...papers.map((p) => el("div", { class: "topic" }, [
      el("div", { class: "topic-head" }, [
        el("a", { href: `#/papers/${p.id}`, text: p.title }),
        el("span", { class: "hint", text: `${p.subject} · ${p.totals.earned}/${p.totals.possible} (${p.totals.percent}%) · ${date(p.confirmed_at)}` }),
      ]),
      aiBlock(p.advice, p.advice_error, "AI advice", "write advice"),
    ])));
  } catch (e) { list.replaceChildren(el("p", { class: "empty", text: "Couldn't load paper advice: " + e.message })); }
}

// ---------- Ask (chat agent) ----------

async function loadChat() {
  const [history, d] = await Promise.all([api(childUrl("/chat")), dashboard().catch(() => null)]);
  if (d) setCourseColors([...d.overview.courses.map((c) => c.title), ...d.assignments.map((a) => a.course)]);
  state.chatSuggestions = d ? suggestedQuestions(d) : SUGGESTIONS.map((text) => ({ text }));
  state.asked = new Set(history.filter((m) => m.role === "user").map((m) => m.text.trim().toLowerCase()));
  $("chat-log").replaceChildren(...(history.length ? history.map(chatBubble) : [chatWelcome(d)]));
  renderSuggestions(true);
  scrollChat();
}

// "PHYSICS 1 AP A" reads better as "Physics 1 AP A"; short codes like AP, IB and III stay as they are.
function niceCourse(name) {
  if (!name || name !== name.toUpperCase()) return name;
  return name.replace(/^\d\d\s*-\s*\d\d\s+/, "")
    .replace(/[A-Z][A-Z]+/g, (w) => (w.length <= 3 ? w : w[0] + w.slice(1).toLowerCase()));
}
const firstName = (d) => (d?.child?.name || "your child").split(" ")[0];
const shorten = (t, n = 42) => (t.length > n ? t.slice(0, n - 1).trimEnd() + "…" : t);

// Questions about this child's own data, most pressing first. Each may name a course for its colour dot.
function suggestedQuestions(d) {
  const o = d.overview, name = firstName(d), out = [];
  const add = (text, course) => { if (!out.some((q) => q.text === text)) out.push({ text, course }); };

  const drops = o.courses.filter((c) => c.change_7_days !== null && c.change_7_days <= -3)
    .sort((a, b) => a.change_7_days - b.change_7_days);
  if (drops[0]) add(`Why did ${niceCourse(drops[0].title)} drop ${Math.abs(drops[0].change_7_days)} points this week?`, drops[0].title);

  if (o.missing.length === 1) add(`What's missing in ${niceCourse(o.missing[0].course)}?`, o.missing[0].course);
  else if (o.missing.length > 1) add(`What work is missing, and what should we do first?`);

  const test = o.upcoming.find((a) => a.kind === "test");
  if (test) add(`How can ${name} prepare for “${shorten(test.title)}”?`, test.course);
  if (o.upcoming.length) add(`What's due this week?`);

  const weak = (d.weak_topics.topics || []).find((t) => t.flagged);
  if (weak) add(`How can I help ${name} with ${weak.topic.toLowerCase()}?`);

  const attention = d.cause.courses_needing_attention || [];
  if (d.cause.cause !== "on_track" && attention[0]) add(`Is ${niceCourse(attention[0])} about effort or understanding?`, attention[0]);
  if (o.late.length) add(`Which work was handed in late?`);

  for (const text of ["What changed this week?", "Which subject needs the most help?", "What tests are coming up?"]) {
    if (out.length >= 4) break;
    add(text);
  }
  return out.slice(0, 5);
}

// The empty chat: say what Ask can do, so nobody faces a blank box.
function chatWelcome(d) {
  const name = firstName(d);
  return el("div", { class: "chat-welcome" }, [
    el("span", { class: "brand-mark", "aria-hidden": "true" }),
    el("h3", { text: `Ask anything about ${name}'s schoolwork` }),
    el("p", { text: `I can look up grades, missing work, upcoming tests and what might help. ` +
      `I only use ${name}'s school data, and I always say where an answer came from.` }),
    el("p", { class: "hint", text: "Try one of these, or type your own question below." }),
  ]);
}

function chatBubble(m) {
  return el("div", { class: "msg " + m.role }, [m.text, sourcesLine(m)]);
}
function sourcesLine(m) {
  const sources = (m.sources || []).map((s) => s.tool.replace(/^get_|^list_/, "").replaceAll("_", " "));
  return sources.length ? el("span", { class: "sources", text: "Based on: " + [...new Set(sources)].join(", ") }) : null;
}

// While the agent works: bouncing dots and a status line that moves on as time passes.
const THINKING_STEPS = ["Looking at the data…", "Checking grades and assignments…", "Writing the answer…"];
function thinkingBubble() {
  const label = el("span", { class: "typing-text", text: THINKING_STEPS[0] });
  const bubble = el("div", { class: "msg assistant typing", role: "status" },
    [el("span", { class: "dots", "aria-hidden": "true" }, [el("i"), el("i"), el("i")]), label]);
  let step = 0;
  bubble.stop = () => clearInterval(timer);
  const timer = setInterval(() => { if (step < THINKING_STEPS.length - 1) label.textContent = THINKING_STEPS[++step]; }, 2500);
  return bubble;
}

// Show the reply a few words at a time, finishing within about two seconds however long it is.
async function typeOut(node, text) {
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) { node.textContent = text; return; }
  const words = text.split(/(\s+)/);
  const delay = Math.min(45, Math.max(10, 1800 / words.length));
  for (let i = 1; i <= words.length; i++) {
    node.textContent = words.slice(0, i).join("");
    if (i % 4 === 0) scrollChat();
    await new Promise((r) => setTimeout(r, delay));
  }
}
function renderSuggestions(show) {
  // Shown whenever the chat is idle, leaving out questions already asked.
  const questions = (state.chatSuggestions || SUGGESTIONS.map((text) => ({ text })))
    .filter((q) => !state.asked?.has(q.text.trim().toLowerCase()));
  $("chat-suggestions").replaceChildren(...(show ? questions.map((q) => {
    const b = el("button", { type: "button", class: "suggestion" }, [q.course ? courseDot(q.course) : null, q.text]);
    b.addEventListener("click", () => ask(q.text));
    return b;
  }) : []));
}
function scrollChat() { const log = $("chat-log"); log.scrollTop = log.scrollHeight; }

async function ask(question) {
  question = question.trim();
  if (!question) return;
  const log = $("chat-log");
  log.querySelector(".chat-welcome")?.remove();
  state.asked?.add(question.toLowerCase());
  log.append(el("div", { class: "msg user", text: question }));
  const thinking = thinkingBubble();
  log.append(thinking); scrollChat();
  renderSuggestions(false);
  $("chat-input").value = ""; $("chat-send").disabled = true;
  try {
    const reply = await api(childUrl("/chat"), { method: "POST", json: { question } });
    thinking.stop();
    const body = el("span");
    const bubble = el("div", { class: "msg assistant writing" }, body);
    thinking.replaceWith(bubble);
    await typeOut(body, reply.text);
    bubble.classList.remove("writing");
    const sources = sourcesLine(reply);
    if (sources) bubble.append(sources);
  } catch (e) {
    thinking.stop();
    const text = e.status === 501 ? "The assistant isn't switched on yet."
      : e.status === 503 && e.message.startsWith("not_configured") ? "AI isn't set up yet, so the assistant can't answer."
      : "Couldn't answer: " + e.message;
    thinking.replaceWith(el("div", { class: "msg error", text }));
  } finally { $("chat-send").disabled = false; renderSuggestions(true); scrollChat(); }
}

// ---------- Settings ----------

async function loadSettings() {
  const { me } = state, u = me.user, s = me.schoology;
  $("profile-name").textContent = u.name;
  $("profile-line").textContent = u.is_demo ? "Demo account with sample data"
    : [u.email, u.district_domain, u.schoology_uid && "Schoology user " + u.schoology_uid].filter(Boolean).join(" · ");
  const labels = { demo: ["Demo", ""], ok: ["Connected to Schoology", "ok"], expiring: ["Reconnect soon", "warn"], reconnect: ["Not connected", "bad"] };
  const [label, cls] = labels[s.state];
  $("connection").textContent = label + (s.expires_at && s.state !== "reconnect" ? " until " + date(s.expires_at) : "");
  $("connection").className = "chip " + cls;

  const keys = await api("/api/ai-keys");
  const enabled = Object.entries(keys).filter(([, k]) => k.byok_enabled);
  $("keys-card").hidden = enabled.length === 0;
  $("keys").replaceChildren(...enabled.map(([provider, k]) => {
    const input = el("input", { type: "password", placeholder: `Paste your ${PROVIDERS[provider]} key`, autocomplete: "off" });
    const save = el("button", { type: "submit", class: "small primary", text: k.key_saved ? "Replace" : "Save" });
    const form = el("form", {}, [input, save]);
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      try { await api(`/api/ai-keys/${provider}`, { method: "PUT", json: { key: input.value } }); say("Key saved.", "ok"); loadPage("settings", true); }
      catch (e) { say(e.message, "bad"); }
    });
    const remove = el("button", { class: "small", text: "Remove" });
    remove.addEventListener("click", async () => { await api(`/api/ai-keys/${provider}`, { method: "DELETE" }); loadPage("settings", true); });
    return el("div", { class: "key-row" }, [
      el("div", {}, [el("b", { text: PROVIDERS[provider] }), " ",
        el("span", { class: "chip " + (k.in_use ? "ok" : ""), text: k.in_use ? "In use" : k.key_saved ? "Saved" : "Not added" })]),
      k.key_saved ? remove : el("span"),
      form,
    ]);
  }));
}

// ---------- actions ----------

function resetChildPages() { state.dash = null; state.loaded = {}; }

// While syncing, the page underneath is blocked: inert stops clicks, taps and keyboard focus.
function blockApp(on) {
  $("sync-overlay").hidden = !on;
  for (const part of document.querySelectorAll("header.topbar, main")) part.inert = on;
  document.body.setAttribute("aria-busy", on ? "true" : "false");
}

async function syncNow() {
  const b = $("sync"); busy(b, true, "Syncing…"); say("");
  blockApp(true);
  try {
    await api("/api/sync", { method: "POST" });
    state.me = await api("/api/me");
    if (!state.childId && state.me.children.length) state.childId = state.me.children[0].id;
    resetChildPages(); renderShell(); showPage();
    api(childUrl("/alerts")).then(updateBadge).catch(() => {});
  } catch (e) {
    say("Sync failed: " + e.message, "bad");
  } finally { blockApp(false); busy(b, false); }
}

function wire() {
  window.addEventListener("hashchange", showPage);
  $("child-picker").addEventListener("change", (e) => { state.childId = Number(e.target.value); resetChildPages(); renderShell(); showPage(); });
  $("sync").addEventListener("click", syncNow);
  $("logout").addEventListener("click", async () => { await fetch("/auth/logout", { method: "POST" }); location.replace("/"); });
  $("show-empty-courses").addEventListener("change", renderCourses);
  $("filter-course").addEventListener("change", renderAssignments);
  $("filter-status").addEventListener("change", renderAssignments);
  $("sort-due").addEventListener("click", () => {
    state.dueSort = state.dueSort === "asc" ? "desc" : "asc";
    renderAssignments();
  });
  $("alerts-refresh").addEventListener("click", async (ev) => {
    busy(ev.target, true, "Checking…");
    try {
      const r = await api(childUrl("/alerts/refresh"), { method: "POST" });
      say(r.new_alerts ? `${r.new_alerts} new alert${r.new_alerts > 1 ? "s" : ""}.` : "No new alerts.", "ok");
      state.loaded.alerts = false; await loadAlerts();
      if (state.childId) loadOverviewAlerts().catch(() => {});
    } catch (e) { say(e.message, "bad"); } finally { busy(ev.target, false); }
  });
  $("week-generate").addEventListener("click", async (ev) => {
    busy(ev.target, true, "Writing…");
    try {
      const r = await api(childUrl("/summaries/generate?force=true"), { method: "POST" });
      if (r.error) say(aiNote(r.error, "write the weekly summary"), "warn"); else say("");
      state.loaded.week = false; await loadWeek();
      if (state.childId) loadOverviewWeek().catch(() => {});
    } catch (e) { say(e.message, "bad"); } finally { busy(ev.target, false); }
  });
  $("topics-label").addEventListener("click", async (ev) => {
    busy(ev.target, true, "Labelling…");
    try {
      const r = await api(childUrl("/weak-topics/label"), { method: "POST" });
      say(r.error ? aiNote(r.error, "label topics") : `Labelled ${r.labelled} lost marks.`, r.error ? "warn" : "ok");
      state.dash = null; state.loaded.topics = false; await loadTopics();
    } catch (e) { say(e.message, "bad"); } finally { busy(ev.target, false); }
  });
  $("chat-form").addEventListener("submit", (ev) => { ev.preventDefault(); ask($("chat-input").value); });
  $("paper-course").addEventListener("change", fillAssignments);
  $("paper-assignment").addEventListener("change", showTitleBox);
  $("paper-files").addEventListener("change", (e) => { $("paper-previews").replaceChildren(...previewTiles(filePreviews(e.target.files))); });
  $("paper-form").addEventListener("submit", uploadPaper);
  $("paper-add-row").addEventListener("click", () => { $("paper-questions").append(questionRow()); });
  $("paper-close").addEventListener("click", () => { location.hash = "#/papers"; });
  $("chat-clear").addEventListener("click", async () => { await api(childUrl("/chat"), { method: "DELETE" }); state.loaded.ask = false; loadChat(); });
}

async function start() {
  wire();
  state.me = await api("/api/me");
  state.childId = state.me.children[0]?.id ?? null;
  renderShell();
  const neverSynced = !state.me.user.is_demo && state.me.schoology.state !== "reconnect"
    && (!state.me.children.length || state.me.children.some((c) => !c.last_synced_at));
  if (neverSynced) await syncNow();
  else {
    showPage();
    if (state.childId) api(childUrl("/alerts")).then(updateBadge).catch(() => {});
  }
}

start().catch((e) => { if (e.message !== "signed out") say(e.message, "bad"); });
