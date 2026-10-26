"use strict";
// Interface 9AM9AM-TEAM : une conversation avec le CEO (Claude Code).
// Tout texte venant du serveur (messages, titres, sorties des workers) passe par
// textContent : jamais interprété comme du HTML.

// Le jeton vient du <meta> de la page (posée après le handshake ?h=nonce) :
// jamais dans l'URL, donc jamais dans l'historique du navigateur.
const TOKEN = (document.querySelector('meta[name="api-token"]') || {}).content || "";
const $ = (id) => document.getElementById(id);
const SETTINGS = ["ceo_mode", "ceo_cloud_session", "ceo_cloud_repo", "ceo_model", "ceo_effort", "escalation", "verify_cmd", "opencode_model", "autoclaw_model", "cline_model",
  "autoclaw_effort", "cline_effort", "autoclaw_cmd", "cline_cmd", "opencode_agent", "opencode_variant", "custom_models", "cline_models", "ui_color"];
// Niveaux d'effort (même liste que orch/effort.py ; "" = défaut de l'outil).
const EFFORTS = ["", "faible", "normal", "élevé", "max", "ultracode"];
// Models du CEO proposés dans la liste (même liste que CEO_MODEL_SUGGESTIONS ; "" = défaut de Claude Code).
const CEO_MODEL_CHOICES = ["", "sonnet", "opus", "haiku", "fable"];
const CEO_MODEL_OTHER = "__autre__";
const DEFAULT_ACCENT = "#e95420";  // la couleur du projet, en attendant celle de ui_color
let allRepos = [];   // dépôts GitHub (mode cloud), chargés via /api/github_repos
let reposLoaded = false;
let reposLoading = false;  // un seul chargement à la fois, jamais relancé à chaque poll
let currentRepo = "";  // dépôt visé en mode cloud (glisser via ceo_cloud_repo)
const STATUS = { todo: "à faire", in_progress: "en cours", done: "fait", blocked: "bloquée", failed: "échouée" };
const VERDICT = {
  ok: "✓ vérifiée par le CEO (Claude Code)",
  ko: "✗ refusée par le CEO (Claude Code)",
  check_ko: "✗ vérification automatique échouée (tests)",
  check_error: "✗ vérification impossible : corrige la commande dans les Paramètres",
  non_supervise: "non vérifiée par le CEO",
};
const VERDICT_CLASS = { ok: "ok", ko: "ko", non_supervise: "warn", check_ko: "ko", check_error: "warn" };

let since = 0;       // curseur du journal technique
let chatSince = 0;   // curseur de la conversation
let busy = false;
let stopped = false;
let timer = null;
let lastTasks = "";
let lastTeam = "";
const pendingSave = new Map();
const taskCards = new Map();   // id de tâche -> carte affichée dans le fil

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

