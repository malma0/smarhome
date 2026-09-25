"use strict";

// Jarvis desktop window. Python (jarvis_gui.py) pushes events into
// window.jarvis.event({...}); user actions go back through
// window.pywebview.api.*. Open index.html?demo in any browser to see a
// scripted run without Python.

const $ = (id) => document.getElementById(id);

const STATES = {
  sleeping:  { label: "Спит",     caption: "Скажи «Джарвис»", color: [104, 118, 176], amp0: 0.020, ampK: 0.05, speed: 0.35, glow: 0.30 },
  listening: { label: "Слушает",  caption: "Слушаю…",          color: [34, 211, 238],  amp0: 0.030, ampK: 0.30, speed: 1.00, glow: 0.60 },
  thinking:  { label: "Думает",   caption: "Думаю…",           color: [139, 92, 246],  amp0: 0.050, ampK: 0.08, speed: 2.40, glow: 0.55 },
  speaking:  { label: "Говорит",  caption: "Говорит…",         color: [232, 90, 200],  amp0: 0.040, ampK: 0.34, speed: 1.40, glow: 0.80 },
};

let pyState = "sleeping";       // what Python last reported
let micLevel = 0, micAt = 0;    // latest mic loudness 0..1
let envelope = null;            // { levels, frameMs, start } while a reply is audibly playing

// ---------------------------------------------------------------- orb

const canvas = $("orbCanvas");
const ctx = canvas.getContext("2d");
let level = 0;
let color = STATES.sleeping.color.slice();
let phase = 0, lastNow = performance.now();

function resizeCanvas() {
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = Math.round(rect.width * dpr);
  canvas.height = Math.round(rect.height * dpr);
}
new ResizeObserver(resizeCanvas).observe(canvas);

function visualState(now) {
  if (envelope) {
    if (now - envelope.start < envelope.levels.length * envelope.frameMs) return "speaking";
    envelope = null;
  }
  return pyState;
}

function targetLevel(vis, now, t) {
  if (vis === "speaking") {
    if (envelope) {
      const i = Math.floor((now - envelope.start) / envelope.frameMs);
      return envelope.levels[Math.min(i, envelope.levels.length - 1)] || 0;
    }
    // voice without a loudness envelope (e.g. the Windows fallback): generic pulse
    return 0.35 + 0.3 * Math.abs(Math.sin(t * 7.3) * Math.sin(t * 2.9));
  }
  if (vis === "listening") return now - micAt < 300 ? micLevel : 0;
  if (vis === "thinking") return 0.3 + 0.12 * Math.sin(t * 2.2);
  return 0.5 + 0.5 * Math.sin(t * 1.1);  // sleeping: slow breath
}

// Russian plural: 1 фраза, 2 фразы, 5 фраз, 11 фраз, 21 фраза
function plural(n, one, few, many) {
  const m10 = n % 10, m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
  return many;
}

function mix(a, b, k) { return a + (b - a) * k; }
function rgba(c, a) { return `rgba(${c[0] | 0},${c[1] | 0},${c[2] | 0},${a})`; }

