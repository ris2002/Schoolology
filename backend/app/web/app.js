// Parent app. Code-calculated facts render immediately; AI text fills in where the
// feature is available, otherwise a short note says why it is missing.
"use strict";

const $ = (id) => document.getElementById(id);
const PAGES = ["overview", "alerts", "week", "topics", "advice", "ask", "settings"];
const CAUSES = {
  understanding: "Understanding", effort: "Effort", mixed: "Effort and understanding",
  broad_understanding: "Broad understanding", outside_school: "Something outside school", on_track: "On track",
};
const STATUS = { graded: "Graded", missing: "Missing", awaiting_grade: "Awaiting grade", upcoming: "Upcoming", excused: "Excused" };
const PROVIDERS = { huggingface: "Hugging Face", anthropic: "Claude" };
const SUGGESTIONS = [
  "What is missing right now?",
  "Which subject needs the most help?",
  "What changed this week?",
  "Is this about effort or understanding?",
  "What tests are coming up?",
];

const state = { me: null, childId: null, dash: null, loaded: {} };

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
function aiBlock(text, error, label = "AI explanation") {
  if (text) return el("div", { class: "ai" }, [el("span", { class: "ai-label", text: label }), el("p", { text })]);
  return el("div", { class: "ai missing", text: aiNote(error) || "No explanation yet." });
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

function currentPage() {
  const page = location.hash.replace(/^#\/?/, "");
  return PAGES.includes(page) ? page : "overview";
}

function showPage() {
  const page = currentPage();
  say("");
  document.querySelectorAll("[data-view]").forEach((v) => { v.hidden = v.dataset.view !== page || !state.childId && page !== "settings"; });
  document.querySelectorAll(".tabs-nav a").forEach((a) => {
    if (a.dataset.page === page) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
  loadPage(page);
}

// Each page loads once per child; Sync and actions reload what they change.
async function loadPage(page, force = false) {
  if (page !== "settings" && !state.childId) return;
  if (state.loaded[page] && !force) return;
  state.loaded[page] = true;
  try {
    await ({ overview: loadOverview, alerts: loadAlerts, week: loadWeek, topics: loadTopics,
             advice: loadAdvice, ask: loadChat, settings: loadSettings })[page]();
  } catch (e) {
    state.loaded[page] = false;
    if (e.message !== "signed out") say("Could not load this page: " + e.message, "bad");
  }
}

async function dashboard(force = false) {
  if (!state.dash || force) state.dash = await api(childUrl("/dashboard"));
  return state.dash;
}

// ---------- Overview (code only) ----------

async function loadOverview() {
  const d = await dashboard();
  const o = d.overview;
  $("synced").textContent = "Last synced " + when(d.child.last_synced_at);
  $("tile-cause").textContent = CAUSES[d.cause.cause];
  $("tile-missing").textContent = o.missing.length;
  $("tile-late").textContent = o.late.length;
  $("tile-upcoming").textContent = o.upcoming.length;
  renderCourses();

  const item = (a) => el("li", {}, [el("span", { text: a.title }), el("span", { class: "hint", text: ` · ${a.course} · due ${date(a.due_at)}` })]);
  $("missing").replaceChildren(...(o.missing.length ? o.missing.map(item) : [el("li", { class: "empty", text: "Nothing missing." })]));
  $("upcoming").replaceChildren(...(o.upcoming.length ? o.upcoming.map(item) : [el("li", { class: "empty", text: "Nothing due." })]));

  const filter = $("filter-course"), current = filter.value;
  const courseNames = [...new Set(d.assignments.map((a) => a.course))].sort();
  filter.replaceChildren(new Option("All courses", ""), ...courseNames.map((c) => new Option(c, c)));
  filter.value = courseNames.includes(current) ? current : "";
  renderAssignments();

  const cats = $("categories");
  if (!d.categories.length) emptyRow(cats, 4, "No grading categories.");
  else cats.replaceChildren(...d.categories.map((c) => el("tr", {}, [td(c.course), td(c.title), td(c.kind), td(num(c.weight), "num")])));
}

function renderCourses() {
  const courses = state.dash.overview.courses;
  const showEmpty = $("show-empty-courses").checked;
  const rows = courses.filter((c) => showEmpty || c.average !== null || c.missing_count || c.awaiting_grade_count);
  const body = $("courses");
  if (!rows.length) { emptyRow(body, 7, "No graded courses yet. Try Sync."); return; }
  body.replaceChildren(...rows.map((c) => el("tr", {}, [
    td(c.title),
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
  const body = $("assignments");
  if (!rows.length) emptyRow(body, 7, "No assignments match.");
  else body.replaceChildren(...rows.map((a) => {
    const chips = [el("span", { class: "chip " + ({ missing: "bad", graded: "ok", awaiting_grade: "warn" }[a.status] || ""), text: STATUS[a.status] })];
    if (a.late) chips.push(" ", el("span", { class: "chip warn", text: "Late" }));
    const score = a.score === null ? "–" : `${a.score}/${a.max_points}` + (a.percent !== null ? ` (${a.percent}%)` : "");
    return el("tr", {}, [td(a.title), td(a.course), td(a.category), td(date(a.due_at), "nowrap"), td(score, "num"),
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
  const list = $("alert-list");
  if (!alerts.length) { list.replaceChildren(el("li", { class: "empty", text: "No alerts. New ones appear after a sync." })); return; }
  list.replaceChildren(...alerts.map((a) => {
    const read = el("button", { class: "small", text: "Mark read" });
    read.addEventListener("click", async () => {
      await api(childUrl(`/alerts/${a.id}/read`), { method: "POST" });
      state.loaded.alerts = false; loadAlerts();
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

async function loadWeek() {
  const summaries = await api(childUrl("/summaries"));
  const [latest, ...older] = summaries;
  const thisWeek = latest && isThisWeek(latest.week_start);
  $("week-current").replaceChildren(thisWeek
    ? aiBlock(latest.text, null, `Week of ${date(latest.week_start)}`)
    : el("p", { class: "empty", text: "No summary for this week yet. Summaries are written once a week, or press the button." }));
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
  $("cause-explanation").replaceChildren(el("div", { class: "ai missing", text: "Writing an explanation…" }));
  $("cause-courses").replaceChildren(...cause.courses.filter((c) => c.assignments_counted).map((c) => el("tr", {}, [
    td(c.course), el("td", {}, el("span", { class: "chip " + (c.cause === "on_track" ? "ok" : "warn"), text: CAUSES[c.cause] })),
    td(pct(c.missing_or_late_rate), "num"), td(num(c.completed_average), "num"), td(num(c.test_average), "num"),
    td(c.weak_topics.join(", ") || "–"),
  ])));
  $("gap").replaceChildren(el("p", { class: "hint", text: "Loading…" }));

  const [advice, gap] = await Promise.all([api(childUrl("/advice")), api(childUrl("/gap"))]);
  $("cause-explanation").replaceChildren(aiBlock(advice.explanation, advice.ai_error));

  if (!gap.courses.length) {
    $("gap").replaceChildren(el("p", { class: "empty", text: "Needs both homework and test grades in a course. Categories the app can't identify as homework or tests are left out." }));
    return;
  }
  const table = el("table", {}, [
    el("thead", {}, el("tr", {}, [el("th", { text: "Course" }), el("th", { class: "num", text: "Homework" }),
      el("th", { class: "num", text: "Tests" }), el("th", { class: "num", text: "Gap" })])),
    el("tbody", {}, gap.courses.map((g) => el("tr", {}, [td(g.course), td(num(g.homework_average), "num"), td(num(g.test_average), "num"),
      el("td", { class: "num" }, g.flagged ? el("span", { class: "chip warn", text: String(g.gap) }) : String(g.gap))]))),
  ]);
  const explained = gap.courses.filter((g) => g.flagged).map((g) => el("div", {}, [
    el("p", { text: g.course, class: "hint" }), aiBlock(g.explanation, g.ai_error),
  ]));
  $("gap").replaceChildren(el("div", { class: "table-wrap" }, table),
    el("p", { class: "hint", text: `Highlighted when homework is ${gap.courses[0].threshold}+ points above tests.` }), ...explained);
}

// ---------- Ask (chat agent) ----------

async function loadChat() {
  const history = await api(childUrl("/chat"));
  $("chat-log").replaceChildren(...history.map(chatBubble));
  renderSuggestions(history.length === 0);
  scrollChat();
}

function chatBubble(m) {
  const sources = (m.sources || []).map((s) => s.tool.replace(/^get_|^list_/, "").replaceAll("_", " "));
  return el("div", { class: "msg " + m.role }, [m.text,
    sources.length ? el("span", { class: "sources", text: "Based on: " + [...new Set(sources)].join(", ") }) : null]);
}
function renderSuggestions(show) {
  $("chat-suggestions").replaceChildren(...(show ? SUGGESTIONS.map((q) => {
    const b = el("button", { type: "button", text: q });
    b.addEventListener("click", () => ask(q));
    return b;
  }) : []));
}
function scrollChat() { const log = $("chat-log"); log.scrollTop = log.scrollHeight; }

async function ask(question) {
  question = question.trim();
  if (!question) return;
  const log = $("chat-log");
  log.append(el("div", { class: "msg user", text: question }));
  const thinking = el("div", { class: "msg assistant hint", text: "Looking at the data…" });
  log.append(thinking); scrollChat();
  renderSuggestions(false);
  $("chat-input").value = ""; $("chat-send").disabled = true;
  try {
    const reply = await api(childUrl("/chat"), { method: "POST", json: { question } });
    thinking.replaceWith(chatBubble(reply));
  } catch (e) {
    const text = e.status === 501 ? "The assistant isn't switched on yet."
      : e.status === 503 && e.message.startsWith("not_configured") ? "AI isn't set up yet, so the assistant can't answer."
      : "Couldn't answer: " + e.message;
    thinking.replaceWith(el("div", { class: "msg error", text }));
  } finally { $("chat-send").disabled = false; scrollChat(); }
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

async function syncNow() {
  const b = $("sync"); busy(b, true, "Syncing…"); say("");
  try {
    await api("/api/sync", { method: "POST" });
    state.me = await api("/api/me");
    if (!state.childId && state.me.children.length) state.childId = state.me.children[0].id;
    resetChildPages(); renderShell(); showPage();
    api(childUrl("/alerts")).then(updateBadge).catch(() => {});
  } catch (e) {
    say("Sync failed: " + e.message, "bad");
  } finally { busy(b, false); }
}

function wire() {
  window.addEventListener("hashchange", showPage);
  $("child-picker").addEventListener("change", (e) => { state.childId = Number(e.target.value); resetChildPages(); renderShell(); showPage(); });
  $("sync").addEventListener("click", syncNow);
  $("logout").addEventListener("click", async () => { await fetch("/auth/logout", { method: "POST" }); location.replace("/"); });
  $("show-empty-courses").addEventListener("change", renderCourses);
  $("filter-course").addEventListener("change", renderAssignments);
  $("filter-status").addEventListener("change", renderAssignments);
  $("alerts-refresh").addEventListener("click", async (ev) => {
    busy(ev.target, true, "Checking…");
    try {
      const r = await api(childUrl("/alerts/refresh"), { method: "POST" });
      say(r.new_alerts ? `${r.new_alerts} new alert${r.new_alerts > 1 ? "s" : ""}.` : "No new alerts.", "ok");
      state.loaded.alerts = false; await loadAlerts();
    } catch (e) { say(e.message, "bad"); } finally { busy(ev.target, false); }
  });
  $("week-generate").addEventListener("click", async (ev) => {
    busy(ev.target, true, "Writing…");
    try {
      const r = await api(childUrl("/summaries/generate?force=true"), { method: "POST" });
      if (r.error) say(aiNote(r.error, "write the weekly summary"), "warn"); else say("");
      state.loaded.week = false; await loadWeek();
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