async function api(path, body) {
  const opts = { headers: { "X-Token": TOKEN } };
  if (body !== undefined) {
    opts.method = "POST";
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  let data;
  try { data = await res.json(); } catch (e) { data = { error: "réponse illisible (" + res.status + ")" }; }
  return { status: res.status, data };
}

/* Avertissements de pièces jointes : un effacement générique ne doit pas les faire disparaître. */
function clearBannerKeepFiles() {
  if ($("banner").dataset.kind !== "files") banner("");
}

function banner(text, kind) {
  $("banner").textContent = text || "";
  $("banner").hidden = !text;
  $("banner").dataset.kind = kind || "";
}

const hhmm = (iso) => (iso || "").slice(11, 16);

/* ------------------------------------------------------------ conversation */

function nearBottom(box) { return box.scrollHeight - box.scrollTop - box.clientHeight < 90; }

const COPY_FEEDBACK_MS = 1500;
const COPY_LABEL = "Copier";

/* ---- texte mis en forme : blocs de markdown.js → éléments (textContent seulement) */
const MAX_HEADING_TAG = 6;
function inlineNodes(tokens) {
  return tokens.map((tok) => {
    if (tok.t === "br") return document.createElement("br");
    if (tok.t === "strong") return el("strong", "", tok.v);
    if (tok.t === "em") return el("em", "", tok.v);
    if (tok.t === "code") return el("code", "", tok.v);
    return document.createTextNode(tok.v);
  });
}
function blockNode(b) {
  if (b.type === "heading") {
    const h = el("h" + Math.min(b.level + 2, MAX_HEADING_TAG), "md-h");
    h.append(...inlineNodes(b.inline));
    return h;
  }
  if (b.type === "list") {
    const list = el(b.ordered ? "ol" : "ul", "md-list");
    for (const item of b.items) {
      const li = el("li", item.depth ? "d" + item.depth : "");
      li.append(...inlineNodes(item.inline));
      list.append(li);
    }
    return list;
  }
  if (b.type === "code") {
    const pre = el("pre", "md-code" + (b.lang ? " lang-" + b.lang.replace(/[^\w-]/g, "") : ""));
    pre.append(el("code", "", b.text));
    return pre;
  }
  if (b.type === "quote") {
    const q = el("blockquote", "md-quote");
    q.append(...inlineNodes(b.inline));
    return q;
  }
  if (b.type === "hr") return el("hr", "md-hr");
  if (b.type === "note") return el("p", "md-note", "… " + b.text);
  const first = b.inline[0];
  const advice = first && first.t === "text" && first.v.startsWith("Modèle conseillé");  // mise à part du contenu
  const p = el("p", advice ? "md-p md-advice" : "md-p");
  p.append(...inlineNodes(b.inline));
  return p;
}
function renderMarkdown(text) {
  const box = el("div", "md");
  for (const b of Markdown.parseMarkdown(text)) box.append(blockNode(b));
  return box;
}

/* ---- activité d'un agent : « A utilisé 4 outils » + une ligne par outil */
const STEP_ICON = { completed: "✓", error: "✗" };
function fmtMs(ms) {
  if (ms === null || ms === undefined) return "";
  return ms < 100 ? "<0,1 s" : (ms / 1000).toFixed(ms < 10000 ? 1 : 0).replace(".", ",") + " s";
}
function stepsNode(d) {
  const steps = Array.isArray(d.steps) ? d.steps : [];
  if (!steps.length) return null;
  const extra = d.steps_omitted > 0 ? " (" + d.steps_omitted + " non listé" + (d.steps_omitted > 1 ? "s" : "") + ")" : "";
  const total = steps.length + (d.steps_omitted || 0);
  const box = el("details", "ev-steps");
  box.append(el("summary", "", "A utilisé " + total + " outil" + (total > 1 ? "s" : "") + extra));
  const list = el("ol", "steps");
  for (const s of steps) {
    const li = el("li", "step " + (s.status === "completed" ? "ok" : s.status === "error" ? "bad" : "unk"));
    li.append(el("span", "step-icon", Object.hasOwn(STEP_ICON, s.status) ? STEP_ICON[s.status] : "·"), el("strong", "step-tool", s.tool));
    if (s.summary) li.append(el("span", "step-arg", s.summary));
    const dur = fmtMs(s.ms);
    if (dur) li.append(el("span", "time", dur));
    list.append(li);
  }
  box.append(list);
  return box;
}

/* Copie de secours (WKWebView du logiciel : l'API moderne peut y être refusée). */
function legacyCopy(text) {
  const area = el("textarea", "clip-buffer");
  area.value = text;
  area.setAttribute("readonly", "");
  document.body.append(area);
  area.select();
  try { return document.execCommand("copy"); } catch (e) { return false; } finally { area.remove(); }
}

/* Copie dans le presse-papiers ; si les deux méthodes échouent, c'est dit, jamais avalé. */
async function copyText(text, button) {
  let copied = false;
  try { await navigator.clipboard.writeText(text); copied = true; } catch (e) { copied = legacyCopy(text); }
  if (!copied) {
    banner("Copie impossible : sélectionne le texte à la main.", "info");
    return;
  }
  button.textContent = "Copié ✓";
  clearTimeout(button._restore);  // un second clic ne fige pas « Copié ✓ »
  button._restore = setTimeout(() => { button.textContent = COPY_LABEL; }, COPY_FEEDBACK_MS);
}

function copyButton(text) {
  const btn = el("button", "copy", COPY_LABEL);
  btn.type = "button";
  btn.title = "Copie ce message dans le presse-papiers";
  btn.addEventListener("click", () => copyText(text, btn));
  return btn;
}

/* « ↻ Rejouer » : renvoie ce message au CEO tel quel (sans les fichiers joints du moment). */
let lastUserText = "";  // ton dernier message affiché : c'est lui qu'une réponse d'erreur rejoue
let replaying = false;
async function replayPrompt(text) {
  if (busy) { banner("Le CEO est déjà au travail : attends sa réponse ou clique sur « Arrêter »."); return; }
  if (replaying) return;
  replaying = true;
  try { await send(text, { files: [], pasted: [] }); } finally { replaying = false; }
}

function replayButton(text) {
  const btn = el("button", "replay", "↻ Rejouer");
  btn.type = "button";
  btn.title = "Renvoie ce message au CEO";
  btn.addEventListener("click", () => replayPrompt(text));
  return btn;
}

function messageNode(m) {
  if (m.role === "user") {
    const row = el("div", "msg user");
    const meta = el("div", "meta");
    meta.append(el("span", "time", hhmm(m.at)), copyButton(m.text), replayButton(m.text));
    row.append(el("div", "bubble", m.text));
    if (m.data && Array.isArray(m.data.files) && m.data.files.length) {
      row.append(el("div", "attached muted", "📎 " + m.data.files.join(", ")));
    }
    row.append(meta);
    return row;
  }
  const kind = m.kind || "reply";
  const row = el("div", "msg ceo " + kind);
  const head = el("div", "who");
  head.append(el("span", "avatar", "CC"),
              el("strong", "", kind === "report" ? "Rapport du CEO" : "Claude Code"),
              el("span", "role", kind === "report" ? "réponse finale" : "CEO"),
              el("span", "time", hhmm(m.at)));
  const bubble = el("div", "bubble rich");
  bubble.append(renderMarkdown(m.text));
  const meta = el("div", "meta");
  meta.append(copyButton(m.text));
  if (kind === "error" && lastUserText) meta.append(replayButton(lastUserText));
  row.append(head, bubble, meta);
  return row;
}

function planNode(m) {
  const card = el("div", "ev plan");
  const tasks = (m.data && m.data.tasks) || [];
  card.append(el("div", "ev-head", "🗂 Plan · " + tasks.length + " tâche" + (tasks.length > 1 ? "s" : "")));
  const list = el("ol", "ev-list");
  for (const t of tasks) list.append(el("li", "", "[" + t.id + "] " + t.title + " — " + t.worker));
  card.append(list);
  return card;
}

function taskStartNode(m) {
  const d = m.data || {};
  const card = el("div", "ev job running");
  const head = el("div", "ev-head");
  head.append(el("span", "spinner"), el("strong", "", d.worker || "worker"),
              el("span", "", " travaille · tâche [" + d.id + "]"));
  card.append(head, el("div", "ev-title", d.title || ""));
  card._head = head;
  taskCards.set(d.id, card);
  return card;
}

function taskResult(m) {
  const d = m.data || {};
  let card = taskCards.get(d.id);
  const fresh = !card;
  if (!card) { card = el("div", "ev job"); card._head = el("div", "ev-head"); card.append(card._head); }
  card.classList.remove("running");
  card.classList.add(d.cancelled ? "stopped" : d.status === "blocked" || d.status === "failed" ? "bad" : "finished");
  const head = card._head;
  const spinner = head.querySelector(".spinner");
  if (spinner) spinner.remove();
  const chip = el("span", "chip-status", d.cancelled ? "arrêtée" : d.status === "in_progress" ? VERIFYING
                                       : (STATUS[d.status] || d.status));
  head.append(chip);
  if (d.model) head.append(el("span", "muted model-used", " · model : " + d.model));
  card._chip = chip;
  const steps = stepsNode(d);
  if (steps) card.append(steps);
  const out = el("details", "ev-out");
  out.append(el("summary", "", "Ce que le worker a répondu"), renderMarkdown(m.text));
  card.append(out);
  return fresh ? card : null;
}

const FINAL_CHIP = { ok: "fait · vérifié", ko: "refusée", non_supervise: "fait · non vérifié" };

function verdictNode(m) {
  const d = m.data || {};
  const card = taskCards.get(d.id);
  const line = el("div", "ev-verdict " + (VERDICT_CLASS[d.verdict] || "warn"));
  line.append(el("strong", "", VERDICT[d.verdict] || "verdict inconnu"));
  const note = (m.text || "").trim();
  if (note) {
    const short = note.length > 170 ? note.slice(0, 170).replace(/\s+\S*$/, "") + "…" : note;
    line.append(el("span", "", " — " + short));
    if (short !== note) {
      const more = el("details", "ev-out");
      more.append(el("summary", "", "Lire toute la vérification"), el("pre", "", note));
      line.append(more);
    }
  }
  if (card && card._chip) {
    card._chip.textContent = FINAL_CHIP[d.verdict] || card._chip.textContent;
    card.classList.remove("finished", "bad");
    card.classList.add(d.verdict === "ko" ? "bad" : "finished");
  }
  if (card) { card.append(line); return null; }
  const alone = el("div", "ev job");
  alone.append(line);
  return alone;
}

/* Le badge « en cours de vérification » doit finir : tests rouges/verts ou arrêt. */
const VERIFYING = "en cours de vérification";
function settleChip(m) {
  const d = m.data || {};
  const card = taskCards.get(d.id);
  if (!card || !card._chip) return;
  if (d.ok) { card._chip.textContent = "tests verts · le CEO audite"; return; }
  card._chip.textContent = d.broken ? "✗ vérification impossible" : "✗ tests rouges";
  card.classList.remove("finished");
  card.classList.add("bad");
}
function settleStopped() {
  for (const card of taskCards.values()) {
    if (card._chip && card._chip.textContent === VERIFYING) {
      card._chip.textContent = "arrêtée";
      card.classList.remove("finished");
      card.classList.add("stopped");
    }
  }
}

function eventNode(m) {
  switch (m.kind) {
    case "plan": return planNode(m);
    case "task_start": return taskStartNode(m);
    case "task_result": return taskResult(m);
    case "verdict": return verdictNode(m);
    case "check": settleChip(m); return el("div", "ev notice", m.text);
    case "stopped": settleStopped(); return el("div", "ev notice", m.text);
    default: return el("div", "ev notice", m.text);
  }
}

function renderChat(messages) {
  if (!messages.length) return;
  const box = $("thread");
  const stick = nearBottom(box);
  $("welcome").hidden = true;
  const frag = document.createDocumentFragment();
  for (const m of messages) {
    if (m.role === "user") lastUserText = m.text;
    const node = m.role === "event" ? eventNode(m) : messageNode(m);
    if (node) frag.append(node);
  }
  box.append(frag);
  if (stick || messages.some((m) => m.role === "user")) box.scrollTop = box.scrollHeight;
}

function renderTyping(state) {
  const team = state.team;
  const worker = team.workers.find((w) => w.active);
  const working = team.ceo.active ? ["Claude Code", team.ceo.state]
    : (worker ? [worker.name, worker.state] : null);
  $("typing").hidden = !state.busy;
  if (state.busy) {
    $("typing-text").textContent = working ? working[0] + " " + working[1] : "L'équipe s'organise…";
    const box = $("thread");
    if (nearBottom(box)) box.scrollTop = box.scrollHeight;
  }
}

/* ------------------------------------------------------------ panneaux latéraux */

function renderTeam(team) {
  const key = JSON.stringify(team);
  if (key === lastTeam) return;
  lastTeam = key;
  const box = $("team");
  box.replaceChildren();
  const where = team.ceo.where ? " — " + team.ceo.where : "";
  const effort = (x) => (x.effort ? " · effort " + x.effort : "");
  const agents = [{ name: team.ceo.name, role: "CEO — model : " + team.ceo.model + effort(team.ceo) + where,
                    state: team.ceo.state,
                    active: team.ceo.active, ceo: true }]
    .concat(team.workers.map((w) => ({ name: w.name, role: "worker — model : " + w.model + effort(w), state: w.state,
                                        active: w.active, ceo: false })));
  for (const a of agents) {
    const card = el("div", "agent" + (a.active ? " active" : "") + (a.ceo ? " ceo" : ""));
    card.append(el("span", "dot"), el("strong", "", a.name), el("span", "role", a.role),
                el("span", "astate", a.state));
    box.append(card);
  }
}

function renderTasks(state) {
  $("problem").hidden = !state.problem;
  $("problem").textContent = state.problem || "";
  const key = JSON.stringify(state.tasks);
  if (key === lastTasks) return;
  lastTasks = key;
  const list = $("tasks");
  list.replaceChildren();
  $("no-tasks").hidden = state.tasks.length > 0 || Boolean(state.problem);
  const RECENT = 12;
  const older = state.tasks.slice(0, -RECENT);
  // Les anciennes tâches bloquées/échouées/en cours restent visibles ; on dit combien de terminées sont masquées.
  const keep = older.filter((t) => ["blocked", "failed", "in_progress"].includes(t.status));
  const hidden = older.length - keep.length;
  if (hidden > 0) list.append(el("li", "task muted", "… " + hidden + " tâche(s) terminée(s) plus ancienne(s) non affichée(s)"));
  for (const t of [...keep, ...state.tasks.slice(-RECENT)]) {
    const li = el("li", "task st-" + t.status);
    li.append(el("span", "tid", "#" + t.id), el("span", "badge", STATUS[t.status] || t.status),
              el("span", "ttitle", t.title));
    const meta = [t.worker];
    if (t.status === "in_progress" && t.phase) {
      meta.push(t.phase === "review" ? "Claude Code vérifie" : "travaille");
    }
    if (t.verdict) meta.push(VERDICT[t.verdict] || t.verdict);
    li.append(el("span", "tmeta", meta.join(" · ")));
    list.append(li);
  }
}

function renderJournal(lines) {
  if (!lines.length) return;
  const box = $("journal");
  const stick = nearBottom(box);
  const frag = document.createDocumentFragment();
  for (const line of lines) frag.append(el("div", "ln", line || " "));
  box.append(frag);
  while (box.childElementCount > 1500) box.firstElementChild.remove();
  if (stick) box.scrollTop = box.scrollHeight;
}

function applyTheme(color) {
  // ui_color vient du réglage (hex validé côté serveur) : tout le reste du thème en
  // dérive. On passe par la CSSOM — un attribut style en dur serait bloqué par la CSP.
  const hex = /^#[0-9a-fA-F]{6}$/.test(color || "") ? color.toLowerCase() : DEFAULT_ACCENT;
  const r = parseInt(hex.slice(1, 3), 16), g = parseInt(hex.slice(3, 5), 16), b = parseInt(hex.slice(5, 7), 16);
  const s = document.documentElement.style;
  s.setProperty("--accent", hex);
  s.setProperty("--accent-ink", 0.2126 * r + 0.7152 * g + 0.0722 * b > 140 ? "#0c0c0e" : "#ffffff");
  s.setProperty("--accent-soft", `rgba(${r}, ${g}, ${b}, .16)`);
  s.setProperty("--user", `rgba(${r}, ${g}, ${b}, .30)`);
}

let shownWorkspace = null;  // génération du dossier de travail affiché par cette page

function followWorkspace(state) {
  // Dossier changé (ici ou dans une autre fenêtre) : tâches et conversation sont celles d'un
  // autre projet, les curseurs de lecture ne valent plus → on recharge la page.
  if (shownWorkspace !== null && state.workspace !== shownWorkspace) { location.reload(); return true; }
  shownWorkspace = state.workspace;
  return false;
}

function paintVerifyHint(s) {
  const input = $("verify_cmd"), hint = $("verify-hint"), approve = $("verify-approve");
  if (!input || !hint) return;
  const eff = s.verify_effective || "";
  const files = (s.verify_files || []).join(", ") || "le projet";
  input.placeholder = eff ? eff + " (détectée automatiquement)" : "aucune commande détectée — ex. swift test";
  hint.textContent = {
    detected: "✓ Détectée et autorisée pour ce dossier : « " + eff + " ».",
    pending: "⚠ Détectée : « " + eff + " » (" + files + "). Elle exécute le code de ce dossier : "
      + "elle n'est PAS lancée tant que tu ne l'autorises pas.",
    changed: "⚠ " + files + " a changé depuis ton accord pour « " + eff + " » : relis-le, "
      + "la vérification n'est plus lancée tant que tu ne l'autorises pas de nouveau.",
    custom: "Ta commande est utilisée : « " + eff + " ». Vide le champ pour revenir à la détection.",
    off: "Vérification désactivée pour ce dossier. Vide le champ pour la réactiver (détection).",
    none: "Aucune commande détectée dans ce dossier : écris la tienne (ex. swift test).",
  }[s.verify_source] || "";
  if (!approve) return;
  const ask = s.verify_source === "pending" || s.verify_source === "changed";
  approve.hidden = !ask;
  approve.dataset.cmd = ask ? eff : "";
  approve.textContent = ask ? "Autoriser « " + eff + " » pour ce dossier" : "";
}

function renderSettings(state) {
  const models = state.models || [];
  for (const [listId, values] of [["models", models]]) {
    const list = $(listId);
    const key = JSON.stringify(values);
    if (list.dataset.key !== key) {
      list.replaceChildren();
      for (const m of values) { const o = document.createElement("option"); o.value = m; list.append(o); }
      list.dataset.key = key;
    }
  }
  for (const key of SETTINGS) {
    const input = $(key);
    if (!input) continue;
    if (key === "ceo_cloud_repo") continue;  // valeur = dépôt cliqué, pas de champ texte
    if (document.activeElement !== input && !pendingSave.has(key))
      input.value = state.settings[key] || (key === "ui_color" ? DEFAULT_ACCENT : "");
  }
  syncCeoModelPick();
  renderFreeModels(state.free_models);
  renderClineModels(state.cline_models);
  paintVerifyHint(state.settings);
  applyTheme(state.settings.ui_color);
  currentRepo = state.settings.ceo_cloud_repo || "";
  paintModeButtons(($("ceo_mode") || {}).value || "local");
  // Le mode cloud n'a de sens qu'avec une session existante : on le signale sans bloquer.
  const cloudInput = $("ceo_cloud_session");
  const hint = $("ceo-mode-hint");
  const box = $("cloud-repos");
  const isCloud = (($("ceo_mode") || {}).value || "local") === "cloud";
  if (box) box.hidden = !isCloud;
  const roWarn = $("cloud-readonly-warning");
  if (roWarn) roWarn.hidden = !isCloud;
  if (cloudInput) cloudInput.disabled = !isCloud;
  if (hint) {
    hint.textContent = !isCloud
      ? "Local : le CEO tourne ici. Cloud : il envoie à une session distante existante."
      : (cloudInput && cloudInput.value
        ? "☁️ Le CEO enverra à cette session distante."
        : "⚠️ Colle l'id session_... ou l'URL claude.ai/code/... sinon repli sur Local.");
  }
  if (isCloud && !reposLoaded) loadRepos(false);
  if (isCloud) paintRepos();
  $("workers").textContent = state.workers;
  renderCustomAgents(state.custom_agents || []);
}
function renderCustomAgents(agents) {
  const box = $("custom-agents");
  if (!box) return;
  const key = JSON.stringify(agents);
  if (box.dataset.key === key) return;
  box.dataset.key = key;
  box.replaceChildren();
  if (!agents.length) {
    box.append(el("p", "muted", "Aucun agent ajouté pour l'instant."));
    return;
  }
  for (const a of agents) {
    const row = el("div", "ca-row");
    row.append(el("strong", "", a.name), el("span", "muted", " · " + (a.model || "model par défaut")
      + " · effort " + (a.effort || "par défaut")));
    const del = el("button", "", "Supprimer");
    del.type = "button";
    del.addEventListener("click", async () => {
      try {
        const res = await api("/api/custom_agent", { op: "delete", name: a.name });
        if (res.status !== 200) banner(res.data.error || "suppression impossible");
        else { box.dataset.key = ""; refresh(); }
      } catch (e) {
        banner("Agent non supprimé : serveur injoignable.", "offline");
      }
    });
    row.append(del);
    box.append(row);
  }
}

async function refreshAuth() {
  const box = $("auth-state");
  if (!box) return;
  box.textContent = "Vérification…";
  try {
    const res = await api("/api/auth");
    if (res.status !== 200 || !res.data.ok) {
      box.textContent = "Non connecté — " + ((res.data && res.data.error) || "vérifie ton Terminal.");
      return;
    }
    box.textContent = "Connecté : " + res.data.email
      + (res.data.plan ? " · " + res.data.plan : "")
      + (res.data.org ? " · " + res.data.org : "");
  } catch (e) {
    box.textContent = "Serveur injoignable.";
  }
}

/* ------------------------------------------------- mode local/cloud + dépôts */

function paintModeButtons(mode) {
  const local = $("mode-local");
  const cloud = $("mode-cloud");
  const sel = $("ceo_mode");
  if (sel) sel.value = mode;
  if (local) { local.classList.toggle("on", mode !== "cloud"); local.setAttribute("aria-pressed", mode !== "cloud" ? "true" : "false"); }
  if (cloud) { cloud.classList.toggle("on", mode === "cloud"); cloud.setAttribute("aria-pressed", mode === "cloud" ? "true" : "false"); }
}

async function setMode(mode) {
  paintModeButtons(mode);
  const sel = $("ceo_mode");
  if (sel) sel.value = mode;
  try {
    const res = await api("/api/setting", { key: "ceo_mode", value: mode });
    if (res.status !== 200) { banner(res.data.error || "mode non sauvegardé"); return; }
    banner("");
    if (mode === "cloud" && !reposLoaded) await loadRepos(false);
    refresh();
  } catch (e) {
    banner("Mode non sauvegardé : serveur injoignable.", "offline");
  }
}

async function setRepo(repo) {
  try {
    const res = await api("/api/setting", { key: "ceo_cloud_repo", value: repo });
    if (res.status !== 200) { banner(res.data.error || "dépôt non sauvegardé"); return; }
    currentRepo = repo;
    banner(repo ? "Dépôt cloud : " + repo : "Dépôt cloud effacé.", repo ? "info" : "");
    paintRepos();
  } catch (e) {
    banner("Dépôt non sauvegardé : serveur injoignable.", "offline");
  }
}

async function loadRepos(force) {
  if (reposLoading) return;
  reposLoading = true;
  const note = $("repos-note");
  if (note) note.textContent = "Chargement des dépôts…";
  try {
    const res = await api("/api/github_repos" + (force ? "?force=1" : ""));
    if (res.status !== 200 || !res.data.ok) {
      if (note) note.textContent = (res.data && res.data.error) || "dépôts injoignables.";
      allRepos = [];
      reposLoaded = true;
      paintRepos();
      return;
    }
    allRepos = res.data.repos || [];
    reposLoaded = true;
    const warn = (res.data.warnings || []).join(" ; ");
    if (note) note.textContent = (allRepos.length
      ? allRepos.length + " dépôt(s). Clique pour viser ce dépôt en mode Cloud."
      : "Aucun dépôt visible pour ce compte.") + (warn ? " ⚠️ " + warn : "");
    paintRepos();
  } catch (e) {
    reposLoaded = true;  // pas de nouvel essai à chaque poll : « Actualiser » relance
    if (note) note.textContent = "Serveur injoignable. Clique sur Actualiser pour réessayer.";
  } finally {
    reposLoading = false;
  }
}

function paintRepos() {
  const list = $("repo-list");
  const note = $("repos-note");
  if (!list) return;
  const q = (($("repo-search") || {}).value || "").toLowerCase();
  // textContent partout : un nom de dépôt ne devient jamais du HTML.
  const shown = allRepos.filter((r) => !q || (r.repo || "").toLowerCase().includes(q));
  const key = JSON.stringify([allRepos, q, ($("ceo_mode") || {}).value, currentRepo]);
  if (list.dataset.key === key) return;
  list.dataset.key = key;
  list.replaceChildren();
  if (!reposLoaded) {
    list.append(el("p", "muted", "Chargement…"));
    return;
  }
  if (!allRepos.length) {
    list.append(el("p", "muted", "Aucun dépôt — vérifie « gh auth login » dans ton Terminal, puis Actualiser."));
    return;
  }
  if (!shown.length) {
    list.append(el("p", "muted", "Aucun dépôt ne correspond au filtre."));
    return;
  }
  for (const r of shown.slice(0, 100)) {
    const row = el("button", "repo-row");
    row.type = "button";
    row.setAttribute("role", "option");
    const selected = r.repo === currentRepo;
    row.setAttribute("aria-selected", selected ? "true" : "false");
    if (selected) row.classList.add("on");
    row.title = selected ? "Dépôt actuel du mode Cloud — clique pour le retirer"
                         : "Travailler sur " + r.repo + " en mode Cloud";
    row.append(el("strong", "", (selected ? "✓ " : "") + r.repo));
    row.append(el("span", "lock", r.private ? "🔒 privé" : "🌍 public"));
    if (r.updated) row.append(el("span", "when", r.updated));
    row.addEventListener("click", () => setRepo(selected ? "" : r.repo));
    list.append(row);
  }
  if (note && shown.length > 100) note.textContent = shown.length + " dépôts : les 100 premiers affichés.";
}

function render(state) {
  if (followWorkspace(state)) return;
  if (state.chat_reset) { location.reload(); return; }  // conversation remise à zéro : ids repartis de 1
  busy = state.busy;
  const where = state.team && state.team.ceo && state.team.ceo.where;
  const demo = state.dry_run ? "— MODE DÉMO (dry-run)" : "";
  $("mode").textContent = (demo + (demo && where ? " " : "") + (where || "")).trim();
  $("send").disabled = busy;
  $("send").hidden = busy;
  $("stop").hidden = !busy;
  $("retry").disabled = busy;
  $("selftest").disabled = busy;
  renderChat(state.chat);
  renderTyping(state);
  renderTeam(state.team);
  renderTasks(state);
  renderJournal(state.journal);
  renderSettings(state);
  renderAccount(state);
  since = state.next;
  chatSince = state.chat_last;
}

let lastAccountState = "idle";

function renderAccount(state) {
  // Changement de compte Claude : texte du serveur (déjà en français, secrets masqués), affiché tel quel.
  const acc = state.account || { state: "idle", message: "" };
  const box = $("auth-switch-state"), go = $("auth-switch"), stop = $("auth-switch-cancel");
  if (!box || !go || !stop) return;
  const running = acc.state === "running";
  go.disabled = running || !!state.busy;
  stop.hidden = !running;
  box.textContent = acc.message || "";
  if (lastAccountState === "running" && !running) {  // vient de se terminer : relire le vrai compte
    refreshAuth();
    refreshUsage(true);
    banner(acc.message, acc.state === "done" ? "info" : "");
  }
  lastAccountState = acc.state;
}

async function refresh() {
  clearTimeout(timer);
  if (stopped) return;
  let res = null;
  try {
    res = await api("/api/state?since=" + since + "&chat_since=" + chatSince);
  } catch (e) {  // réseau coupé : le serveur ne répond plus du tout
    banner("Serveur injoignable : le Terminal de 9AM9AM-TEAM a-t-il été fermé ? "
           + "Relance 9AM9AM-TEAM.command.", "offline");
    $("send").disabled = true;
  }
  if (res && res.status !== 200) {  // le serveur répond, mais avec une erreur : on dit laquelle
    banner("Le serveur a répondu une erreur : " + (res.data.error || "code " + res.status), "server");
  } else if (res) {
    if (["offline", "server", "render"].includes($("banner").dataset.kind)) banner("");
    try {
      render(res.data);
    } catch (e) {
      banner("Erreur d'affichage de la page : " + e.message + ". Recharge la page si elle reste figée.", "render");
      // Les curseurs avancent quand même : sinon chaque relecture réafficherait les mêmes messages.
      since = res.data.next;
      chatSince = res.data.chat_last;
    }
  }
  timer = setTimeout(refresh, busy ? 500 : 1500);
}

/* ------------------------------------------------------------ actions */

async function act(action) {
  if (action === "quit" && busy && !confirm("Le CEO est au travail : l'interrompre et quitter ?")) return;
  let res;
  try {
    res = await api("/api/action", { action });
  } catch (e) {
    banner("Serveur injoignable.", "offline");
    return;
  }
  if (res.status >= 400) { banner(res.data.error || "erreur " + res.status); return; }
  banner("");
  if (action === "quit") {
    stopped = true;
    clearTimeout(timer);
    for (const b of document.querySelectorAll("button, textarea, input")) b.disabled = true;
    banner("Interface arrêtée. Tu peux fermer cet onglet.", "info");
    return;
  }
  refresh();
}

async function send(text, payload) {
  let res;
  try {
    const body = { message: text };
    if (payload) {
      if (payload.files && payload.files.length) body.files = payload.files;
      if (payload.pasted && payload.pasted.length) body.pasted = payload.pasted;
    } else if (payload === undefined && wsFiles.length) {
      const p = wsPayload();
      if (p.files.length) body.files = p.files;
      if (p.pasted.length) body.pasted = p.pasted;
    }
    res = await api("/api/chat", body);
  } catch (e) {
    banner("Serveur injoignable : message non envoyé.", "offline");
    return false;
  }
  if (res.status !== 202) { banner(res.data.error || "message refusé (" + res.status + ")"); return false; }
  if (res.data && res.data.notes) banner(res.data.notes.join(" "), "info");
  else banner("");
  busy = true;
  refresh();
  return true;
}

function autosize() {
  const input = $("input");
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 200) + "px";
}