function drawOrb(now) {
  const dt = Math.min(0.05, (now - lastNow) / 1000);
  lastNow = now;
  const t = now / 1000;
  const vis = visualState(now);
  const cfg = STATES[vis];

  level = mix(level, targetLevel(vis, now, t), vis === "speaking" ? 0.35 : 0.15);
  color = color.map((v, i) => mix(v, cfg.color[i], 0.06));
  phase += dt * cfg.speed;

  const w = canvas.width, h = canvas.height;
  const cx = w / 2, cy = h / 2;
  const base = Math.min(w, h) * 0.27 * (1 + level * (vis === "sleeping" ? 0.03 : 0.10));
  const amp = cfg.amp0 + level * cfg.ampK;

  ctx.clearRect(0, 0, w, h);

  // outer glow
  const glow = ctx.createRadialGradient(cx, cy, base * 0.3, cx, cy, base * 1.9);
  glow.addColorStop(0, rgba(color, cfg.glow * (0.45 + level * 0.4)));
  glow.addColorStop(1, rgba(color, 0));
  ctx.fillStyle = glow;
  ctx.fillRect(0, 0, w, h);

  // three soft blobs, each wobbling on its own phase
  ctx.globalCompositeOperation = "lighter";
  for (let k = 0; k < 3; k++) {
    const hueShift = [[1, 1, 1], [0.75, 0.9, 1.2], [1.2, 0.8, 0.9]][k];
    const c = color.map((v, i) => Math.min(255, v * hueShift[i]));
    ctx.beginPath();
    const steps = 96;
    for (let i = 0; i <= steps; i++) {
      const a = (i / steps) * Math.PI * 2;
      const wobble =
        0.55 * Math.sin(3 * a + phase * 1.0 + k * 1.7) +
        0.30 * Math.sin(5 * a - phase * 1.3 + k * 0.9) +
        0.15 * Math.sin(7 * a + phase * 0.7 + k * 2.3);
      const r = base * (1 - 0.06 * k) * (1 + amp * wobble);
      const x = cx + Math.cos(a) * r, y = cy + Math.sin(a) * r;
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    }
    const fill = ctx.createRadialGradient(cx - base * 0.3, cy - base * 0.35, base * 0.1, cx, cy, base * 1.1);
    fill.addColorStop(0, rgba(c.map((v) => mix(v, 255, 0.45)), 0.55));
    fill.addColorStop(0.6, rgba(c, 0.32));
    fill.addColorStop(1, rgba(c, 0.05));
    ctx.fillStyle = fill;
    ctx.fill();
  }
  ctx.globalCompositeOperation = "source-over";

  // glossy highlight
  const hl = ctx.createRadialGradient(cx - base * 0.35, cy - base * 0.4, 0, cx - base * 0.35, cy - base * 0.4, base * 0.6);
  hl.addColorStop(0, "rgba(255,255,255,0.28)");
  hl.addColorStop(1, "rgba(255,255,255,0)");
  ctx.fillStyle = hl;
  ctx.beginPath();
  ctx.arc(cx, cy, base * 0.95, 0, Math.PI * 2);
  ctx.fill();

  // keep status/caption colour in sync with what's on screen
  document.body.style.setProperty("--state-color", rgba(cfg.color, 1));
  if (vis !== shownVisual) showVisual(vis);

  requestAnimationFrame(drawOrb);
}

// ---------------------------------------------------------------- captions

let pyDetail = "";
let shownVisual = null;

function showVisual(vis) {
  shownVisual = vis;
  $("statusText").textContent = STATES[vis].label;
  $("caption").textContent = vis === pyState && pyDetail ? pyDetail : STATES[vis].caption;
}

// ---------------------------------------------------------------- chat

const chat = $("chat");

function scrollDown() { chat.scrollTop = chat.scrollHeight; }

function hideEmpty() {
  const empty = $("empty");
  if (empty) empty.remove();
}

// A voice phrase can always be corrected, right from the moment it shows up
// - including one Jarvis couldn't make out at all (empty text).
function addUser(text, voice, utteranceId, speaker, speakerSure) {
  hideEmpty();
  const msg = document.createElement("div");
  msg.className = "msg user" + (voice && !text ? " unheard" : "");
  msg.innerHTML = `<div class="bubble"></div><div class="meta"><span class="how"></span></div>`;
  msg.querySelector(".bubble").textContent = text || "Не разобрал, что ты сказал";
  msg.querySelector(".how").textContent = voice ? "голосом" : "текстом";
  chat.appendChild(msg);
  if (voice) addSpeakerLabel(msg, utteranceId, speaker, speakerSure !== false);
  if (voice) {
    const btn = document.createElement("button");
    btn.className = "edit-btn";
    btn.textContent = text ? "✎ Не так понял?" : "✎ Вписать, что я сказал";
    btn.title = "Впиши, что ты сказал на самом деле - Jarvis ответит заново, а исправление пойдёт в данные для обучения.";
    btn.onclick = () => startEdit(msg, utteranceId || null);
    msg.querySelector(".meta").appendChild(btn);
  }
  scrollDown();
}

