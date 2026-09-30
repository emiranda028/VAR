"use strict";

const $ = (id) => document.getElementById(id);
const pct = (x) => (x == null ? "—" : `${Math.round(x * 100)}%`);
const state = { user: null, types: [], type: null, values: {}, referees: [], review: null };
const ROLES = { admin: "Administrador", analista: "Analista", operador: "Operador VAR", arbitro: "Árbitro" };
const REC_CLASS = { COBRAR: "yes", "NO COBRAR": "no", DUDOSA: "doubt" };

// ---------- utilidades ----------

/** Crea un elemento: el("td", {className: "num"}, "texto", otroNodo). Los strings van como texto (sin HTML). */
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "dataset") Object.assign(node.dataset, v);
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (k in node) node[k] = v;
    else node.setAttribute(k, v);
  }
  for (const c of children.flat()) if (c != null) node.append(c);
  return node;
}

function can(permission) {
  return state.user && state.user.permissions.includes(permission);
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    ...options,
  });
  if (res.status === 401 && !path.startsWith("/api/auth/login")) {
    showLogin();
    throw new Error("Tu sesión venció. Volvé a ingresar.");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const detail = Array.isArray(body.detail) ? body.detail.map((d) => d.msg).join("; ") : body.detail;
    throw new Error(detail || `Error ${res.status}`);
  }
  return res.json();
}

const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body ?? {}) });

let toastTimer;
function toast(message, bad = false) {
  const t = $("toast");
  t.textContent = message;
  t.className = bad ? "toast bad" : "toast";
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), 4000);
}

async function busy(button, fn) {
  button.disabled = true;
  try {
    return await fn();
  } catch (err) {
    toast(err.message, true);
  } finally {
    button.disabled = false;
  }
}

function today() {
  const d = new Date();
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
}

function refereeOptions(select, { empty, selected } = {}) {
  select.replaceChildren();
  if (empty) select.append(el("option", { value: "" }, empty));
  for (const r of state.referees) {
    select.append(el("option", { value: r.id, selected: r.id === selected }, `${r.name}${r.country ? ` (${r.country})` : ""}`));
  }
}

function pill(kind, text) {
  return el("span", { className: `pill ${kind}` }, text);
}

// ---------- sesión ----------

function showLogin() {
  state.user = null;
  $("app-view").hidden = true;
  $("login-view").hidden = false;
  $("login-user").focus();
}

async function startApp(user) {
  state.user = user;
  $("login-view").hidden = true;
  $("app-view").hidden = false;
  $("user-name").textContent = user.username;
  $("user-role").textContent = user.role_label + (user.referee ? ` · ${user.referee}` : "");
  buildTabs();
  const [types, refs] = await Promise.all([api("/api/play-types"), api("/api/referees")]);
  state.types = types;
  state.referees = refs;
  setupReviewForm();
}

$("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("login-error").textContent = "";
  try {
    const r = await post("/api/auth/login", { username: $("login-user").value.trim(), password: $("login-pass").value });
    $("login-pass").value = "";
    await startApp(r.user);
  } catch (err) {
    $("login-error").textContent = err.message;
  }
});

$("btn-logout").addEventListener("click", async () => {
  await post("/api/auth/logout").catch(() => {});
  showLogin();
});

$("btn-password").addEventListener("click", () => {
  $("pw-error").textContent = "";
  $("password-form").reset();
  $("password-dialog").showModal();
});
$("pw-cancel").addEventListener("click", () => $("password-dialog").close());
$("password-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await post("/api/auth/password", { current: $("pw-current").value, new: $("pw-new").value });
    $("password-dialog").close();
    toast("Contraseña actualizada. Ingresá con la nueva.");
    showLogin();
  } catch (err) {
    $("pw-error").textContent = err.message;
  }
});

// ---------- pestañas ----------

const LOADERS = { "tab-history": loadHistory, "tab-data": loadData, "tab-users": loadUsers };

function buildTabs() {
  const tabs = $("tabs");
  tabs.replaceChildren();
  const pages = [...document.querySelectorAll("[data-tab]")].filter((p) => can(p.dataset.perm));
  for (const page of pages) {
    tabs.append(el("button", { type: "button", role: "tab", dataset: { target: page.id }, onclick: () => showTab(page.id) }, page.dataset.tab));
  }
  showTab(pages[0].id);
}