async function submit() {
  const input = $("input");
  const text = input.value.trim();
  if (!text && wsFiles.length === 0) return;
  if (busy) { banner("Le CEO est déjà au travail : attends sa réponse ou clique sur « Arrêter »."); return; }
  if (await send(text || "(fichiers joints, sans message)", wsPayload())) {
    input.value = ""; autosize(); setWsFiles([]);
  }
}

function saveLater(key) {
  clearTimeout(pendingSave.get(key));
  pendingSave.set(key, setTimeout(async () => {
    try {
      const res = await api("/api/setting", { key, value: $(key).value });
      if (res.status !== 200) banner(res.data.error || "réglage non sauvegardé");
      else if (key === "ceo_model" || key === "ceo_mode" || key === "ceo_cloud_session") { banner(""); refresh(); }
    } catch (e) {
      banner("Réglage non sauvegardé : serveur injoignable.", "offline");
    } finally {
      pendingSave.delete(key);
    }
  }, 500));
}

$("composer").addEventListener("submit", (e) => { e.preventDefault(); submit(); });
$("input").addEventListener("input", autosize);
$("input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); submit(); }
});
$("stop").addEventListener("click", () => act("stop"));
$("quit").addEventListener("click", () => act("quit"));
$("retry").addEventListener("click", () => act("retry"));
$("selftest").addEventListener("click", () => act("selftest"));
$("auth-refresh").addEventListener("click", refreshAuth);
$("verify-approve").addEventListener("click", async () => {
  const cmd = $("verify-approve").dataset.cmd || "";
  if (!cmd || !confirm("Autoriser « " + cmd + " » ? Elle exécutera le code de ce dossier après chaque travail.")) return;
  const res = await api("/api/verify_approve", { cmd });
  if (res.status !== 200) banner(res.data.error || "accord non enregistré");
  else banner("Vérification autorisée pour ce dossier.", "info");
  refresh();
});
$("auth-logout").addEventListener("click", async () => {
  if (!confirm("Déconnecter Claude Code sur ce Mac ?")) return;
  try {
    const res = await api("/api/auth_logout", {});
    if (res.status !== 200) banner(res.data.error || "déconnexion impossible");
    else banner("Déconnecté.", "info");
  } catch (e) {
    banner("Déconnexion non faite : serveur injoignable.", "offline");
  }
  refreshAuth();
});
$("auth-switch").addEventListener("click", async () => {
  const email = $("auth-email").value.trim();
  if (!email || !email.includes("@")) { banner("Indique l'email du compte à utiliser."); return; }
  if (!confirm("Déconnecter le compte Claude Code actuel, puis ouvrir le navigateur pour te connecter avec "
               + email + " ?")) return;
  try {
    const res = await api("/api/auth_switch", { email });
    if (res.status !== 202) banner(res.data.error || "changement de compte refusé");
    else banner("Changement de compte lancé : valide la connexion dans le navigateur.", "info");
  } catch (e) {
    banner("Changement de compte non lancé : serveur injoignable.", "offline");
  }
  refresh();
});
$("auth-switch-cancel").addEventListener("click", async () => {
  try {
    await api("/api/auth_switch_cancel", {});
  } catch (e) {
    banner("Annulation non envoyée : serveur injoignable.", "offline");
  }
  refresh();
});
$("auth-login").addEventListener("click", async () => {
  const email = $("auth-email").value.trim();
  if (!email || !email.includes("@")) { banner("Indique ton email pour générer la commande."); return; }
  const cmd = "claude auth login --email " + email;
  try { await navigator.clipboard.writeText(cmd); banner("Commande copiée : colle-la dans ton Terminal.", "info"); }
  catch (e) { banner(cmd, "info"); }
});
$("ca-add").addEventListener("click", async () => {
  let res;
  try {
    res = await api("/api/custom_agent", {
      name: $("ca-name").value, cmd: $("ca-cmd").value, model: $("ca-model").value,
      effort: $("ca-effort").value });
  } catch (e) {
    banner("Agent non ajouté : serveur injoignable.", "offline");
    return;
  }
  if (res.status !== 200) { banner(res.data.error || "ajout impossible"); return; }
  banner("");
  $("ca-name").value = ""; $("ca-cmd").value = ""; $("ca-model").value = ""; $("ca-effort").value = "";
  refresh();
});
/* Model du CEO : une vraie liste (une saisie à suggestions ne montrerait que ce qui ressemble
   à la valeur écrite). « Autre… » laisse taper un nom complet. */