// Who said it: "Матвей", or "Матвей?" when Jarvis only guessed. Clicking
// fixes it - the label goes into the voice training data, so a wrong one
// would teach the wrong thing.
function addSpeakerLabel(msg, utteranceId, speaker, sure) {
  const who = document.createElement(utteranceId ? "button" : "span");
  who.className = "who" + (speaker && sure ? "" : " unsure");
  who.textContent = speaker ? (sure ? speaker : `${speaker}?`) : "кто говорил?";
  if (utteranceId) {
    who.type = "button";
    who.title = "Кто это сказал? Нажми, чтобы исправить - это пойдёт в данные для обучения голосов.";
    who.onclick = () => pickSpeaker(msg, utteranceId, who);
  }
  msg.querySelector(".meta").appendChild(who);
}

function pickSpeaker(msg, utteranceId, who) {
  if (msg.querySelector(".pick-speaker")) return;
  const box = document.createElement("div");
  box.className = "pick-speaker";
  const names = voiceProfiles.map((p) => p.name);
  if (!names.length) {
    box.innerHTML = `<span class="pick-hint">Сначала запиши голоса - нажми на имя вверху.</span>`;
  }
  for (const name of names) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = `Это ${name}`;
    btn.onclick = () => {
      call("set_speaker", utteranceId, name);
      who.textContent = name;
      who.classList.remove("unsure");
      box.remove();
    };
    box.appendChild(btn);
  }
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "cancel";
  cancel.textContent = "Отмена";
  cancel.onclick = () => box.remove();
  box.appendChild(cancel);
  msg.appendChild(box);
  scrollDown();
}

function startEdit(msg, utteranceId) {
  if (msg.querySelector(".edit-box")) return;
  const bubble = msg.querySelector(".bubble");
  const box = document.createElement("form");
  box.className = "edit-box";
  box.innerHTML =
    `<input maxlength="2000" placeholder="Что ты сказал на самом деле?">` +
    `<div class="edit-actions">` +
    `<button class="save" type="submit">Спросить заново</button>` +
    (utteranceId ? `<button class="only" type="button">Только сохранить</button>` : "") +
    `<button class="cancel" type="button">Отмена</button></div>`;
  const input = box.querySelector("input");
  input.value = msg.classList.contains("unheard") ? "" : bubble.textContent;
  const finish = (ask) => {
    const text = input.value.trim();
    if (!text) { input.focus(); return; }
    call("correct", utteranceId, text, ask);
    bubble.textContent = text;
    msg.classList.remove("unheard");
    msg.querySelector(".edit-btn").textContent = "✎ Не так понял?";
    const meta = msg.querySelector(".meta");
    if (!meta.querySelector(".fixed")) {
      const tag = document.createElement("span");
      tag.className = "fixed";
      tag.textContent = ask ? "исправлено, спросил заново" : "исправлено";
      meta.insertBefore(tag, meta.querySelector(".edit-btn"));
    }
    box.remove();
  };
  box.onsubmit = (e) => { e.preventDefault(); finish(true); };
  const only = box.querySelector(".only");
  if (only) only.onclick = () => finish(false);
  box.querySelector(".cancel").onclick = () => box.remove();
  msg.appendChild(box);
  input.focus();
  input.select();
  scrollDown();
}

function addJarvis(text, actions) {
  hideEmpty();
  const msg = document.createElement("div");
  msg.className = "msg jarvis";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  msg.appendChild(bubble);
  if (actions && actions.length) {
    const list = document.createElement("div");
    list.className = "actions";
    for (const a of actions) {
      const chip = document.createElement("span");
      chip.className = "action" + (a.ok ? "" : " failed");
      chip.textContent = a.summary;
      list.appendChild(chip);
    }
    msg.appendChild(list);
  }
  chat.appendChild(msg);
  scrollDown();
}

