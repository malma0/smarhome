/* Jarvis panel - making scenarios and schedules (the design's SceneEditor and
   ScheduleEditor). A scenario is saved as a Home Assistant script (POST /api/scenes,
   app/scenes.py), a schedule as an automation (POST /api/schedules). Plain ES2017. */
(function () {
  "use strict";
  const { api, toast, icon, esc } = window.JV;
  const data = () => window.JV.scenesData;

  // ---------------------------------------------------------------- what a step can be

  const TH = {  // thing: [name, icon, accusative]
    light: ["Свет", "light", "свет"], curtain: ["Шторы", "curtain", "шторы"], ac: ["Кондиционер", "ac", "кондиционер"],
    heat: ["Отопление", "heat", "отопление"], vent: ["Вентиляция", "vent", "вентиляцию"], humid: ["Увлажнитель", "humid", "увлажнитель"],
    socket: ["Розетки", "socket", "розетки"], water: ["Кран воды", "water", "воду"], gas: ["Кран газа", "gas", "газ"],
    guard: ["Охрана", "shield", "охрану"], nT: ["Норма", "thermo", "Температура"], nC: ["Норма CO₂", "co2", "CO₂"],
    nH: ["Норма влажности", "humid", "Влажность"],
  };
  const FROM_HA = { light: "light", curtains: "curtain", ac: "ac", heating: "heat", ventilation: "vent", humidifier: "humid",
    socket: "socket", water_valve: "water", gas_valve: "gas", security: "guard" };
  const TO_HA = {};
  Object.keys(FROM_HA).forEach((k) => { TO_HA[FROM_HA[k]] = k; });
  const NORMS = { nT: ["temperature", 16, 28, 0.5, " °C"], nC: ["co2_max", 500, 1200, 50, " ppm"], nH: ["humidity_min", 30, 60, 5, "%"] };
  const NORM_OF = { temperature: "nT", co2_max: "nC", humidity_min: "nH" };
  const DEF = { light: { on: true, v: 60 }, curtain: { mode: "close", v: 50 }, ac: { on: true, v: 22 }, heat: { on: true, v: 22 },
    vent: { on: true }, humid: { on: true }, socket: { on: true }, water: { mode: "close" }, gas: { mode: "close" },
    guard: { on: true }, nT: { v: 22 }, nC: { v: 800 }, nH: { v: 40 } };
  const LOC = { "Кухня": "на кухне", "Спальня": "в спальне", "Зал": "в зале", "Гостиная": "в гостиной", "Кабинет": "в кабинете",
    "Коридор": "в коридоре", "Прихожая": "в прихожей", "Детская": "в детской", "Ванная": "в ванной", "Балкон": "на балконе" };
  const ICONS = [["play", "Воспроизведение"], ["moon", "Луна"], ["sunrise", "Рассвет"], ["home", "Дом"], ["exit", "Выход"], ["light", "Лампа"]];
  const PITCH = 68;  // a step row and its gap
  const fmt = (n) => String(n).replace(".", ",");
  const plural = (n, a, b, c) => { const t = n % 10, h = n % 100; return n + " " + (t === 1 && h !== 11 ? a : t >= 2 && t <= 4 && (h < 12 || h > 14) ? b : c); };
  const roomName = (r) => (r === "all" ? "Весь дом" : r);
  const at = (r) => (r === "all" ? "во всём доме" : LOC[r] || "в комнате «" + r + "»");
  const rooms = () => (window.JV.house().rooms || []).filter((r) => r.name !== "Весь дом");

  /** The things a room has: its devices, as the design's kinds. "all": what any room has, and the guard. */
  function thingsOf(room) {
    const h = window.JV.house();
    if (room === "all") {
      const set = {};
      rooms().forEach((r) => r.devices.forEach((d) => { if (FROM_HA[d.type] && !TO_HA_VALVE[FROM_HA[d.type]]) set[FROM_HA[d.type]] = 1; }));
      if (h.house && h.house.devices.some((d) => d.type === "security")) set.guard = 1;
      return ["light", "curtain", "ac", "heat", "vent", "humid", "socket", "guard"].filter((k) => set[k]);
    }
    const r = rooms().find((x) => x.name === room);
    const has = r ? r.devices.map((d) => FROM_HA[d.type]) : [];
    return ["light", "curtain", "ac", "heat", "vent", "humid", "socket", "water", "gas"].filter((k) => has.indexOf(k) >= 0);  // the design's order
  }
  const TO_HA_VALVE = { water: 1, gas: 1 };
  function normsOf(room) {
    if (room === "all") return ["nT", "nC", "nH"];
    const r = rooms().find((x) => x.name === room);
    return r ? Object.keys(r.norms).map((k) => NORM_OF[k]).filter(Boolean) : [];
  }

  function act(s) {
    const k = s.thing;
    if (k === "light") return !s.on ? "выкл" : s.v >= 100 ? "вкл" : s.v + "%";
    if (k === "curtain") return s.mode === "open" ? "открыть" : s.mode === "close" ? "закрыть" : "на " + s.v + "%";
    if (k === "ac" || k === "heat") return s.on ? "вкл · " + fmt(s.v) + " °C" : "выкл";
    if (k === "water" || k === "gas") return "закрыть";
    if (k === "guard") return s.on ? "поставить" : "снять";
    if (k === "nT") return fmt(s.v) + " °C";
    if (k === "nC") return "до " + s.v + " ppm";
    if (k === "nH") return s.v + "%";
    return s.on ? "вкл" : "выкл";
  }
  const label = (s) => (s.thing === "guard" ? "Охрана — " + act(s) : TH[s.thing][0] + " · " + roomName(s.room) + " — " + act(s));
  function phrase(s) {
    const k = s.thing, inn = at(s.room), acc = TH[k][2];
    if (k === "light") return !s.on ? "выключит свет " + inn : s.v >= 100 ? "включит свет " + inn
      : s.v < 60 ? "приглушит свет " + inn + " до " + s.v + "%" : "включит свет " + inn + " на " + s.v + "%";
    if (k === "curtain") return s.mode === "open" ? "откроет шторы " + inn : s.mode === "close" ? "закроет шторы " + inn : "откроет шторы " + inn + " на " + s.v + "%";
    if (k === "ac" || k === "heat") return s.on ? "включит " + acc + " " + inn + " на " + fmt(s.v) + " °C" : "выключит " + acc + " " + inn;
    if (k === "water" || k === "gas") return "перекроет " + acc + " " + inn;
    if (k === "guard") return s.on ? "поставит дом на охрану" : "снимет охрану";
    if (k === "nT") return "будет держать " + fmt(s.v) + " °C " + inn;
    if (k === "nC") return "будет держать CO₂ до " + s.v + " ppm " + inn;
    if (k === "nH") return "будет держать влажность " + s.v + "% " + inn;
    return (s.on ? "включит " : "выключит ") + acc + " " + inn;
  }

  /** The editor's step -> the server's (app/scenes.py), and back. */
  function toApi(s) {
    const room = s.room === "all" ? "all" : s.room;
    if (NORMS[s.thing]) return { room, norm: NORMS[s.thing][0], value: s.v };
    if (s.thing === "guard") return { room: "all", device: "security", action: s.on ? "on" : "off" };
    if (s.thing === "water" || s.thing === "gas") return { room, device: TO_HA[s.thing], action: "off" };
    if (s.thing === "curtain") {
      if (s.mode === "pos") return { room, device: "curtains", action: s.v > 0 ? "on" : "off", position: s.v };
      return { room, device: "curtains", action: s.mode === "open" ? "on" : "off" };
    }
    const step = { room, device: TO_HA[s.thing], action: s.on ? "on" : "off" };
    if (s.thing === "light" && s.on && s.v < 100) step.brightness_pct = s.v;
    if ((s.thing === "ac" || s.thing === "heat") && s.on) step.temperature = s.v;
    return step;
  }
  function fromApi(a) {
    const room = String(a.room || "").toLowerCase() === "all" ? "all" : a.room;
    if (a.norm) return { room, thing: NORM_OF[a.norm], v: a.value };
    const k = FROM_HA[a.device];
    if (k === "guard") return { room: "all", thing: "guard", on: a.action === "on" };
    if (k === "water" || k === "gas") return { room, thing: k, mode: "close" };
    if (k === "curtain") return a.position != null && a.position > 0 && a.position < 100 ? { room, thing: k, mode: "pos", v: a.position }
      : { room, thing: k, mode: a.action === "on" ? "open" : "close", v: 50 };
    const s = { room, thing: k, on: a.action === "on" };
    if (k === "light") s.v = a.brightness_pct || 100;
    if (k === "ac" || k === "heat") s.v = a.temperature || 22;
    return s;
  }

  // ---------------------------------------------------------------- the scenario editor

  const ed = { node: null, id: null, name: "", icon: "play", phrases: [], phraseEditing: false, steps: [], pk: null,
    drag: null, tested: -1, testing: false, ask: null };

  function section(id) {
    const node = document.createElement("section");
    node.className = "screen";
    node.id = id;
    document.getElementById("app").insertBefore(node, document.getElementById("nav"));
    return node;
  }

  function skeleton() {
    ed.node.innerHTML = `<div class="se-scroll">
      <div class="se-head"><button class="sub-back" data-back>${icon("back", 20)}Сценарии</button><h1 id="seTitle"></h1></div>
      <div style="display:flex;flex-direction:column;gap:14px">
        <label class="se-field"><span>Название</span><input id="seName" placeholder="Например, «Кино»" maxlength="40" autocomplete="off"></label>
        <div class="se-field"><span>Значок</span><div class="se-icons" id="seIcons" role="radiogroup" aria-label="Значок сценария"></div></div></div>
      <section class="se-sec"><div class="cap"><h2>Фразы для голоса</h2><span id="seHint"></span></div><div class="se-phrases" id="sePhrases"></div></section>
      <section class="se-sec"><div class="cap row"><h2>Шаги</h2><span id="seCount"></span></div><div id="seSteps"></div>
        <button class="se-add jv-press" data-add>${icon("plus", 18)}Добавить шаг</button></section>
      <section class="se-summary"><i>${icon("voice", 17)}</i><span><small>Что сделает</small><b id="seSummary" aria-live="polite"></b></span></section>
      <button class="se-delete jv-press" data-delete id="seDelete">${icon("close", 16)}Удалить сценарий</button>
    </div>
    <div class="se-bar"><button class="try jv-press" data-try>${icon("play", 18)}<span id="seTryTxt">Проверить</span></button><button class="save jv-press" data-save>Сохранить</button></div>
    <div id="seOverlay"></div>`;
  }

  function drawIcons() {
    ed.node.querySelector("#seIcons").innerHTML = ICONS.map((ic) =>
      `<button class="jv-press${ed.icon === ic[0] ? " on" : ""}" role="radio" aria-checked="${ed.icon === ic[0]}" aria-label="${ic[1]}" data-icon-pick="${ic[0]}">${icon(ic[0], 20)}</button>`).join("");
  }

  function drawPhrases() {
    ed.node.querySelector("#seHint").textContent = ed.phrases.length ? "Скажи: «Jarvis, " + ed.phrases[0] + "»" : "По этим фразам Jarvis запустит сценарий голосом";
    ed.node.querySelector("#sePhrases").innerHTML = ed.phrases.map((p, i) =>
      `<span class="se-phrase"><span>${icon("mic", 14)}${esc(p)}</span><button aria-label="Удалить фразу «${esc(p)}»" data-phrase-del="${i}">${icon("close", 14)}</button></span>`).join("") +
      (ed.phraseEditing ? `<span class="se-phrase-new"><input id="sePhraseNew" aria-label="Новая фраза" placeholder="включи кино" maxlength="60" autocomplete="off"><button class="jv-press" data-phrase-add>Добавить</button></span>`
        : `<button class="se-dash jv-press" data-phrase-start>${icon("plus", 16)}Фраза</button>`);
    if (ed.phraseEditing) { const input = ed.node.querySelector("#sePhraseNew"); if (input) input.focus(); }
  }

  function drawSteps() {
    const box = ed.node.querySelector("#seSteps");
    ed.node.querySelector("#seCount").textContent = ed.steps.length ? plural(ed.steps.length, "шаг", "шага", "шагов") + " · по порядку" : "";
    if (!ed.steps.length) {
      box.innerHTML = `<div class="se-empty"><i>${icon("scenes", 22)}</i><b>Шагов пока нет</b><span>Выбери комнату, устройство и что с ним сделать. Шаги выполнятся по порядку.</span></div>`;
    } else {
      box.innerHTML = `<div class="se-steps">` + ed.steps.map((s, i) => {
        const ok = ed.tested >= i;
        return `<div class="se-step${ok ? " ok" : ""}" data-row="${i}">
          <button class="grip" aria-label="Переместить шаг ${i + 1}" data-grip="${i}">${icon("grip", 20)}</button>
          <button class="what" data-step-edit="${i}" aria-label="Изменить шаг: ${esc(label(s))}"><i>${icon(TH[s.thing][1], 18)}${ok ? `<em>${icon("check", 11)}</em>` : ""}</i>
            <span><b>${esc(label(s))}</b><small>Шаг ${i + 1}</small></span></button>
          <button class="del jv-press" aria-label="Удалить шаг: ${esc(label(s))}" data-step-del="${i}">${icon("close", 18)}</button></div>`;
      }).join("") + `</div>`;
    }
    drawSummary();
  }

  function drawSummary() {
    const box = ed.node.querySelector("#seSummary");
    if (!ed.steps.length) { box.textContent = "Добавь шаги — здесь появится описание обычными словами."; box.className = "none"; }
    else {
      const parts = ed.steps.map(phrase);
      let shown = parts.slice(0, 4);
      if (parts.length > 4) shown = parts.slice(0, 3).concat(["ещё " + plural(parts.length - 3, "действие", "действия", "действий")]);
      const txt = shown.length > 1 ? shown.slice(0, -1).join(", ") + " и " + shown[shown.length - 1] : shown[0];
      box.textContent = txt.charAt(0).toUpperCase() + txt.slice(1) + ".";
      box.className = "";
    }
    drawBar();
  }

  function drawBar() {
    const valid = ed.name.trim().length > 0 && ed.steps.length > 0;
    const save = ed.node.querySelector("[data-save]");
    save.disabled = !valid;
    save.setAttribute("aria-label", valid ? "Сохранить" : "Сохранить — сначала добавь название и шаг");
    const tryBtn = ed.node.querySelector("[data-try]");
    tryBtn.disabled = !ed.steps.length || ed.testing;
    ed.node.querySelector("#seTryTxt").textContent = ed.testing ? "Выполняю…" : "Проверить";
  }

  function drawOverlay() {
    const o = ed.node.querySelector("#seOverlay");
    if (ed.ask) { o.innerHTML = data().askHtml(ed.ask); return; }
    if (!ed.pk) { o.innerHTML = ""; return; }
    o.innerHTML = `<button class="scrim" data-pk-close aria-label="Закрыть выбор шага"></button>` + pickerHtml();
  }

  function drawAll() { drawIcons(); drawPhrases(); drawSteps(); drawOverlay(); }

  // ---------------------------------------------------------------- the step picker: where, what, what to do

  function pickerHtml() {
    const pk = ed.pk;
    const no = { room: 1, what: 2, action: 3 }[pk.step];
    const crumb = pk.step === "room" ? "" : " · " + roomName(pk.room) + (pk.step === "action" ? " · " + TH[pk.thing][0] : "");
    const title = { room: "Где?", what: "Что?", action: "Что сделать?" }[pk.step];
    let html = `<div class="pk" role="dialog" aria-label="Новый шаг"><span class="grab"></span>
      <div class="top">${pk.step !== "room" && pk.editIdx == null ? `<button class="back jv-press" aria-label="Назад" data-pk-back>${icon("back", 20)}</button>` : ""}
        <div class="t"><small>Шаг ${no} из 3${esc(crumb)}</small><b>${title}</b></div>
        <button class="x jv-press" aria-label="Закрыть" data-pk-close>${icon("close", 18)}</button></div>
      <div class="bars">${[1, 2, 3].map((n) => `<span class="${n <= no ? "on" : ""}"></span>`).join("")}</div>`;
    if (pk.step === "room") {
      html += `<div class="body"><button class="all jv-press${pk.room === "all" ? " on-pick" : ""}" data-pk-room="all"><span class="h">${icon("home", 22)}</span>
          <span class="w"><b>Весь дом</b><small>Все комнаты сразу и охрана</small></span><span style="color:var(--text3);display:flex">${icon("chev", 18)}</span></button>
        <div class="rooms">${rooms().map((r) => `<button class="room jv-press${pk.room === r.name ? " on-pick" : ""}" data-pk-room="${esc(r.name)}"><b>${esc(r.name)}</b>
          <small>${plural(r.devices.length, "устройство", "устройства", "устройств")}</small></button>`).join("")}</div></div>`;
    } else if (pk.step === "what") {
      const things = thingsOf(pk.room), norms = normsOf(pk.room);
      html += `<div class="body">${things.length ? `<span class="lbl">Устройства</span><div class="things">${things.map((k) =>
          `<button class="thing jv-press${pk.thing === k ? " on-pick" : ""}" data-pk-thing="${k}"><i>${icon(TH[k][1], 19)}</i><span>${TH[k][0]}</span></button>`).join("")}</div>` : ""}
        ${norms.length ? `<span class="lbl" style="margin-top:4px">Норма</span><div class="things">${norms.map((k) =>
          `<button class="norm jv-press" data-pk-thing="${k}">${icon(TH[k][1], 18)}${TH[k][2]}</button>`).join("")}</div>` : ""}</div>`;
    } else {
      html += actionHtml();
    }
    return html + `</div>`;
  }

  function actionHtml() {
    const pk = ed.pk, k = pk.thing, d = pk.draft;
    const seg = (opts, cur, key) => `<div class="segs" role="radiogroup" style="grid-template-columns:repeat(${opts.length},minmax(0,1fr))">` +
      opts.map((o) => `<button role="radio" aria-checked="${cur === o[0]}" class="${cur === o[0] ? "on" : ""}" data-pk-set="${key}:${o[0]}">${o[1]}</button>`).join("") + `</div>`;
    let html = `<div class="body act">`;
    if (["light", "ac", "heat", "vent", "humid", "socket"].indexOf(k) >= 0) html += seg([["true", "Включить"], ["false", "Выключить"]], String(d.on), "on");
    if (k === "guard") html += seg([["true", "Поставить"], ["false", "Снять"]], String(d.on), "on");
    if (k === "curtain") html += seg([["open", "Открыть"], ["close", "Закрыть"], ["pos", "Положение"]], d.mode, "mode");
    const slider = (lbl, min) => `<div class="val"><div class="head"><span>${lbl}</span><b><span id="pkV">${d.v}</span><i>%</i></b></div>
      <span class="slider"><span class="trk0"></span><span class="fill" style="width:${d.v}%"></span><span class="thumb" style="left:${d.v}%"></span>
      <input type="range" min="${min}" max="100" step="5" value="${d.v}" data-pk-range aria-label="${lbl}"></span></div>`;
    if (k === "light" && d.on) html += slider("Яркость", 5);
    if (k === "curtain" && d.mode === "pos") html += slider("Открыть на", 0);
    const stepper = (lbl, hint, text) => `<div class="stepbox"><span><b>${lbl}</b><small>${hint}</small></span><div class="stepper">
      <button class="jv-press" aria-label="Меньше" data-pk-step="-1">${icon("minus", 18)}</button><b aria-live="polite">${text}</b>
      <button class="jv-press" aria-label="Больше" data-pk-step="1">${icon("plus", 18)}</button></div></div>`;
    if ((k === "ac" || k === "heat") && d.on) html += stepper("Температура", k === "ac" ? "Охлаждать до этой температуры" : "Греть до этой температуры", fmt(d.v) + " °C");
    if (NORMS[k]) html += stepper(TH[k][2], "Дом сам включит отопление, кондиционер, вентиляцию или увлажнитель", fmt(d.v) + NORMS[k][4]);
    if (k === "water" || k === "gas") {
      html += `<div class="valves" role="radiogroup" aria-label="Действие с краном">
        <button class="valve on" role="radio" aria-checked="true"><span class="dot"></span><span class="w"><b>Закрыть</b><small>${k === "gas" ? "Перекрыть газ" : "Перекрыть воду"} ${esc(at(pk.room))}</small></span></button>
        <button class="valve" role="radio" aria-checked="false" aria-disabled="true" disabled><span class="dot"></span><span class="w"><b>Открыть</b><small>Открыть — только с подтверждением</small></span><span class="lock">${icon("lock", 18)}</span></button>
        <span class="note">Сценарий может только перекрыть кран. Открыть его можно вручную — удержанием кнопки или PIN-кодом.</span></div>`;
    }
    const preview = label(Object.assign({ room: pk.room, thing: k }, d));
    html += `<div class="preview"><i>${icon(TH[k][1], 17)}</i><span id="pkPreview">${esc(preview)}</span></div>
      <button class="confirm-step jv-press" data-pk-confirm>${pk.editIdx != null ? "Готово" : "Добавить шаг"}</button></div>`;
    return html;
  }

  function pickerClick(t) {
    const pk = ed.pk;
    if (t.closest("[data-pk-close]")) { ed.pk = null; drawOverlay(); return true; }
    if (t.closest("[data-pk-back]")) { pk.step = pk.step === "action" ? "what" : "room"; drawOverlay(); return true; }
    const room = t.closest("[data-pk-room]");
    if (room) { pk.room = room.dataset.pkRoom; pk.step = "what"; drawOverlay(); return true; }
    const thing = t.closest("[data-pk-thing]");
    if (thing) { pk.thing = thing.dataset.pkThing; pk.draft = Object.assign({}, DEF[pk.thing]); pk.step = "action"; drawOverlay(); return true; }
    const set = t.closest("[data-pk-set]");
    if (set) {
      const [key, val] = set.dataset.pkSet.split(":");
      pk.draft[key] = val === "true" ? true : val === "false" ? false : val;
      drawOverlay();
      return true;
    }
    const step = t.closest("[data-pk-step]");
    if (step) {
      const dir = +step.dataset.pkStep, k = pk.thing;
      const [lo, hi, d] = NORMS[k] ? NORMS[k].slice(1, 4) : [16, 30, 0.5];
      pk.draft.v = Math.min(hi, Math.max(lo, Math.round((pk.draft.v + dir * d) * 10) / 10));
      drawOverlay();
      return true;
    }
    if (t.closest("[data-pk-confirm]")) {
      const s = Object.assign({ room: pk.thing === "guard" ? "all" : pk.room, thing: pk.thing }, pk.draft);
      if (pk.editIdx != null) ed.steps[pk.editIdx] = s; else ed.steps.push(s);
      toast(pk.editIdx != null ? "Шаг изменён" : "Шаг добавлен");
      ed.pk = null;
      ed.tested = -1;
      drawSteps();
      drawOverlay();
      return true;
    }
    return false;
  }

  // ---------------------------------------------------------------- dragging a step by its grip

  function dragStart(e, i) {
    const grip = e.target.closest("[data-grip]");
    try { grip.setPointerCapture(e.pointerId); } catch (err) { /* no capture: still works while over the grip */ }
    const row = grip.closest(".se-step");
    const scale = row.getBoundingClientRect().height / row.offsetHeight || 1;
    ed.drag = { i, y0: e.clientY, dy: 0, scale };
    row.classList.add("lifted");
  }
  function dragMove(e) {
    const d = ed.drag;
    if (!d) return;
    d.dy = (e.clientY - d.y0) / d.scale;
    const target = Math.max(0, Math.min(ed.steps.length - 1, d.i + Math.round(d.dy / PITCH)));
    ed.node.querySelectorAll(".se-step").forEach((row, j) => {
      if (j === d.i) row.style.transform = `translateY(${d.dy}px) scale(1.02)`;
      else if (j > d.i && j <= target) row.style.transform = `translateY(-${PITCH}px)`;
      else if (j < d.i && j >= target) row.style.transform = `translateY(${PITCH}px)`;
      else row.style.transform = "";
    });
  }
  function dragEnd() {
    const d = ed.drag;
    if (!d) return;
    ed.drag = null;
    const to = Math.max(0, Math.min(ed.steps.length - 1, d.i + Math.round(d.dy / PITCH)));
    if (to !== d.i) { const [x] = ed.steps.splice(d.i, 1); ed.steps.splice(to, 0, x); ed.tested = -1; }
    drawSteps();
  }

  // ---------------------------------------------------------------- open, try, save, delete

  function openScene(scene, opts) {
    opts = opts || {};
    const copy = !!opts.copy;
    ed.id = scene && !copy ? scene.id : null;
    ed.name = scene ? scene.name + (copy ? " (копия)" : "") : "";
    ed.icon = scene ? data().icon(scene) : "play";
    ed.phrases = scene && !copy ? scene.phrases.slice() : [];  // a copy's phrases would start the original too
    ed.steps = scene && scene.steps ? scene.steps.map(fromApi).filter((s) => s.thing) : [];
    ed.phraseEditing = false; ed.pk = null; ed.drag = null; ed.tested = -1; ed.testing = false; ed.ask = null;
    ed.node.querySelector("#seTitle").textContent = ed.id ? "Сценарий" : "Новый сценарий";
    ed.node.querySelector("#seName").value = ed.name;
    ed.node.querySelector("#seDelete").hidden = !ed.id;
    ed.node.querySelector(".se-scroll").scrollTop = 0;
    drawAll();
    window.JV.show("scene-edit");
  }

  async function trySteps() {
    if (!ed.steps.length || ed.testing) return;
    ed.testing = true;
    ed.tested = -1;
    drawSteps();
    let result = null;
    try { result = await api("POST", "/api/scenes/try", { steps: ed.steps.map(toApi) }); } catch (e) { if (e.message !== "locked") toast(e.message, true); }
    if (!result || result.error) {
      ed.testing = false;
      drawSteps();
      if (result) toast(result.error, true);
      return;
    }
    let n = -1;  // the steps tick off one by one, as the design shows them
    const tick = setInterval(() => {
      n += 1;
      ed.tested = n;
      drawSteps();
      if (n >= ed.steps.length - 1) {
        clearInterval(tick);
        ed.testing = false;
        drawBar();
        toast("Проверено — " + plural(ed.steps.length, "шаг выполнен", "шага выполнены", "шагов выполнено"));
        window.JV.refresh();
      }
    }, 380);
  }

  async function save() {
    const body = { name: ed.name.trim(), icon: ed.icon, phrases: ed.phrases, steps: ed.steps.map(toApi) };
    if (ed.id) body.id = ed.id;
    try {
      const r = await api("POST", "/api/scenes", body);
      if (r.error) { toast(r.error, true); return; }
      toast(ed.id ? "Сценарий сохранён" : "«" + body.name + "» готов — скажи «Jarvis, " + (ed.phrases[0] || body.name.toLowerCase()) + "»");
      window.JV.show("scenes");
    } catch (e) { if (e.message !== "locked") toast(e.message, true); }
  }

  ed.node = section("screen-scene-edit");
  skeleton();
  ed.node.addEventListener("input", (e) => {
    if (e.target.id === "seName") { ed.name = e.target.value; drawBar(); return; }
    if (e.target.matches("[data-pk-range]")) {  // the slider follows the finger, the preview too
      const v = +e.target.value;
      ed.pk.draft.v = v;
      const s = e.target.closest(".slider");
      s.querySelector(".fill").style.width = v + "%";
      s.querySelector(".thumb").style.left = v + "%";
      ed.node.querySelector("#pkV").textContent = v;
      ed.node.querySelector("#pkPreview").textContent = label(Object.assign({ room: ed.pk.room, thing: ed.pk.thing }, ed.pk.draft));
    }
  });
  ed.node.addEventListener("keydown", (e) => {
    if (e.target.id === "sePhraseNew" && e.key === "Enter") { e.preventDefault(); ed.node.querySelector("[data-phrase-add]").click(); }
  });
  ed.node.addEventListener("pointerdown", (e) => {
    const grip = e.target.closest("[data-grip]");
    if (grip) { e.preventDefault(); dragStart(e, +grip.dataset.grip); }
  });
  ed.node.addEventListener("pointermove", dragMove);
  ed.node.addEventListener("pointerup", dragEnd);
  ed.node.addEventListener("pointercancel", dragEnd);
  ed.node.addEventListener("click", (e) => {
    const t = e.target;
    if (ed.ask) {
      if (t.closest("[data-ask-no]")) { ed.ask = null; drawOverlay(); }
      else if (t.closest("[data-ask-yes]")) { const a = ed.ask; ed.ask = null; drawOverlay(); a.act(); }
      return;
    }
    if (ed.pk && pickerClick(t)) return;
    if (t.closest("[data-back]")) { window.JV.show("scenes"); return; }
    const ic = t.closest("[data-icon-pick]");
    if (ic) { ed.icon = ic.dataset.iconPick; drawIcons(); return; }
    if (t.closest("[data-phrase-start]")) { ed.phraseEditing = true; drawPhrases(); return; }
    if (t.closest("[data-phrase-add]")) {
      const v = (ed.node.querySelector("#sePhraseNew").value || "").trim().replace(/[.,]/g, " ").trim();
      if (v && ed.phrases.map((x) => x.toLowerCase()).indexOf(v.toLowerCase()) < 0) ed.phrases.push(v);
      ed.phraseEditing = false;
      drawPhrases();
      return;
    }
    const pdel = t.closest("[data-phrase-del]");
    if (pdel) { ed.phrases.splice(+pdel.dataset.phraseDel, 1); drawPhrases(); return; }
    if (t.closest("[data-add]")) { ed.pk = { step: "room", room: null, thing: null, draft: {}, editIdx: null }; drawOverlay(); return; }
    const sedit = t.closest("[data-step-edit]");
    if (sedit) {
      const i = +sedit.dataset.stepEdit, s = ed.steps[i];
      ed.pk = { step: "action", room: s.room, thing: s.thing, draft: Object.assign({}, DEF[s.thing], s), editIdx: i };
      drawOverlay();
      return;
    }
    const sdel = t.closest("[data-step-del]");
    if (sdel) { ed.steps.splice(+sdel.dataset.stepDel, 1); ed.tested = -1; drawSteps(); return; }
    if (t.closest("[data-try]")) { trySteps(); return; }
    if (t.closest("[data-save]")) { save(); return; }
    if (t.closest("[data-delete]") && ed.id) {
      const scene = data().list().find((x) => x.id === ed.id);
      if (scene) { ed.ask = data().deleteAsk(scene, () => window.JV.show("scenes")); drawOverlay(); }
    }
  });
  window.JV.add("scene-edit", { tab: "scenes", back: "scenes", open: () => {} });
  window.JV.editScene = openScene;

  // ---------------------------------------------------------------- the schedule editor

  const DAYS = [["Пн", "понедельник", "пн"], ["Вт", "вторник", "вт"], ["Ср", "среда", "ср"], ["Чт", "четверг", "чт"],
    ["Пт", "пятница", "пт"], ["Сб", "суббота", "сб"], ["Вс", "воскресенье", "вс"]];
  const PRESETS = [["Будни", [1, 1, 1, 1, 1, 0, 0]], ["Выходные", [0, 0, 0, 0, 0, 1, 1]], ["Каждый день", [1, 1, 1, 1, 1, 1, 1]]];
  const p2 = (n) => (n < 10 ? "0" : "") + n;
  const sd = { node: null, r: null, name: "", h: 8, m: 0, days: [0, 0, 0, 0, 0, 0, 0], scene: "", ask: null };

  function sdSkeleton() {
    sd.node.innerHTML = `<div class="se-scroll" style="padding-bottom:120px">
      <div class="se-head"><button class="sub-back" data-back>${icon("back", 20)}Сценарии</button><h1 id="sdTitle"></h1></div>
      <label class="se-field"><span>Название</span><input id="sdName" maxlength="40" autocomplete="off"></label>
      <section class="sd-time" id="sdTime"></section>
      <section class="se-sec"><div class="cap"><h2>Дни недели</h2></div><div class="sd-days" id="sdDays" role="group" aria-label="Дни недели"></div><div class="sd-quick" id="sdQuick"></div></section>
      <section class="se-sec"><div class="cap"><h2 id="sdScenesCap">Какой сценарий запустить</h2></div><div class="sd-scenes" id="sdScenes" role="radiogroup" aria-label="Сценарий"></div>
        <div class="sd-guard"><i>${icon("shield", 18)}</i><span>Не срабатывает, когда дом под охраной</span></div></section>
      <button class="se-delete jv-press" data-delete id="sdDelete">${icon("close", 16)}Удалить расписание</button></div>
    <div class="se-bar one"><button class="save jv-press" data-save id="sdSave">Сохранить</button></div><div id="sdOverlay"></div>`;
  }

  function nextRun() {
    if (sd.r && !sd.r.time) return { text: "Срабатывает на закате", on: true };
    const now = new Date(), today = (now.getDay() + 6) % 7, nowMin = now.getHours() * 60 + now.getMinutes(), tm = sd.h * 60 + sd.m;
    for (let k = 0; k <= 7; k++) {
      const d = (today + k) % 7;
      if (!sd.days[d] || (k === 0 && tm <= nowMin)) continue;
      const mins = k * 1440 + tm - nowMin, hh = Math.floor(mins / 60), mm = mins % 60;
      const whenTxt = k === 0 ? "сегодня" : k === 1 ? "завтра" : "в " + DAYS[d][2];
      return { text: "Сработает " + whenTxt + " в " + p2(sd.h) + ":" + p2(sd.m) + " — через " + (hh ? hh + " ч " : "") + mm + " мин", on: true };
    }
    return { text: "Выбери дни недели", on: false };
  }

  function sdDraw() {
    const sun = sd.r && !sd.r.time;
    const nr = nextRun();
    const col = (cur, prev, next, key, word) => `<div class="sd-col"><button class="up jv-press" aria-label="${word} больше" data-sd="${key}:1"><span>${icon("down", 22)}</span></button>
      <span class="near" aria-hidden="true">${prev}</span><span class="cur" aria-live="polite">${cur}</span><span class="near" aria-hidden="true">${next}</span>
      <button class="jv-press" aria-label="${word} меньше" data-sd="${key}:-1">${icon("down", 22)}</button></div>`;
    sd.node.querySelector("#sdTime").innerHTML = (sun ? `<div class="sd-sun">${icon("sun", 26)}На закате</div>`
      : `<div class="sd-cols" role="group" aria-label="Время запуска">${col(p2(sd.h), p2((sd.h + 23) % 24), p2((sd.h + 1) % 24), "h", "Час")}<span class="sd-colon">:</span>${col(p2(sd.m), p2((sd.m + 55) % 60), p2((sd.m + 5) % 60), "m", "Минуты")}</div>`) +
      `<span class="sd-next${nr.on ? " on" : ""}">${esc(nr.text)}</span>`;
    sd.node.querySelector("#sdDays").innerHTML = DAYS.map((d, i) =>
      `<button class="jv-press${sd.days[i] ? " on" : ""}" aria-pressed="${!!sd.days[i]}" aria-label="${d[1]}" data-day="${i}">${d[0]}</button>`).join("");
    sd.node.querySelector("#sdQuick").innerHTML = PRESETS.map((q, i) => {
      const on = q[1].every((x, j) => x === sd.days[j]);
      return `<button class="jv-press${on ? " on" : ""}" aria-pressed="${on}" data-preset="${i}">${q[0]}</button>`;
    }).join("");
    const list = sd.r && !sd.r.app ? [] : data().list();
    sd.node.querySelector("#sdScenesCap").textContent = sd.r && !sd.r.app ? "Что запускает" : "Какой сценарий запустить";
    sd.node.querySelector("#sdScenes").innerHTML = sd.r && !sd.r.app
      ? `<div class="sd-scene on"><i>${icon(sd.r.time ? "sunrise" : "light", 21)}</i><span class="w"><b>${esc(sd.r.name)}</b><small>Встроенное расписание дома — меняются время и дни</small></span></div>`
      : list.map((x) => {
        const on = sd.scene === x.id;
        return `<button class="sd-scene jv-press${on ? " on" : ""}" role="radio" aria-checked="${on}" data-scene-pick="${esc(x.id)}"><i>${icon(data().icon(x), 21)}</i>
          <span class="w"><b>${esc(x.name)}${x.steps ? "<em>свой</em>" : ""}</b><small>${esc(x.does)}</small></span><span class="ring">${on ? icon("check", 14) : ""}</span></button>`;
      }).join("");
    const anyDay = sd.days.some((x) => x);
    const valid = anyDay && (sd.r && !sd.r.app ? true : !!sd.scene);
    const btn = sd.node.querySelector("#sdSave");
    btn.disabled = !valid;
    btn.textContent = valid ? "Сохранить" : !anyDay ? "Выбери дни" : "Выбери сценарий";
    const scene = data().list().find((x) => x.id === sd.scene);
    sd.node.querySelector("#sdName").placeholder = scene ? scene.name : "Например, «Подъём по будням»";
    sd.node.querySelector("#sdOverlay").innerHTML = sd.ask ? data().askHtml(sd.ask) : "";
  }

  function openSchedule(r, opts) {
    opts = opts || {};
    sd.r = r || null;
    sd.name = r ? r.name : "";
    const [h, m] = (r && r.time ? r.time : "08:00").split(":").map(Number);
    sd.h = h; sd.m = m - (m % 5 === 0 ? 0 : m % 5);
    sd.days = [1, 2, 3, 4, 5, 6, 7].map((d) => (r ? (r.days.indexOf(d) >= 0 ? 1 : 0) : 0));
    sd.scene = r && r.app ? r.scene : opts.scene || "";
    sd.ask = null;
    sd.node.querySelector("#sdTitle").textContent = r ? "Расписание" : "Новое расписание";
    const name = sd.node.querySelector("#sdName");
    name.value = sd.name;
    name.disabled = !!(r && !r.app);  // the house's own keep their names
    sd.node.querySelector("#sdDelete").hidden = !(r && r.app);
    sd.node.querySelector(".se-scroll").scrollTop = 0;
    sdDraw();
    window.JV.show("sched-edit");
  }

  async function sdSave() {
    const days = sd.days.map((x, i) => (x ? i + 1 : 0)).filter(Boolean);
    const time = p2(sd.h) + ":" + p2(sd.m);
    const scene = data().list().find((x) => x.id === sd.scene);
    const name = sd.name.trim() || (scene ? scene.name : "");
    try {
      let r;
      if (sd.r && !sd.r.app) {  // the house's own: its time (if it has one) and its days
        if (sd.r.time && sd.r.time !== time) r = await api("POST", "/api/schedules", { action: "set_time", name: sd.r.name, time });
        if (!r || !r.error) r = await api("POST", "/api/schedules", { action: "set_days", id: sd.r.id, days });
      } else if (sd.r) {
        r = await api("POST", "/api/schedules", { action: "update", id: sd.r.id, name, time, days, scene: sd.scene });
      } else {
        r = await api("POST", "/api/schedules", { action: "create", name, time, days, scene: sd.scene });
      }
      if (r.error) { toast(r.error, true); return; }
      toast(sd.r ? "Расписание сохранено" : "Расписание добавлено");
      window.JV.show("scenes");
    } catch (e) { if (e.message !== "locked") toast(e.message, true); }
  }

  sd.node = section("screen-sched-edit");
  sdSkeleton();
  sd.node.addEventListener("input", (e) => { if (e.target.id === "sdName") sd.name = e.target.value; });
  sd.node.addEventListener("click", (e) => {
    const t = e.target;
    if (sd.ask) {
      if (t.closest("[data-ask-no]")) { sd.ask = null; sdDraw(); }
      else if (t.closest("[data-ask-yes]")) { const a = sd.ask; sd.ask = null; sdDraw(); a.act(); }
      return;
    }
    if (t.closest("[data-back]")) { window.JV.show("scenes"); return; }
    const st = t.closest("[data-sd]");
    if (st) {
      const [key, dir] = st.dataset.sd.split(":");
      if (key === "h") sd.h = (sd.h + +dir + 24) % 24; else sd.m = (sd.m + 5 * +dir + 60) % 60;
      sdDraw();
      return;
    }
    const day = t.closest("[data-day]");
    if (day) { const i = +day.dataset.day; sd.days[i] = sd.days[i] ? 0 : 1; sdDraw(); return; }
    const preset = t.closest("[data-preset]");
    if (preset) { sd.days = PRESETS[+preset.dataset.preset][1].slice(); sdDraw(); return; }
    const pick = t.closest("[data-scene-pick]");
    if (pick) { sd.scene = pick.dataset.scenePick; sdDraw(); return; }
    if (t.closest("[data-save]")) { sdSave(); return; }
    if (t.closest("[data-delete]") && sd.r && sd.r.app) {
      const scene = data().list().find((x) => x.id === sd.r.scene);
      const r = sd.r;
      sd.ask = { title: "Удалить расписание?", yes: "Удалить расписание",
        text: "«" + r.name + "» в " + r.time + " больше не будет запускаться." + (scene ? " Сам сценарий «" + scene.name + "» останется." : ""),
        act: async () => {
          try {
            const res = await api("POST", "/api/schedules", { action: "delete", id: r.id });
            if (res.error) { toast(res.error, true); return; }
            toast("Расписание удалено");
            window.JV.show("scenes");
          } catch (err) { if (err.message !== "locked") toast(err.message, true); }
        } };
      sdDraw();
    }
  });
  window.JV.add("sched-edit", { tab: "scenes", back: "scenes", open: () => {} });
  window.JV.editSchedule = openSchedule;

  // the phone's back button: a sheet or a dialog closes first
  const backBefore = window.jarvisBack;
  window.jarvisBack = () => {
    const screen = document.getElementById("app").getAttribute("data-screen");
    if (screen === "scene-edit" && (ed.pk || ed.ask)) { ed.pk = null; ed.ask = null; drawOverlay(); return true; }
    if (screen === "sched-edit" && sd.ask) { sd.ask = null; sdDraw(); return true; }
    return backBefore ? backBefore() : false;
  };
})();