function syncCeoModelPick() {
  const pick = $("ceo_model_pick"), text = $("ceo_model");
  if (!pick || !text || document.activeElement === text) return;
  const known = CEO_MODEL_CHOICES.includes(text.value.trim());
  pick.value = known ? text.value.trim() : CEO_MODEL_OTHER;
  text.hidden = known;
}
(function () {
  const pick = $("ceo_model_pick"), text = $("ceo_model");
  if (!pick || !text) return;
  for (const value of CEO_MODEL_CHOICES.concat([CEO_MODEL_OTHER])) {
    const o = document.createElement("option");
    o.value = value;
    o.textContent = value === "" ? "défaut de Claude Code" : (value === CEO_MODEL_OTHER ? "Autre… (nom complet)" : value);
    pick.append(o);
  }
  pick.addEventListener("change", () => {
    if (pick.value === CEO_MODEL_OTHER) { text.hidden = false; text.value = ""; text.focus(); return; }
    text.hidden = true;
    text.value = pick.value;
    text.dispatchEvent(new Event("input"));  // même enregistrement que la saisie
  });
})();
/* Model d'opencode : une vraie liste des models gratuits scannés à chaque ouverture
   (catalogue `opencode models`). Le model enregistré reste proposé même absent du scan ;
   « Autre… » laisse taper un nom complet. */