function showTab(id) {
  for (const page of document.querySelectorAll("[data-tab]")) page.hidden = page.id !== id;
  for (const b of $("tabs").children) b.setAttribute("aria-selected", String(b.dataset.target === id));
  if (LOADERS[id]) LOADERS[id]().catch((err) => toast(err.message, true));
}

// ---------- revisión ----------

function segmented(options, current, onChange) {
  const wrap = el("div", { className: "segmented", role: "radiogroup" });
  for (const [value, label] of options) {
    const b = el("button", { type: "button", role: "radio" }, label);
    b.setAttribute("aria-checked", String(value === current));
    b.addEventListener("click", () => {
      wrap.querySelectorAll("button").forEach((x) => x.setAttribute("aria-checked", "false"));
      b.setAttribute("aria-checked", "true");
      onChange(value);
    });
    wrap.append(b);
  }
  return wrap;
}

function setupReviewForm() {
  const sel = $("referee");
  const bound = state.user.role === "arbitro" && state.user.referee_id;
  if (bound) {
    refereeOptions(sel, { selected: state.user.referee_id });
    sel.disabled = true;
  } else {
    refereeOptions(sel, { empty: "(sin árbitro — sólo jugadas similares)" });
    sel.disabled = false;
  }
  state.type = state.types[0];
  const types = segmented(state.types.map((t) => [t.key, t.label]), state.type.key, selectType);
  types.id = "play-types";
  $("play-types").replaceWith(types);
  selectType(state.type.key);
  $("result").hidden = true;
  $("empty").hidden = false;
}

function selectType(key) {
  state.type = state.types.find((t) => t.key === key);
  state.values = {};
  const box = $("features");
  box.replaceChildren();
  for (const f of state.type.features) {
    if (f.kind === "bool") {
      state.values[f.key] = false;
      box.append(el("div", { className: "toggle", title: f.help || "" }, el("span", {}, f.label),
        segmented([[true, "Sí"], [false, "No"]], false, (v) => (state.values[f.key] = v))));
    } else if (f.kind === "scale") {
      state.values[f.key] = f.min;
      const opts = f.options.map((label, i) => [f.min + i, label]);
      box.append(el("div", { className: "range-row" }, el("span", {}, f.label),
        segmented(opts, f.min, (v) => (state.values[f.key] = v))));
    } else {
      state.values[f.key] = 0;
      const input = el("input", { type: "number", step: "0.1", min: f.min, max: f.max, value: 0, required: true });
      input.addEventListener("input", () => (state.values[f.key] = Number(input.value)));
      box.append(el("label", { className: "range-row" }, el("span", {}, f.label), input));
    }
  }
}

function renderPlays(listId, plays, emptyText) {
  const ul = $(listId);
  ul.replaceChildren();
  if (!plays.length) {
    ul.append(el("li", {}, el("span", { className: "none" }, emptyText)));
    return;
  }
  for (const p of plays) {
    ul.append(el("li", {},
      el("span", { className: `tag ${p.called ? "yes" : "no"}` }, p.called ? "COBRÓ" : "NO COBRÓ"),
      el("div", {}, el("div", { className: "desc" }, p.description || "(sin descripción)"),
        el("div", { className: "meta" }, [p.referee, p.match, p.date].filter(Boolean).join(" · "))),
      el("span", { className: "sim", title: "Similitud con la jugada actual" }, pct(p.similarity))));
  }
}