function addNote(text) {
  hideEmpty();
  const note = document.createElement("div");
  note.className = "note";
  note.textContent = text;
  chat.appendChild(note);
  scrollDown();
}

let toastTimer = null;
function toast(text) {
  const el = $("toast");
  el.textContent = text;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 3500);
}

// ---------------------------------------------------------------- name modal

function askName(prompt, known) {
  const modal = $("nameModal");
  const input = $("nameInput");
  const list = $("nameKnown");
  input.value = "";
  list.innerHTML = "";
  known = known || [];
  $("nameText").textContent = known.length
    ? "Если это кто-то из них - нажми на имя: запись добавится к голосу, и дальше я буду узнавать увереннее. Или впиши новое имя."
    : "Как тебя зовут? Запомню голос и дальше буду узнавать сам.";
  input.placeholder = known.length ? "Новое имя" : "Имя";
  modal.hidden = false;
  input.focus();
  const done = (name) => {
    modal.hidden = true;
    $("nameForm").onsubmit = null;
    $("nameSkip").onclick = null;
    call("answer_name", name);
  };
  for (const name of known) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "known-name";
    btn.textContent = `Это ${name}`;
    btn.onclick = () => done(name);
    list.appendChild(btn);
  }
  $("nameForm").onsubmit = (e) => { e.preventDefault(); done(input.value.trim()); };
  $("nameSkip").onclick = () => done("");
}

// ---------------------------------------------------------------- voices in the house

let voiceProfiles = [];
let enrollingName = null;

const READ_LINES = (name) => [
  `Привет, Джарвис! Меня зовут ${name}, запомни мой голос.`,
  "Сегодня на улице тепло, и вечером мы пойдём гулять в парк.",
  "Включи, пожалуйста, свет на кухне и поставь чайник.",
];

function renderVoices() {
  const list = $("voiceList");
  list.innerHTML = "";
  if (!voiceProfiles.length) {
    list.innerHTML = `<p class="voice-empty">Пока ни одного голоса.</p>`;
    return;
  }
  for (const p of voiceProfiles) {
    const row = document.createElement("div");
    row.className = "voice-row";
    row.innerHTML = `<div><div class="voice-name"></div><div class="voice-meta"></div></div><button type="button" class="more">Дозаписать</button>`;
    row.querySelector(".voice-name").textContent = p.name;
    row.querySelector(".voice-meta").textContent = `${p.samples} ${plural(p.samples, "запись", "записи", "записей")} голоса`;
    row.querySelector(".more").onclick = () => startEnroll(p.name);
    list.appendChild(row);
  }
}

function openVoices() {
  renderVoices();
  $("voicesHome").hidden = false;
  $("enrollView").hidden = true;
  $("voicesModal").hidden = false;
}

function closeVoices() {
  if (enrollingName) call("cancel_enroll");
  enrollingName = null;
  $("voicesModal").hidden = true;
}

function startEnroll(name) {
  name = name.trim();
  if (!name) { $("newVoiceName").focus(); return; }
  enrollingName = name;
  $("enrollTitle").textContent = `Запись голоса: ${name}`;
  const lines = $("readLines");
  lines.innerHTML = "";
  for (const text of READ_LINES(name)) {
    const li = document.createElement("li");
    li.textContent = text;
    lines.appendChild(li);
  }
  showEnrollProgress(0, 3);
  $("enrollStatus").textContent = "Слушаю…";
  $("voicesHome").hidden = true;
  $("enrollView").hidden = false;
  $("voicesModal").hidden = false;
  call("start_enroll", name);
}