const FREE_SCAN_NOTE = {
  scanning: () => "🔍 Scan des models gratuits d'opencode en cours…",
  ok: (s) => s.models.length
    ? "✓ " + s.models.length + " models gratuits trouvés sur opencode (scan de " + s.at + ")."
    : "Aucun model gratuit dans le catalogue opencode (scan de " + s.at + ").",
  error: (s) => "✗ Scan impossible : " + s.error + (s.models.length ? " — liste du dernier scan réussi." : ""),
  off: (s) => s.error,
  idle: () => "Scan des models gratuits pas encore lancé.",
};
const SCAN_ABSENT = " (actuel — absent du scan)";
const LIST_ABSENT = " (actuel — pas dans ta liste)";
function fillFreeModelPick(pick, values, listed, opts) {
  const o0 = opts || { absent: SCAN_ABSENT };
  const key = JSON.stringify([values, listed, o0]);  // le libellé dépend aussi de la liste
  if (pick.dataset.key === key || document.activeElement === pick) return;
  pick.replaceChildren();
  if (o0.emptyLabel !== undefined) {  // « rien de réglé » est un état normal (ex. Cline)
    const none = document.createElement("option");
    none.value = "";
    none.textContent = o0.emptyLabel;
    pick.append(none);
  }
  for (const m of values) {
    const o = document.createElement("option");
    o.value = m;
    // « absent » seulement si une liste existe vraiment (sinon : libellé inventé).
    o.textContent = !listed.length || listed.includes(m) ? m : m + o0.absent;
    pick.append(o);
  }
  const other = document.createElement("option");
  other.value = CEO_MODEL_OTHER;
  other.textContent = "Autre… (nom complet)";
  pick.append(other);
  pick.dataset.key = key;
}
/* Liste + champ caché : le model enregistré reste choisi même absent de la liste. */
function syncModelPick(pick, text, listed, opts) {
  const current = text.value.trim();
  const values = current && !listed.includes(current) ? [current].concat(listed) : listed.slice();
  fillFreeModelPick(pick, values, listed, opts);
  if (document.activeElement === pick || document.activeElement === text) return;
  if (current) { pick.value = current; text.hidden = true; }
  else if (opts.emptyLabel !== undefined) { pick.value = ""; text.hidden = true; }
}
function renderClineModels(models) {
  const pick = $("cline_model_pick"), text = $("cline_model");
  if (!pick || !text) return;
  syncModelPick(pick, text, models || [], { absent: LIST_ABSENT, emptyLabel: "défaut de l'outil" });
}
function renderFreeModels(scan) {
  const pick = $("opencode_model_pick"), text = $("opencode_model"), note = $("free-models-note");
  if (!pick || !text) return;
  const s = scan || { status: "idle", models: [], error: "", at: "" };
  syncModelPick(pick, text, s.models, { absent: SCAN_ABSENT });
  if (note) note.textContent = (FREE_SCAN_NOTE[s.status] || (() => ""))(s);
  const rescan = $("free-models-rescan");
  if (rescan) rescan.disabled = s.status === "scanning";
}
function bindModelPick(pick, text) {
  pick.addEventListener("change", () => {
    if (pick.value === CEO_MODEL_OTHER) { text.hidden = false; text.value = ""; text.focus(); return; }
    text.hidden = true;
    text.value = pick.value;
    text.dispatchEvent(new Event("input"));  // même enregistrement que la saisie
  });
}
(function () {
  const clinePick = $("cline_model_pick"), clineText = $("cline_model");
  if (clinePick && clineText) bindModelPick(clinePick, clineText);
  const pick = $("opencode_model_pick"), text = $("opencode_model"), rescan = $("free-models-rescan");
  if (!pick || !text) return;
  bindModelPick(pick, text);
  if (rescan) rescan.addEventListener("click", async () => {
    rescan.disabled = true;
    try {
      const res = await api("/api/free_models_scan", {});
      const scan = res.data.free_models || {};
      if (res.status !== 200) banner(res.data.error || "scan non lancé");
      else if (!res.data.ok && scan.status !== "scanning") banner(scan.error || "scan non lancé", "info");
    } catch (e) {
      banner("Scan non lancé : serveur injoignable.", "offline");
    }
    refresh();
  });
})();
for (const sel of document.querySelectorAll("select.effort")) {
  for (const level of EFFORTS) {
    const o = document.createElement("option");
    o.value = level;
    o.textContent = level || "défaut de l'outil";
    sel.append(o);
  }
}
for (const chip of document.querySelectorAll(".chip")) {
  chip.addEventListener("click", () => { $("input").value = chip.textContent; autosize(); $("input").focus(); });
}
for (const key of SETTINGS) {
  if (key === "ceo_mode" || key === "ceo_cloud_repo") continue;  // boutons dédiés, pas de champ auto
  const input = $(key);
  if (input) input.addEventListener("input", () => saveLater(key));
}
for (const sw of document.querySelectorAll(".swatch")) {
  sw.style.backgroundColor = sw.dataset.color;  // CSSOM : le HTML ne porte jamais de style en dur (CSP)
  sw.addEventListener("click", () => {
    const input = $("ui_color");
    if (input) { input.value = sw.dataset.color; saveLater("ui_color"); }
    applyTheme(sw.dataset.color);
  });
}
const colorInput = $("ui_color");
if (colorInput) colorInput.addEventListener("input", () => applyTheme(colorInput.value));
const modeLocal = $("mode-local");
const modeCloud = $("mode-cloud");
if (modeLocal) modeLocal.addEventListener("click", () => setMode("local"));
if (modeCloud) modeCloud.addEventListener("click", () => setMode("cloud"));
const reposBtn = $("repos-refresh");
if (reposBtn) reposBtn.addEventListener("click", () => loadRepos(true));
const repoSearch = $("repo-search");
if (repoSearch) repoSearch.addEventListener("input", () => paintRepos());