function renderResult(r) {
  state.review = r;
  $("empty").hidden = true;
  $("result").hidden = false;
  $("question").textContent = r.question + (r.referee ? ` — ${r.referee.name}` : "");
  const v = $("verdict");
  v.textContent = r.recommendation;
  v.className = `verdict ${REC_CLASS[r.recommendation]}`;
  $("bar-yes").style.width = pct(r.probability_call);
  $("bar-no").style.width = pct(r.probability_no_call);
  $("p-yes").textContent = pct(r.probability_call);
  $("p-no").textContent = pct(r.probability_no_call);
  $("confidence").textContent = `Confianza ${r.confidence} · basada en ${r.components.similar_plays_effective_n} jugadas equivalentes`;

  const c = r.components;
  $("s-general").textContent = pct(c.similar_plays_probability);
  $("s-general-n").textContent = `n efectivo ${c.similar_plays_effective_n}`;
  $("s-ref").textContent = pct(c.referee_similar_probability);
  $("s-ref-n").textContent = c.peers_similar_probability == null
    ? "sin jugadas comparables"
    : `colegas: ${pct(c.peers_similar_probability)} · n ${c.referee_similar_effective_n}`;
  const adj = Math.round(c.referee_adjustment * 100);
  $("s-adj").textContent = `${adj > 0 ? "+" : ""}${adj} pts`;
  $("s-adj-d").textContent = adj > 1 ? "más propenso a cobrar" : adj < -1 ? "menos propenso a cobrar" : "sin sesgo relevante";
  $("s-base").textContent = pct(c.base_rate);

  renderPlays("ref-plays", r.referee_similar_plays, "Este árbitro no tiene jugadas parecidas registradas.");
  renderPlays("similar-plays", r.similar_plays, "No hay jugadas parecidas en el histórico.");

  // Decisión final
  $("decision").hidden = !can("decide");
  $("decision-form").hidden = false;
  $("decision-done").hidden = true;
  $("decision-ref-field").hidden = !!r.referee;
  if (!r.referee) refereeOptions($("decision-referee"));
  $("decision-date").value = today();
  $("decision-match").value = "";
  $("decision-desc").value = "";
}

$("play-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const btn = e.submitter || document.querySelector("#play-form .primary");
  busy(btn, async () => {
    const refereeId = $("referee").value;
    const r = await post("/api/predict", {
      play_type: state.type.key,
      referee_id: refereeId ? Number(refereeId) : null,
      features: state.values,
    });
    renderResult(r);
  });
});

for (const b of document.querySelectorAll(".decision-buttons button")) {
  b.addEventListener("click", () => busy(b, async () => {
    const called = b.dataset.called === "true";
    const body = {
      called,
      match: $("decision-match").value.trim(),
      date: $("decision-date").value,
      description: $("decision-desc").value.trim(),
    };
    if (!state.review.referee) body.referee_id = Number($("decision-referee").value);
    await post(`/api/reviews/${state.review.review_id}/decision`, body);
    $("decision-form").hidden = true;
    const done = $("decision-done");
    done.hidden = false;
    const matched = (state.review.probability_call >= 0.5) === called;
    done.textContent = `Registrado: ${called ? "se cobró" : "no se cobró"}. La jugada ya forma parte del histórico. `
      + (state.review.recommendation === "DUDOSA" ? "(La sugerencia era dudosa.)" : matched ? "Coincidió con la sugerencia." : "No coincidió con la sugerencia.");
  }));
}

// ---------- historial ----------

function statTile(label, value, detail) {
  return el("div", { className: "stat" }, el("div", { className: "k" }, label), el("div", { className: "v" }, value),
    el("div", { className: "d" }, detail || ""));
}

async function loadHistory() {
  const [stats, reviews] = await Promise.all([api("/api/reviews/stats"), api("/api/reviews?limit=100")]);
  $("review-stats").replaceChildren(
    statTile("Revisiones con decisión", String(stats.decided), `${stats.pending} sin decisión registrada`),
    statTile("Acierto general", pct(stats.accuracy), "sugerencia ≥50% vs. decisión"),
    statTile("Acierto en no dudosas", pct(stats.decisive_accuracy), `${stats.decisive} revisiones`),
    statTile("Brier score", stats.brier == null ? "—" : stats.brier.toFixed(3), "menor es mejor (0 = perfecto)"),
  );
  const typeLabel = Object.fromEntries(state.types.map((t) => [t.key, t.label]));
  const tbody = $("reviews-table").querySelector("tbody");
  tbody.replaceChildren();
  if (!reviews.length) {
    tbody.append(el("tr", {}, el("td", { colSpan: 7, className: "muted" }, "Todavía no hay revisiones.")));
  }
  for (const r of reviews) {
    const final = r.final_called == null ? pill("pending", "PENDIENTE") : pill(r.final_called ? "yes" : "no", r.final_called ? "COBRÓ" : "NO COBRÓ");
    tbody.append(el("tr", {},
      el("td", { className: "num" }, r.created_at.slice(0, 16)),
      el("td", {}, r.referee || "—"),
      el("td", {}, typeLabel[r.play_type] || r.play_type),
      el("td", { className: "num" }, pct(r.probability)),
      el("td", {}, pill(REC_CLASS[r.recommendation], r.recommendation)),
      el("td", {}, final),
      el("td", { className: "muted" }, r.user || "")));
  }
}