function showEnrollProgress(collected, needed) {
  const bar = $("enrollProgress");
  bar.innerHTML = "";
  for (let i = 0; i < needed; i++) {
    const dot = document.createElement("span");
    dot.className = "step" + (i < collected ? " done" : "");
    bar.appendChild(dot);
  }
}

function onEnroll(e) {
  showEnrollProgress(e.collected, e.needed);
  const status = $("enrollStatus");
  if (e.status === "progress") status.textContent = `Записано ${e.collected} из ${e.needed} - продолжай`;
  if (e.status === "done") status.textContent = `Готово! Голос «${e.name}» запомнен.`;
  if (e.status === "partial") status.textContent = `Время вышло - сохранил ${e.collected} из ${e.needed}. Можно дозаписать позже.`;
  if (e.status === "failed") status.textContent = "Не услышал речи. Попробуй ещё раз, ближе к микрофону.";
  if (["done", "partial", "failed", "cancelled"].includes(e.status)) {
    enrollingName = null;
    $("enrollCancel").textContent = "Закрыть";
    if (e.status === "done") setTimeout(() => { if (!enrollingName) closeVoices(); }, 1800);
  } else {
    $("enrollCancel").textContent = "Отмена";
  }
}

$("residentChip").onclick = openVoices;
$("voicesClose").onclick = closeVoices;
$("enrollCancel").onclick = closeVoices;
$("newVoiceForm").onsubmit = (e) => {
  e.preventDefault();
  const name = $("newVoiceName").value;
  $("newVoiceName").value = "";
  startEnroll(name);
};

// ---------------------------------------------------------------- events from Python

window.jarvis = {
  event(e) {
    switch (e.type) {
      case "state":
        pyState = STATES[e.state] ? e.state : "sleeping";
        pyDetail = e.detail || "";
        shownVisual = null;  // refresh caption on next frame
        break;
      case "user": addUser(e.text, e.voice, e.id, e.speaker, e.speaker_sure); break;
      case "jarvis": addJarvis(e.text, e.actions); break;
      case "note": addNote(e.text); break;
      case "info": toast(e.text); break;
      case "resident": $("residentName").textContent = e.name; break;
      case "stats": $("datasetText").textContent = `${e.minutes} мин · ${e.utterances} ${plural(e.utterances, "фраза", "фразы", "фраз")}`; break;
      case "mic": micLevel = e.level; micAt = performance.now(); break;
      case "envelope":
        if (e.levels && e.levels.length) envelope = { levels: e.levels, frameMs: e.frame_seconds * 1000, start: performance.now() };
        break;
      case "ask_name": askName(e.prompt, e.known); break;
      case "voices": voiceProfiles = e.profiles || []; if (!$("voicesModal").hidden && !enrollingName) renderVoices(); break;
      case "enroll": onEnroll(e); break;
    }
  },
};

// ---------------------------------------------------------------- user actions

function call(method, ...args) {
  const api = window.pywebview && window.pywebview.api;
  if (api && api[method]) return api[method](...args);
  if (DEMO) demoCall(method, ...args);
}

$("orb").onclick = () => call("wake");

const input = $("input");
const send = $("send");
const syncSend = () => { send.disabled = !input.value.trim(); };
input.addEventListener("input", syncSend);
syncSend();
$("composer").onsubmit = (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  call("send_text", text);
  input.value = "";
  syncSend();
};

// ---------------------------------------------------------------- demo (index.html?demo)

const DEMO = new URLSearchParams(location.search).has("demo");

let demoEnrollTimers = [];