/* ------------------------------------------------------------ bureau Linux */

function tickClock() {
  const clock = $("clock");
  if (!clock) return;
  try {
    clock.textContent = new Date().toLocaleString("fr-FR", {
      weekday: "short", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
    });
  } catch (e) { clock.textContent = ""; }
}

function gotoPanel(where) {
  const targets = { chat: "card-chat", tasks: "card-tasks", journal: "card-journal", settings: "card-settings" };
  const node = $(targets[where] || "");
  if (!node) return;
  if (node.tagName === "DETAILS" && !node.open) node.open = true;
  if (node.scrollIntoView) node.scrollIntoView({ behavior: "smooth", block: "nearest" });
  if (where === "chat") $("input").focus();
  else if (node.focus) node.focus({ preventScroll: true });
}

for (const btn of document.querySelectorAll(".dock button")) {
  btn.addEventListener("click", () => gotoPanel(btn.dataset.goto));
}
const activities = $("activities");
if (activities) activities.addEventListener("click", () => gotoPanel("chat"));
tickClock();
setInterval(tickClock, 30000);

/* ------------------------------------------------------------ usage de l'abonnement Claude (ligne du haut) */

const USAGE_REFRESH_MS = 120000;  // même rythme que le cache serveur

function paintUsage(u) {
  const box = $("usage");
  if (!box) return;
  box.className = "top-usage";
  if (!u || !u.ok) {
    box.textContent = "Claude : usage inconnu";
    box.title = (u && u.error) || "usage non lu";
    return;
  }
  const worst = u.meters.some((m) => m.level === "limit") ? "limit"
    : (u.meters.some((m) => m.level === "warn") ? "warn" : "");
  if (worst) box.classList.add(worst);
  const parts = u.meters.map((m) => m.label + " " + m.pct + " %");
  box.textContent = u.limit_reached
    ? "⛔ Limite Claude atteinte — " + parts.join(" · ") + (u.limit_reset ? " · reprise : " + u.limit_reset : "")
    : "Claude : " + parts.join(" · ");
  box.title = u.meters.map((m) => m.label + " : " + m.pct + " % utilisé" + (m.reset ? " · remise à zéro " + m.reset : "")).join("\n");
}

