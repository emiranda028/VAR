"use strict";

const $ = (id) => document.getElementById(id);
const pct = (x) => `${Math.round(x * 100)}%`;
const state = { types: [], type: null, values: {} };

async function api(path, options) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Error ${res.status}`);
  }
  return res.json();
}

// --- Modo estático (GitHub Pages): el modelo corre en el navegador con Pyodide ---
const ESTATICO = window.VARGENTO_ESTATICO === true;
const PYODIDE_URL = "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/";
const PYODIDE_JS = "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/pyodide.js";
const PY_DIR = "/vargento_py";
const PY_MODULOS = ["__init__.py", "domain.py", "model.py", "servicio.py"];
const estatico = { playTypes: null, referees: null, pyodide: null };

const PY_PUENTE = `
import json
from vargento.servicio import historia_desde_json, respuesta_prediccion

_HISTORIA = historia_desde_json(json.loads(_historia_json))
_ARBITROS = json.loads(_arbitros_json)
del _historia_json, _arbitros_json

def _predecir(cuerpo_json):
    c = json.loads(cuerpo_json)
    try:
        r = respuesta_prediccion(c["play_type"], c["features"], c.get("referee_id"), _HISTORIA, _ARBITROS)
    except (ValueError, LookupError) as exc:
        return json.dumps({"error": str(exc)})
    return json.dumps({"ok": r})