// ---------- datos ----------

async function readFile() {
  const file = $("import-file").files[0];
  if (!file) throw new Error("Elegí un archivo CSV primero.");
  return file.text();
}

function renderImport(r, dryRun) {
  const box = $("import-result");
  box.replaceChildren();
  if (r.error_count) {
    box.append(el("div", { className: "notice" }, `${r.error_count} filas con errores: no se importó nada. ${r.valid} filas válidas.`),
      el("ul", { className: "errors" }, r.errors.map((e) => el("li", {}, `Fila ${e.row}: ${e.error}`))));
    return;
  }
  const newRefs = r.new_referees.length ? ` Árbitros nuevos: ${r.new_referees.join(", ")}.` : "";
  box.append(el("div", { className: "notice" }, dryRun
    ? `Archivo válido: ${r.valid} jugadas listas para importar.${newRefs}`
    : `Importadas ${r.imported} jugadas.${newRefs}`));
}

$("btn-validate").addEventListener("click", (e) => busy(e.currentTarget, async () => {
  renderImport(await post("/api/plays/import", { csv: await readFile(), dry_run: true }), true);
}));
$("btn-import").addEventListener("click", (e) => busy(e.currentTarget, async () => {
  const r = await post("/api/plays/import", { csv: await readFile() });
  renderImport(r, false);
  if (r.imported) {
    state.referees = await api("/api/referees");
    setupReviewForm();
    await loadData();
  }
}));

function renderParams(params) {
  const labels = {
    bandwidth: "Ancho similitud", referee_bandwidth: "Ancho árbitro", top_k: "Top K",
    base_prior: "Prior base", referee_prior: "Prior árbitro",
  };
  $("model-params").replaceChildren(...Object.entries(params).map(([k, v]) => el("span", { className: "chip" }, `${labels[k] || k}: ${v}`)));
}

function metricsView(m, title) {
  const cal = el("table", { className: "table" },
    el("thead", {}, el("tr", {}, el("th", {}, "Prob. dada"), el("th", {}, "Jugadas"), el("th", {}, "Promedio dado"), el("th", {}, "Se cobró realmente"))),
    el("tbody", {}, m.calibration.map((b) => el("tr", {},
      el("td", { className: "num" }, `${pct(b.from)}–${pct(b.to)}`), el("td", { className: "num" }, String(b.n)),
      el("td", { className: "num" }, pct(b.predicted)), el("td", { className: "num" }, pct(b.observed))))));
  return el("div", {},
    el("h3", {}, title),
    el("div", { className: "metrics" },
      statTile("Acierto", pct(m.accuracy), `${m.n} jugadas evaluadas de ${m.total_plays}`),
      statTile("Acierto en no dudosas", pct(m.decisive_accuracy), `${pct(m.decisive_share)} de las jugadas`),
      statTile("Log-loss", m.log_loss.toFixed(3), `sin árbitro ${m.log_loss_without_referee.toFixed(3)} · sólo promedio ${m.log_loss_base_rate.toFixed(3)}`),
      statTile("Brier", m.brier.toFixed(3), "menor es mejor")),
    el("h3", {}, "Calibración: ¿cuando dice X%, se cobra X%?"),
    el("div", { className: "table-wrap" }, cal));
}

$("btn-evaluate").addEventListener("click", (e) => busy(e.currentTarget, async () => {
  $("model-result").replaceChildren(el("div", { className: "notice" }, "Evaluando…"));
  const m = await post("/api/model/evaluate");
  $("model-result").replaceChildren(m.n ? metricsView(m, "Evaluación actual") : el("div", { className: "notice" }, "No hay jugadas cargadas."));
}));