let usageAt = 0;  // dernière lecture réussie (horloge de la page)

async function refreshUsage(force) {
  // Onglet masqué : on économise le sous-process, sauf pour la toute première lecture.
  if (document.hidden && !force && usageAt) return;
  try {
    const res = await api("/api/usage");
    paintUsage(res.status === 200 ? res.data : null);
    usageAt = Date.now();
  } catch (e) {
    paintUsage({ ok: false, error: "serveur injoignable" });
  }
}
refreshUsage(true);
setInterval(refreshUsage, USAGE_REFRESH_MS);
document.addEventListener("visibilitychange", () => {  // de retour sur la page : valeur à jour
  if (!document.hidden && Date.now() - usageAt > USAGE_REFRESH_MS) refreshUsage(true);
});

/* ------------------------------------------------------------ workspace : onglet dossier + fichiers joints */

let wsFiles = [];   // {kind:"ws", path} (dossier de travail) ou {kind:"local", name, text} (ton Mac)
let wsPickText = [];

function wsLabel(f) {
  return typeof f === "string" ? f : (f.kind === "local" ? f.name + " (ton Mac)" : f.path);
}

function setWsFiles(files) {
  const seen = [];
  wsFiles = [];
  for (const f of files || []) {
    const k = wsLabel(f);
    if (!k || seen.indexOf(k) >= 0) continue;
    seen.push(k);
    wsFiles.push(f);
    if (wsFiles.length >= 5) break;
  }
  const box = $("ws-files");
  box.textContent = "";
  for (const f of wsFiles) {
    const label = wsLabel(f);
    const chip = el("span", "ws-chip" + (typeof f !== "string" && f.kind === "local" ? " ws-local" : ""), label);
    const x = el("button", "", "✕");
    x.type = "button";
    x.title = "Retirer " + label;
    x.setAttribute("aria-label", "Retirer " + label);
    x.addEventListener("click", () => setWsFiles(wsFiles.filter((g) => wsLabel(g) !== label)));
    chip.append(x);
    box.append(chip);
  }
  const count = $("ws-count");
  count.textContent = wsFiles.length ? wsFiles.length + " joint(s)" : "";
}