`;

async function textoRelativo(ruta) {
  const res = await fetch(ruta);
  if (!res.ok) throw new Error(`No se pudo leer ${ruta} (${res.status})`);
  return res.text();
}

function cargarScript(src) {
  return new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = src;
    s.onload = resolve;
    s.onerror = () => reject(new Error(`No se pudo cargar ${src}`));
    document.head.appendChild(s);
  });
}

async function cargarDatosEstaticos() {
  const [types, refs] = await Promise.all([
    textoRelativo("datos/play-types.json"),
    textoRelativo("datos/referees.json"),
  ]);
  estatico.playTypes = JSON.parse(types);
  estatico.referees = JSON.parse(refs);
}

async function cargarPyodide() {
  const [, historia, ...fuentes] = await Promise.all([
    cargarScript(PYODIDE_JS),
    textoRelativo("datos/history.json"),
    ...PY_MODULOS.map((m) => textoRelativo(`py/vargento/${m}`)),
  ]);
  const py = await loadPyodide({ indexURL: PYODIDE_URL });
  py.FS.mkdirTree(`${PY_DIR}/vargento`);
  PY_MODULOS.forEach((m, i) => py.FS.writeFile(`${PY_DIR}/vargento/${m}`, fuentes[i]));
  py.runPython(`import sys\nsys.path.insert(0, ${JSON.stringify(PY_DIR)})`);
  py.globals.set("_historia_json", historia);
  py.globals.set("_arbitros_json", JSON.stringify(estatico.referees));
  py.runPython(PY_PUENTE);
  estatico.pyodide = py;
}

async function apiEstatica(path, options) {
  if (path === "/api/play-types") return estatico.playTypes;
  if (path === "/api/referees") return estatico.referees;
  if (path === "/api/predict") {
    const py = estatico.pyodide;
    if (!py) throw new Error("El modelo todavía se está cargando");
    py.globals.set("_cuerpo", options.body);
    const r = JSON.parse(py.runPython("_predecir(_cuerpo)"));
    if (r.error) throw new Error(r.error);
    return r.ok;
  }
  throw new Error(`Ruta no disponible en la demo: ${path}`);
}

const consultar = (path, options) => (ESTATICO ? apiEstatica(path, options) : api(path, options));

function segmented(options, current, onChange) {
  const wrap = document.createElement("div");
  wrap.className = "segmented";
  wrap.setAttribute("role", "radiogroup");
  for (const [value, label] of options) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = label;
    b.setAttribute("role", "radio");
    b.setAttribute("aria-checked", String(value === current));
    b.addEventListener("click", () => {
      wrap.querySelectorAll("button").forEach((x) => x.setAttribute("aria-checked", "false"));
      b.setAttribute("aria-checked", "true");
      onChange(value);
    });
    wrap.appendChild(b);
  }
  return wrap;
}

function renderTypes() {
  const box = $("play-types");
  box.replaceWith(
    Object.assign(
      segmented(state.types.map((t) => [t.key, t.label]), state.type.key, (key) => selectType(key)),
      { id: "play-types" },
    ),
  );
}

function selectType(key) {
  state.type = state.types.find((t) => t.key === key);
  state.values = {};
  const box = $("features");
  box.innerHTML = "";
  for (const f of state.type.features) {
    if (f.kind === "bool") {
      state.values[f.key] = false;
      const row = document.createElement("div");
      row.className = "toggle";
      row.innerHTML = `<span></span>`;
      row.firstChild.textContent = f.label;
      row.title = f.help || "";
      row.appendChild(segmented([[true, "Sí"], [false, "No"]], false, (v) => (state.values[f.key] = v)));
      box.appendChild(row);
    } else if (f.kind === "scale") {
      state.values[f.key] = f.min;
      const row = document.createElement("div");
      row.className = "range-row";
      row.innerHTML = `<span></span>`;
      row.firstChild.textContent = f.label;
      const opts = f.options.map((label, i) => [f.min + i, label]);
      row.appendChild(segmented(opts, f.min, (v) => (state.values[f.key] = v)));
      box.appendChild(row);
    } else {
      state.values[f.key] = 0;
      const row = document.createElement("label");
      row.className = "range-row";
      row.innerHTML = `<span></span><input type="number" step="0.1">`;
      row.firstChild.textContent = f.label;
      const input = row.querySelector("input");
      Object.assign(input, { min: f.min, max: f.max, value: 0, required: true });
      input.addEventListener("input", () => (state.values[f.key] = Number(input.value)));
      box.appendChild(row);
    }
  }
}

function renderPlays(listId, plays, emptyText) {
  const ul = $(listId);
  ul.innerHTML = "";
  if (!plays.length) {
    const li = document.createElement("li");
    li.innerHTML = `<span class="none"></span>`;
    li.firstChild.textContent = emptyText;
    ul.appendChild(li);
    return;
  }
  for (const p of plays) {
    const li = document.createElement("li");
    li.innerHTML = `<span class="tag ${p.called ? "yes" : "no"}">${p.called ? "COBRÓ" : "NO COBRÓ"}</span>
      <div><div class="desc"></div><div class="meta"></div></div>
      <span class="sim">${pct(p.similarity)}</span>`;
    li.querySelector(".desc").textContent = p.description;
    li.querySelector(".meta").textContent = `${p.referee} · ${p.match} · ${p.date}`;
    li.querySelector(".sim").title = "Similitud con la jugada actual";
    ul.appendChild(li);
  }
}

function renderResult(r) {
  $("empty").hidden = true;
  $("result").hidden = false;
  $("question").textContent = r.question + (r.referee ? ` — ${r.referee.name}` : "");
  const v = $("verdict");
  v.textContent = r.recommendation;
  v.className = "verdict " + { COBRAR: "yes", "NO COBRAR": "no", DUDOSA: "doubt" }[r.recommendation];
  $("bar-yes").style.width = pct(r.probability_call);
  $("bar-no").style.width = pct(r.probability_no_call);
  $("p-yes").textContent = pct(r.probability_call);
  $("p-no").textContent = pct(r.probability_no_call);
  $("confidence").textContent = `Confianza ${r.confidence} · basada en ${r.components.similar_plays_effective_n} jugadas equivalentes`;

  const c = r.components;
  $("s-general").textContent = pct(c.similar_plays_probability);
  $("s-general-n").textContent = `n efectivo ${c.similar_plays_effective_n}`;
  $("s-ref").textContent = c.referee_similar_probability == null ? "—" : pct(c.referee_similar_probability);
  $("s-ref-n").textContent =
    c.peers_similar_probability == null
      ? "sin jugadas comparables"
      : `colegas: ${pct(c.peers_similar_probability)} · n ${c.referee_similar_effective_n}`;
  const adj = Math.round(c.referee_adjustment * 100);
  $("s-adj").textContent = `${adj > 0 ? "+" : ""}${adj} pts`;
  $("s-adj-d").textContent = adj > 1 ? "más propenso a cobrar" : adj < -1 ? "menos propenso a cobrar" : "sin sesgo relevante";
  $("s-base").textContent = pct(c.base_rate);

  renderPlays("ref-plays", r.referee_similar_plays, "Este árbitro no tiene jugadas parecidas registradas.");
  renderPlays("similar-plays", r.similar_plays, "No hay jugadas parecidas en el histórico.");
}

async function init() {
  const fin = ESTATICO ? await iniciarModoEstatico() : null;
  const [types, refs] = await Promise.all([consultar("/api/play-types"), consultar("/api/referees")]);
  state.types = types;
  state.type = types[0];
  const sel = $("referee");
  sel.innerHTML = `<option value="">(sin árbitro — sólo jugadas similares)</option>`;
  for (const r of refs) {
    const o = document.createElement("option");
    o.value = r.id;
    o.textContent = `${r.name}${r.country ? ` (${r.country})` : ""} · ${r.plays} jugadas`;
    sel.appendChild(o);
  }
  renderTypes();
  selectType(state.type.key);

  $("play-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = e.submitter || document.querySelector(".primary");
    btn.disabled = true;
    try {
      const refereeId = $("referee").value;
      const r = await consultar("/api/predict", {
        method: "POST",
        body: JSON.stringify({
          play_type: state.type.key,
          referee_id: refereeId ? Number(refereeId) : null,
          features: state.values,
        }),
      });
      renderResult(r);
    } catch (err) {
      alert(err.message);
    } finally {
      btn.disabled = false;
    }
  });

  if (fin) await fin();
}

// Muestra el aviso y "Cargando modelo…", lee los JSON y devuelve la función que
// termina de cargar Pyodide y restaura el texto y el botón.
async function iniciarModoEstatico() {
  $("aviso-demo").hidden = false;
  const empty = $("empty");
  const textoOriginal = empty.textContent;
  const btn = document.querySelector(".primary");
  empty.textContent = "Cargando modelo…";
  btn.disabled = true;
  await cargarDatosEstaticos();
  return async () => {
    await cargarPyodide();
    empty.textContent = textoOriginal;
    btn.disabled = false;
  };
}

init().catch((err) => {
  $("empty").textContent = `No se pudo cargar la app: ${err.message}`;
});
