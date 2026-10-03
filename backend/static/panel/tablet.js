/* Jarvis panel - the tablet on its side (the design's TabletHome): beside the nav the
   house at a glance - the time, the weather, the guard, the power, the scenarios, the
   voice - and on the right the rooms, until one is opened (app.js puts its panel there).
   Drawn only in the tablet layout (app.js sets data-layout); plain ES2017. */
(function () {
  "use strict";
  const { api, toast, icon, esc } = window.JV;
  const $ = (id) => document.getElementById(id);
  const DAYS = ["Воскресенье", "Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота"];
  const MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];
  const SCENE_ICONS = { "я ушёл": "exit", "я дома": "home", "спокойной ночи": "moon", "доброе утро": "sunrise" };
  const pad = (n) => (n < 10 ? "0" : "") + n;
  const tb = { aside: null, rooms: null, scenes: [], lastRun: "", loadedAt: 0 };
  function paint(node, html) { if (node._html !== html) { node._html = html; node.innerHTML = html; } }  // only what changed

  function make(cls) {
    const node = document.createElement("div");
    node.className = cls;
    $("app").insertBefore(node, $("nav"));
    return node;
  }

  function drawAside() {
    const now = new Date();
    const h = window.JV.house();
    const guard = h.house && h.house.devices.find((d) => d.type === "security");
    const armed = !!(guard && guard.on);
    const latest = tb.lastRun || (tb.scenes.filter((x) => x.last).sort((a, b) => new Date(b.last) - new Date(a.last))[0] || {}).id;
    paint(tb.aside, `<div style="display:flex;flex-direction:column;gap:2px">
        <span class="clock">${pad(now.getHours())}:${pad(now.getMinutes())}</span>
        <span class="day">${DAYS[now.getDay()]}, ${now.getDate()} ${MONTHS[now.getMonth()]}</span>
        <span class="weather"><i>${icon("cloud", 18)}</i>${esc(($("weatherT").textContent || "—") + " · " + ($("weatherW").textContent || ""))}</span></div>
      <button class="tcard jv-press${armed ? " armed" : ""}" aria-pressed="${armed}" data-tab-arm><i>${icon(armed ? "shieldok" : "shield", 24)}</i>
        <span><small>Охрана</small><b>${h.offline || !guard ? "—" : armed ? "Под охраной" : "Снята"}</b></span></button>
      <div class="tcard power"><i>${icon("bolt", 24)}</i><span><b>${esc($("powerNow").textContent || "—")}</b><small>${esc($("powerDay").textContent || "")}</small></span></div>
      <div style="display:flex;flex-direction:column;gap:8px"><span class="lbl">Сценарии</span><div class="tscenes">` +
      tb.scenes.slice(0, 4).map((x) => `<button class="tscene jv-press${x.id === latest ? " on" : ""}" data-tab-scene="${esc(x.id)}">
        <i>${icon(x.icon || SCENE_ICONS[x.name.toLowerCase()] || "play", 20)}</i><span>${esc(x.name)}</span></button>`).join("") +
      `</div></div><span style="flex:1"></span>
      <button class="talk jv-press" aria-label="Говорить с Jarvis" data-tab-talk><span class="mini-orb"><span class="h"></span><span class="b"></span></span>
        <span><b>Скажи «Jarvis»</b><small>или нажми</small></span></button>`);
  }

  function drawRooms() {
    const h = window.JV.house();
    const rooms = h.rooms || [];
    const windows = rooms.filter((r) => r.readings.window && r.readings.window.value).map((r) => r.name.toLowerCase());
    const dangers = rooms.filter((r) => Object.keys(r.dangers).some((k) => r.dangers[k])).map((r) => r.name.toLowerCase());
    const summary = h.offline ? "Нет связи — показаны последние данные"
      : dangers.length ? "Тревога: " + dangers.join(", ")
        : "Всё в норме" + (windows.length ? " · окно открыто: " + windows.join(", ") : "");
    paint(tb.rooms, `<div style="display:flex;flex-direction:column;gap:4px"><h1>Квартира</h1><span class="sum">${esc(summary)}</span></div><div class="list">` +
      rooms.map((r) => {
        const v = (k) => (r.readings[k] ? parseFloat(r.readings[k].value) : null);
        const t = v("temperature"), hum = v("humidity"), co2 = v("carbon_dioxide");
        const co2Max = r.norms.co2_max ? r.norms.co2_max.value : 800;
        const on = r.devices.filter((d) => d.on && d.type !== "water_valve" && d.type !== "gas_valve" && d.type !== "heating").length;
        return `<button class="troom jv-press" data-tab-room="${esc(r.name)}"><span class="w"><b>${esc(r.name)}</b>
          <span class="nums">${hum != null ? `<span>${Math.round(hum)}%</span>` : ""}${co2 != null ? `<span class="${co2 > co2Max ? "warn" : ""}">CO₂ ${Math.round(co2)}</span>` : ""}</span>
          <small class="${on ? "on" : ""}">${on ? "Включено: " + on : "Всё выключено"}</small></span>
          <span class="t">${t != null ? String(t.toFixed(1)).replace(".", ",") + "°" : "—"}</span><span class="chev">${icon("chev", 18)}</span></button>`;
      }).join("") + `</div>`);
  }

  async function loadScenes() {
    try {
      const r = await api("GET", "/api/scenes");
      tb.scenes = r.scenes.filter((x) => !x.steps).concat(r.scenes.filter((x) => x.steps));  // the house's own first
      tb.loadedAt = Date.now();
    } catch (e) { /* the next refresh tries again */ }
    if (window.JV.tablet()) drawAside();
  }

  function tick() {
    if (!window.JV.tablet()) return;
    if (Date.now() - tb.loadedAt > 60000) loadScenes();
    drawAside();
    drawRooms();
  }

  tb.aside = make("tab-aside");
  tb.rooms = make("tab-rooms");
  document.getElementById("app").addEventListener("click", async (e) => {
    const t = e.target;
    if (!t.closest(".tab-aside, .tab-rooms")) return;
    if (t.closest("[data-tab-arm]")) { $("armCard").click(); setTimeout(drawAside, 50); return; }
    if (t.closest("[data-tab-talk]")) { window.JV.show("chat"); return; }
    const room = t.closest("[data-tab-room]");
    if (room) { window.JV.openRoom(room.dataset.tabRoom); return; }
    const scene = t.closest("[data-tab-scene]");
    if (scene) {
      const x = tb.scenes.find((s) => s.id === scene.dataset.tabScene);
      tb.lastRun = x.id;
      drawAside();
      try {
        const r = await api("POST", "/api/scenarios/run", { name: x.name });
        toast(r.error ? r.error : "Сценарий «" + x.name + "» запущен", !!r.error);
        window.JV.refresh();
      } catch (err) { if (err.message !== "locked") toast(err.message, true); }
    }
  });
  window.JV.listen(tick);
  setInterval(() => { if (window.JV.tablet() && !document.hidden) drawAside(); }, 20000);  // the clock
  window.addEventListener("resize", () => setTimeout(tick, 50));
})();