function wsPayload() {
  const ws = [];
  const pasted = [];
  for (const f of wsFiles) {
    if (typeof f === "string") ws.push(f);
    else if (f.kind === "ws") ws.push(f.path);
    else if (f.kind === "local") pasted.push({ name: f.name, text: f.text });
  }
  return { files: ws, pasted: pasted };
}

async function wsLoad(dir) {
  const note = $("ws-note");
  try {
    const res = await api("/api/workspace?dir=" + encodeURIComponent(dir || ""));
    if (res.status !== 200) {
      note.textContent = res.data.error || "dossier illisible.";
      note.hidden = false;
      return;
    }
    const root = String(res.data.root || "").replace(/\/+$/, "");
    $("ws-root").textContent = root.split("/").pop() || "…";   // le nom du dossier seulement, pas son chemin
    $("ws-root").title = "Dossier de travail";
    note.textContent = res.data.note || "";
    note.hidden = !res.data.note;
  } catch (e) {
    note.textContent = "Serveur injoignable.";
    note.hidden = false;
  }
}

$("ws-tab").addEventListener("click", () => {
  const panel = $("ws-panel");
  const open = panel.hidden;
  panel.hidden = !open;
  $("ws-tab").setAttribute("aria-selected", open ? "true" : "false");
  if (open) wsLoad("");
});
$("ws-clear").addEventListener("click", () => setWsFiles([]));

/* Sélecteur natif + glisser-déposer : parcourt TON Mac, pas seulement le
   dossier de travail. Le navigateur ne donne jamais le vrai chemin (vie
   privée) : on lit le contenu côté page et on l'envoie en texte au serveur.
   Bornes : 5 fichiers, texte seul, 6000 car./fichier. */

function wsAddLocalFiles(fileList) {
  const files = [];
  for (let i = 0; i < (fileList || []).length; i++) files.push(fileList[i]);
  if (!files.length) return;
  if (wsFiles.length + files.length > 5) {
    banner("5 fichiers joints au maximum.", "files");
    files.splice(5 - wsFiles.length);
  }
  let pending = files.length;
  const done = () => { pending -= 1; if (pending <= 0) $("input").focus(); };
  for (const f of files) {
    if (f.size > 300000) { banner(f.name + " : trop gros (> 300 ko), non joint.", "files"); done(); continue; }
    const rd = new FileReader();
    rd.onload = () => {
      let text = String(rd.result || "");
      if (text.indexOf("\u0000") >= 0) { banner(f.name + " : binaire, non joint.", "files"); }
      else {
        text = text.trim();
        if (text.length > 6000) text = "(debut omis) " + text.slice(-6000);
        setWsFiles(wsFiles.concat([{ kind: "local", name: f.name, text: text }]));
        clearBannerKeepFiles();
      }
      done();
    };
    rd.onerror = () => { banner(f.name + " : lecture impossible.", "files"); done(); };
    rd.readAsText(f);
  }
}

$("ws-add-file").addEventListener("click", () => $("ws-pick").click());
$("ws-browse").addEventListener("click", async () => {
  if (busy) { banner("Le CEO est au travail : change de dossier après la fin, ou clique sur « Arrêter »."); return; }
  const btn = $("ws-browse");
  btn.disabled = true;
  banner("Choisis le dossier de travail dans la fenêtre du Mac qui vient de s'ouvrir…", "info");
  try {
    const res = await api("/api/workspace_pick", {});
    const data = res.data || {};
    if (data.ok) { location.reload(); return; }  // tâches et conversation du nouveau dossier
    banner(data.cancelled ? "" : (data.error || "changement de dossier impossible"));
  } catch (e) {
    banner("Serveur injoignable.", "offline");
  } finally {
    btn.disabled = false;
  }
});
$("ws-pick").addEventListener("change", (e) => {
  wsAddLocalFiles(e.target.files);
  e.target.value = "";
});
(function () {
  const drop = $("ws-drop");
  if (!drop) return;
  ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => {
    e.preventDefault(); drop.classList.add("over");
  }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => {
    e.preventDefault(); drop.classList.remove("over");
  }));
  drop.addEventListener("drop", (e) => {
    const dt = e.dataTransfer;
    if (dt && dt.files && dt.files.length) wsAddLocalFiles(dt.files);
  });
})();

$("input").focus();
wsLoad("");
refresh();
refreshAuth();