$("btn-calibrate").addEventListener("click", (e) => busy(e.currentTarget, async () => {
  $("model-result").replaceChildren(el("div", { className: "notice" }, "Recalibrando (puede tardar unos segundos)…"));
  const r = await post("/api/model/calibrate");
  renderParams((r.applied ? r.after : r.before).params);
  $("model-result").replaceChildren(
    el("div", { className: "notice" }, r.applied
      ? `Nuevos parámetros aplicados. Log-loss: ${r.before.log_loss.toFixed(3)} → ${r.after.log_loss.toFixed(3)}.`
      : "Los parámetros actuales ya eran los mejores; no se cambió nada."),
    metricsView(r.applied ? r.after : r.before, "Evaluación con los parámetros vigentes"));
}));

$("referee-form").addEventListener("submit", (e) => {
  e.preventDefault();
  busy(e.submitter, async () => {
    await post("/api/referees", { name: $("new-ref-name").value.trim(), country: $("new-ref-country").value.trim() });
    $("referee-form").reset();
    state.referees = await api("/api/referees");
    setupReviewForm();
    await loadData();
    toast("Árbitro agregado.");
  });
});

async function loadData() {
  const [model, refs] = await Promise.all([api("/api/model"), api("/api/referees")]);
  state.referees = refs;
  renderParams(model.params);
  const tbody = $("referees-table").querySelector("tbody");
  tbody.replaceChildren(...refs.map((r) => el("tr", {},
    el("td", {}, r.name), el("td", {}, r.country || "—"), el("td", { className: "num" }, String(r.plays)))));
}

// ---------- usuarios ----------

function roleSelect(select, selected) {
  select.replaceChildren(...Object.entries(ROLES).map(([k, v]) => el("option", { value: k, selected: k === selected }, v)));
  return select;
}

async function patchUser(id, body, okMessage) {
  try {
    await api(`/api/users/${id}`, { method: "PATCH", body: JSON.stringify(body) });
    toast(okMessage);
  } catch (err) {
    toast(err.message, true);
  }
  await loadUsers();
}

async function loadUsers() {
  const users = await api("/api/users");
  roleSelect($("new-user-role"), "operador");
  refereeOptions($("new-user-ref"), { empty: "(sin árbitro vinculado)" });
  const tbody = $("users-table").querySelector("tbody");
  tbody.replaceChildren();
  for (const u of users) {
    const isMe = u.id === state.user.id;
    const role = roleSelect(el("select", { disabled: isMe, "aria-label": `Rol de ${u.username}` }), u.role);
    role.addEventListener("change", () => patchUser(u.id, { role: role.value }, "Rol actualizado."));
    const ref = el("select", { "aria-label": `Árbitro de ${u.username}` });
    refereeOptions(ref, { empty: "—", selected: u.referee_id });
    ref.addEventListener("change", () => patchUser(u.id, ref.value ? { referee_id: Number(ref.value) } : { clear_referee: true }, "Árbitro vinculado actualizado."));
    const toggle = el("button", { type: "button", className: "link", disabled: isMe },
      u.active ? "Desactivar" : "Activar");
    toggle.addEventListener("click", () => patchUser(u.id, { active: !u.active }, u.active ? "Usuario desactivado." : "Usuario activado."));
    const reset = el("button", { type: "button", className: "link" }, "Nueva clave");
    reset.addEventListener("click", () => {
      const password = prompt(`Nueva contraseña para ${u.username} (mínimo 8 caracteres):`);
      if (password) patchUser(u.id, { password }, "Contraseña actualizada. Se cerraron sus sesiones.");
    });
    tbody.append(el("tr", {},
      el("td", {}, u.username + (isMe ? " (vos)" : "")),
      el("td", {}, role),
      el("td", {}, ref),
      el("td", {}, u.active ? pill("yes", "ACTIVO") : pill("pending", "INACTIVO")),
      el("td", {}, el("div", { className: "user" }, toggle, reset))));
  }
}

$("user-form").addEventListener("submit", (e) => {
  e.preventDefault();
  busy(e.submitter, async () => {
    const refId = $("new-user-ref").value;
    await post("/api/users", {
      username: $("new-user-name").value.trim(),
      password: $("new-user-pass").value,
      role: $("new-user-role").value,
      referee_id: refId ? Number(refId) : null,
    });
    $("user-form").reset();
    toast("Usuario creado.");
    await loadUsers();
  });
});

// ---------- inicio ----------

api("/api/auth/me").then(startApp).catch(() => showLogin());
