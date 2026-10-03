/* Jarvis panel - Settings' people: the residents and their voices, guests' codes, Jarvis's own voice.
   The backend is app/people.py through /api/people. A resident's voice is read into the phone
   app's microphone (JarvisApp, like the chat) phrase by phrase. Plain ES2017. */
(function () {
  "use strict";
  const { api, toast, icon, esc } = window.JV;
  const app = window.JarvisApp && window.JarvisApp.startListening ? window.JarvisApp : null;

  function section(id) {
    const node = document.createElement("section");
    node.className = "screen";
    node.id = id;
    document.getElementById("app").insertBefore(node, document.getElementById("nav"));
    return node;
  }
  function paint(node, html) {
    if (node._html === html) return;
    node._html = html;
    node.innerHTML = html;
  }
  const head = (title, words) => `<div class="au-head"><button class="sub-back" data-back>${icon("back", 20)}Настройки</button>
    <h1>${title}</h1><span>${words}</span></div>`;
  const sec = (label, inner) => `<section style="display:flex;flex-direction:column;gap:8px"><h2 class="sec-label">${label}</h2>${inner}</section>`;
  const samples = (n) => {
    const t = n % 10, h = n % 100;
    return n + " " + (t === 1 && h !== 11 ? "фрагмент" : t >= 2 && t <= 4 && (h < 12 || h > 14) ? "фрагмента" : "фрагментов");
  };
  const WEEKDAYS = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"];
  function until(iso) {
    const at = new Date(iso);
    if (isNaN(at)) return "";
    const now = new Date();
    const time = (at.getHours() < 10 ? "0" : "") + at.getHours() + ":" + (at.getMinutes() < 10 ? "0" : "") + at.getMinutes();
    const days = Math.round((new Date(at.getFullYear(), at.getMonth(), at.getDate()) - new Date(now.getFullYear(), now.getMonth(), now.getDate())) / 86400000);
    return "до " + (days === 0 ? "сегодня " : days === 1 ? "завтра " : WEEKDAYS[at.getDay()] + " " + at.getDate() + ", ") + time;
  }
  const HOURS = { 4: "4 часа", 24: "Сутки", 72: "3 дня", 168: "Неделя" };

  const pp = { data: null, loading: false };

  async function load(after) {
    if (pp.loading) return;
    pp.loading = true;
    try { pp.data = await api("GET", "/api/people"); } catch (e) { if (e.message !== "locked") toast(e.message, true); }
    pp.loading = false;
    if (after) after();
  }

  async function send(method, path, body) {
    try {
      const r = await api(method, path, body);
      if (r.error) { toast(r.error, true); return null; }
      pp.data = r;
      return r;
    } catch (e) { if (e.message !== "locked") toast(e.message, true); return null; }
  }

  // ---------------------------------------------------------------- residents and their voices

  const rs = { node: null, rec: null, sure: "" };  // sure: the resident whose × was tapped once  // rec: {name, line, mode: idle/listening/sending, added}

  function drawResidents() {
    let html = head("Жильцы и голоса", "Джарвис узнаёт жильцов по голосу: кто говорит — тому и отвечает, с его привычками.");
    const d = pp.data;
    if (!d) { paint(rs.node, html + `<p class="scr-empty">Загружаю…</p>`); return; }
    html += sec("Жильцы", `<div class="au-box">` + (d.residents.length ? d.residents.map((r) =>
      `<div class="au-row"><span class="pp-ava">${esc(r.name.slice(0, 1).toUpperCase())}</span><span class="words"><b>${esc(r.name)}</b>
        <span class="${r.samples ? "" : "warn"}">${r.samples ? "Голос записан · " + samples(r.samples) : "Голос не записан"}</span></span>
        ${rs.sure === r.name ? `<button class="pp-btn bad jv-press" data-forget="${esc(r.name)}">Удалить?</button>`
          : `<button class="pp-btn jv-press" data-record="${esc(r.name)}">${icon("mic", 16)}${r.samples ? "Дописать" : "Записать"}</button>`}
        <button class="pp-x jv-press" data-sure="${esc(r.name)}" aria-label="${rs.sure === r.name ? "Не удалять" : "Удалить " + esc(r.name)}">${icon(rs.sure === r.name ? "back" : "close", 18)}</button></div>`).join("")
      : `<div class="au-row"><span class="words"><span>Пока никого — добавь первого жильца ниже</span></span></div>`) + `</div>`);
    html += sec("Новый жилец", `<form class="pp-add" data-add-resident><label class="se-field"><span>Имя</span>
      <input id="ppName" placeholder="Например, Эля" maxlength="40" autocomplete="off"></label>
      <button class="pp-main jv-press" type="submit">${icon("plus", 18)}Добавить</button></form>`);
    html += `<p class="pp-note">${app ? "Голос записывается прямо в телефон: три короткие фразы, прочитай каждую своим обычным голосом."
      : "Записать голос можно в приложении Jarvis на телефоне или в окне Джарвиса на компьютере."} Записи остаются только на домашнем компьютере.</p>`;
    paint(rs.node, html);
    paint(rs.sheet, drawRecorder());  // over the whole canvas, not inside the scrolling screen
  }

  function drawRecorder() {
    const r = rs.rec;
    if (!r) return "";
    const lines = pp.data.read_lines;
    const done = r.line >= lines.length;
    const phrase = done ? "" : lines[r.line].replace("{name}", r.name);
    const hint = done ? "Готово — голос записан, теперь Джарвис узнаёт, что говорит " + esc(r.name)
      : { listening: "Читай… нажми ещё раз, когда закончишь", sending: "Запоминаю…" }[r.mode] || "Нажми на микрофон и прочитай фразу";
    return `<div class="pp-rec"><button class="scrim" data-rec-close aria-label="Закрыть"></button><div class="pp-box" role="dialog" aria-label="Запись голоса">
      <div class="top"><b>Голос · ${esc(r.name)}</b><button data-rec-close aria-label="Закрыть">${icon("close", 20)}</button></div>
      <div class="dots">${lines.map((_, i) => `<i class="${i < r.line ? "done" : i === r.line ? "now" : ""}"></i>`).join("")}</div>
      ${done ? `<div class="ok">${icon("check", 30)}</div>` : `<p class="phrase">«${esc(phrase)}»</p>`}
      <span class="hint">${hint}</span>
      ${done ? `<button class="pp-main jv-press" data-rec-close>Закрыть</button>`
        : `<button class="pp-mic jv-press${r.mode === "listening" ? " on" : ""}${r.mode === "sending" ? " busy" : ""}" data-rec-mic aria-label="Запись">${icon("mic", 30)}</button>`}
    </div></div>`;
  }

  // the phone's microphone answers through window.onListening/onVoice/onVoiceError - the chat's own,
  // borrowed while recording and given back after
  let borrowed = null;
  function borrowMic() {
    if (borrowed) return;
    borrowed = { l: window.onListening, v: window.onVoice, e: window.onVoiceError };
    window.onListening = () => { if (rs.rec) { rs.rec.mode = "listening"; drawResidents(); } };
    window.onVoice = (b64) => learn(b64);
    window.onVoiceError = (msg) => { if (rs.rec) { rs.rec.mode = "idle"; drawResidents(); } toast(msg, true); };
  }
  function giveMicBack() {
    if (!borrowed) return;
    if (rs.rec && rs.rec.mode === "listening" && app.cancelListening) app.cancelListening();
    window.onListening = borrowed.l; window.onVoice = borrowed.v; window.onVoiceError = borrowed.e;
    borrowed = null;
  }

  async function learn(b64) {
    const r = rs.rec;
    if (!r) return;
    r.mode = "sending";
    drawResidents();
    const answer = await send("POST", "/api/residents/voice", { name: r.name, audio: b64 });
    if (rs.rec !== r) return;
    r.mode = "idle";
    if (answer) r.line += 1;
    if (r.line >= pp.data.read_lines.length) giveMicBack();
    drawResidents();
  }

  function closeRecorder() {
    giveMicBack();
    rs.rec = null;
    drawResidents();
  }

  rs.node = section("screen-people");
  rs.sheet = document.createElement("div");
  document.getElementById("app").appendChild(rs.sheet);
  rs.sheet.addEventListener("click", (e) => {
    const t = e.target;
    if (t.closest("[data-rec-close]")) { closeRecorder(); return; }
    if (t.closest("[data-rec-mic]")) {
      const r = rs.rec;
      if (r.mode === "idle") app.startListening();  // -> onListening
      else if (r.mode === "listening") { r.mode = "sending"; drawResidents(); app.stopListening(); }  // -> onVoice
    }
  });
  rs.node.addEventListener("click", (e) => {
    const t = e.target;
    if (t.closest("[data-back]")) { window.JV.show("sliders"); return; }
    const sure = t.closest("[data-sure]");
    if (sure) { rs.sure = rs.sure === sure.dataset.sure ? "" : sure.dataset.sure; drawResidents(); return; }
    const forget = t.closest("[data-forget]");
    if (forget) {
      const name = forget.dataset.forget;
      rs.sure = "";
      send("DELETE", "/api/residents/" + encodeURIComponent(name)).then((r) => { if (r) toast(name + " больше не жилец"); drawResidents(); });
      return;
    }
    const rec = t.closest("[data-record]");
    if (rec) {
      if (!app) { toast("Голос записывается в приложении Jarvis на телефоне или в окне Джарвиса на компьютере"); return; }
      borrowMic();
      rs.rec = { name: rec.dataset.record, line: 0, mode: "idle" };
      drawResidents();
    }
  });
  rs.node.addEventListener("submit", async (e) => {
    e.preventDefault();
    const input = rs.node.querySelector("#ppName");
    const name = input.value.trim();
    if (!name) { toast("Напиши имя", true); return; }
    if (await send("POST", "/api/residents", { name })) {
      toast(name + " — теперь жилец");
      drawResidents();
    }
  });
  window.JV.add("people", { tab: "sliders", back: "sliders", open: () => { rs.sure = ""; drawResidents(); load(drawResidents); },
    leave: () => { giveMicBack(); rs.rec = null; paint(rs.sheet, ""); } });

  // ---------------------------------------------------------------- guests: a code that opens the panel for a while

  const gs = { node: null, hours: 24, fresh: "" };

  function drawGuests() {
    let html = head("Гости и временный доступ", "Гость входит в пульт по коду вместо PIN: свет, сценарии, охрана — да, настройки дома — нет. Код перестаёт работать сам.");
    const d = pp.data;
    if (!d) { paint(gs.node, html + `<p class="scr-empty">Загружаю…</p>`); return; }
    html += sec("Действующие коды", `<div class="au-box">` + (d.guests.length ? d.guests.map((g) =>
      `<div class="au-row${g.code === gs.fresh ? " fresh" : ""}"><span class="pp-ava guest">${icon("key", 17)}</span><span class="words"><b>${esc(g.name)}</b><span>${esc(until(g.until))}</span></span>
        <span class="pp-code">${esc(g.code.slice(0, 3) + " " + g.code.slice(3))}</span>
        <button class="pp-x jv-press" data-revoke="${esc(g.code)}" aria-label="Отозвать код ${esc(g.name)}">${icon("close", 18)}</button></div>`).join("")
      : `<div class="au-row"><span class="words"><span>Кодов нет — гости в пульт не входят</span></span></div>`) + `</div>`);
    html += sec("Новый код", `<form class="pp-add" data-add-guest><label class="se-field"><span>Кто придёт</span>
      <input id="ppGuest" placeholder="Например, Бабушка" maxlength="40" autocomplete="off"></label>
      <div class="chips pp-hours" role="radiogroup" aria-label="На сколько">${(d.guest_hours || [4, 24, 72, 168]).map((h) =>
        `<button type="button" role="radio" aria-checked="${gs.hours === h}" class="${gs.hours === h ? "on" : ""}" data-hours="${h}">${HOURS[h] || h + " ч"}</button>`).join("")}</div>
      <button class="pp-main jv-press" type="submit">${icon("key", 18)}Создать код</button></form>`);
    html += `<p class="pp-note">Гость вводит код на экране входа пульта или в приложении Jarvis вместо PIN.</p>`;
    paint(gs.node, html);
  }

  gs.node = section("screen-guests");
  gs.node.addEventListener("click", async (e) => {
    const t = e.target;
    if (t.closest("[data-back]")) { window.JV.show("sliders"); return; }
    const h = t.closest("[data-hours]");
    if (h) {
      gs.hours = parseInt(h.dataset.hours, 10);
      const name = gs.node.querySelector("#ppGuest").value;  // keep what's typed through the redraw
      drawGuests();
      gs.node.querySelector("#ppGuest").value = name;
      return;
    }
    const x = t.closest("[data-revoke]");
    if (x && await send("DELETE", "/api/guests/" + encodeURIComponent(x.dataset.revoke))) { toast("Код отозван"); drawGuests(); }
  });
  gs.node.addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = gs.node.querySelector("#ppGuest").value.trim();
    if (!name) { toast("Напиши, кто придёт", true); return; }
    const r = await send("POST", "/api/guests", { name, hours: gs.hours });
    if (r) {
      gs.fresh = r.guest.code;
      toast("Код создан: " + r.guest.code);
      drawGuests();
    }
  });
  window.JV.add("guests", { tab: "sliders", back: "sliders", open: () => { gs.fresh = ""; drawGuests(); load(drawGuests); } });

  // ---------------------------------------------------------------- Jarvis's voice

  const vs = { node: null };

  function drawVoice() {
    let html = head("Голос ассистента", "Отвечает ли Джарвис вслух. Меняется сразу и сохраняется на компьютере.");
    const d = pp.data;
    if (!d) { paint(vs.node, html + `<p class="scr-empty">Загружаю…</p>`); return; }
    const v = d.voice;
    html += sec("Ответы", `<div class="au-box"><div class="au-row"><span class="words"><b>Отвечать голосом</b>
        <span>${v.enabled ? "Джарвис говорит ответы вслух на компьютере" : "Джарвис отвечает только текстом"}</span></span>
        <button class="switch${v.enabled ? " on" : ""}" role="switch" aria-checked="${v.enabled}" aria-label="Отвечать голосом" data-voice><span class="trk"><span class="knob"></span></span></button></div>
      <div class="au-row"><span class="words"><b>Голос</b><span>${esc(v.provider || "—")}${v.profile ? " · " + esc(v.profile) : ""}</span></span></div></div>`);
    html += `<p class="pp-note">Сам голос (какой движок и чей тембр) выбирается в .env на компьютере. В чате пульта Джарвис отвечает текстом.</p>`;
    paint(vs.node, html);
  }

  vs.node = section("screen-voice");
  vs.node.addEventListener("click", async (e) => {
    if (e.target.closest("[data-back]")) { window.JV.show("sliders"); return; }
    if (e.target.closest("[data-voice]") && pp.data) {
      const on = !pp.data.voice.enabled;
      if (await send("POST", "/api/voice-settings", { enabled: on })) { toast(on ? "Джарвис отвечает голосом" : "Голос выключен"); drawVoice(); }
    }
  });
  window.JV.add("voice", { tab: "sliders", back: "sliders", open: () => { drawVoice(); load(drawVoice); } });

  /** For the Settings rows' second lines. */
  window.JV.people = () => pp.data;
  window.JV.loadPeople = load;
})();