function demoCall(method, ...args) {
  if (method === "wake") runDemo();
  if (method === "start_enroll") {
    const name = args[0];
    const ev = (collected, status) => ({ type: "enroll", name, collected, needed: 3, status });
    demoEnrollTimers = [
      setTimeout(() => jarvis.event(ev(1, "progress")), 1500),
      setTimeout(() => jarvis.event(ev(2, "progress")), 3000),
      setTimeout(() => {
        jarvis.event(ev(3, "done"));
        const known = voiceProfiles.filter((p) => p.name !== name);
        jarvis.event({ type: "voices", profiles: [...known, { name, samples: 3 }] });
      }, 4500),
    ];
  }
  if (method === "set_speaker") toast(`Исправлено: голос - «${args[1]}».`);
  if (method === "cancel_enroll") {
    demoEnrollTimers.forEach(clearTimeout);
    jarvis.event({ type: "enroll", name: "", collected: 0, needed: 3, status: "cancelled" });
  }
  if (method === "send_text") {
    jarvis.event({ type: "user", text: args[0], voice: false });
    later(600, { type: "state", state: "thinking" });
    later(1800, { type: "jarvis", text: "Это демо-режим - настоящий Jarvis ответит, когда окно запущено из Python.", actions: [] });
    later(2000, { type: "state", state: "listening", detail: "Слушаю ещё 10 с - можно без имени" });
  }
  if (method === "correct") {
    const [, text, ask] = args;
    toast("Исправление сохранено в датасет.");
    if (ask) {
      later(300, { type: "state", state: "thinking", detail: "Думаю..." });
      later(1300, { type: "jarvis", text: `(демо) Теперь понял: «${text}».`, actions: [] });
      later(1500, { type: "state", state: "listening", detail: "Слушаю ещё 10 с - можно без имени" });
    }
  }
}

function later(ms, e) { setTimeout(() => jarvis.event(e), ms); }

function runDemo() {
  const steps = [];
  let at = 0;
  const step = (dt, e) => { at += dt; steps.push([at, e]); };
  step(0, { type: "state", state: "listening", detail: "Слушаю (8 с)..." });
  for (let i = 0; i < 26; i++) step(90, { type: "mic", level: 0.15 + 0.8 * Math.abs(Math.sin(i * 0.9) * Math.sin(i * 0.37)) });
  step(200, { type: "state", state: "thinking", detail: "Распознаю..." });
  step(900, { type: "resident", name: "Матвей" });
  step(0, { type: "user", text: "Джарвис, открой блокнот и запиши привет мир", voice: true, id: "demo-1", speaker: "Матвей", speaker_sure: true });
  step(300, { type: "state", state: "thinking", detail: "Думаю..." });
  step(1400, { type: "jarvis", text: "Готово: открыл блокнот, там уже написано «привет мир».",
               actions: [{ ok: true, summary: "Записал hello.txt" }, { ok: true, summary: "Открыл notepad" }] });
  step(0, { type: "stats", minutes: 3.4, utterances: 41 });
  step(0, { type: "state", state: "thinking", detail: "Готовлю голос..." });
  const env = Array.from({ length: 70 }, (_, i) => Math.max(0, Math.sin(i * 0.45) * 0.7 + Math.sin(i * 1.7) * 0.3));
  step(1800, { type: "envelope", levels: env, frame_seconds: 0.05 });
  step(3600, { type: "state", state: "listening", detail: "Слушаю ещё 10 с - можно без имени" });
  step(1500, { type: "state", state: "thinking", detail: "Распознаю..." });
  step(700, { type: "user", text: "", voice: true, id: "demo-2", speaker: "Матвей", speaker_sure: false });  // couldn't make it out
  step(0, { type: "state", state: "listening", detail: "Слушаю ещё 10 с - можно без имени" });
  step(4000, { type: "state", state: "sleeping", detail: "Скажи «Джарвис»" });
  for (const [ms, e] of steps) later(ms, e);
}

if (DEMO) {
  jarvis.event({ type: "voices", profiles: [{ name: "Матвей", samples: 5 }, { name: "Эля", samples: 3 }] });
  jarvis.event({ type: "resident", name: "default" });
  jarvis.event({ type: "stats", minutes: 3.2, utterances: 40 });
  jarvis.event({ type: "state", state: "sleeping", detail: "Скажи «Джарвис»" });
  setTimeout(runDemo, 1200);
}

requestAnimationFrame(drawOrb);
