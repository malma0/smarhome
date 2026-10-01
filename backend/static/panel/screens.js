/* Jarvis panel - the screens behind the nav (the design's PhoneScenes, PhoneSecurity...).
   Built on what app.js shares as window.JV: api, toast, icon, esc. Plain ES2017,
   like app.js, so an old WebView runs it. */
(function () {
  "use strict";
  const { api, toast, icon, esc } = window.JV;

  function section(id) {
    const node = document.createElement("section");
    node.className = "screen";
    node.id = id;
    document.getElementById("app").insertBefore(node, document.getElementById("nav"));
    return node;
  }

  const pad = (n) => (n < 10 ? "0" : "") + n;
  const MONTHS = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];

  /** "2026-10-02T07:00:00+07:00" -> "Сегодня, 07:00" / "Вчера, 23:30" / "28 сен, 08:41". */
  function when(iso) {
    if (!iso) return "";
    const at = new Date(iso);
    if (isNaN(at)) return "";
    const day = new Date(at.getFullYear(), at.getMonth(), at.getDate());
    const now = new Date();
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const days = Math.round((today - day) / 86400000);
    const time = pad(at.getHours()) + ":" + pad(at.getMinutes());
    if (days === 0) return "Сегодня, " + time;
    if (days === 1) return "Вчера, " + time;
    return at.getDate() + " " + MONTHS[at.getMonth()] + ", " + time;
  }

  // ---------------------------------------------------------------- scenarios and schedules

  const SCENE_ICONS = { "я ушёл": "exit", "я дома": "home", "спокойной ночи": "moon", "доброе утро": "sunrise" };
  const DAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];

  const scenes = { node: null, list: null, scheds: null, running: "", justRan: "", busy: false };

  function drawScenes() {
    const s = scenes;
    let html = `<div class="scr-head"><h1>Сценарии</h1><span>Одним касанием или голосом: «Jarvis, я ушёл»</span></div>`;
    if (!s.list) {
      html += `<p class="scr-empty">Загружаю…</p>`;
    } else {
      // the one that ran last is "the current" one - the design's highlighted card
      const latest = s.list.filter((x) => x.last).sort((a, b) => new Date(b.last) - new Date(a.last))[0];
      html += `<div class="scenes">`;
      s.list.forEach((x) => {
        const running = s.running === x.name;
        const active = !running && !s.running && latest && latest.name === x.name;
        let meta = when(x.last) || "Ещё не запускался";
        if (running) meta = "Выполняется…";
        else if (active) meta = "Запущен · " + (s.justRan === x.name ? "только что" : meta.split(", ").pop());
        html += `<button class="scene jv-press${active ? " active" : ""}${running ? " running" : ""}" data-scene="${esc(x.name)}" aria-label="Запустить сценарий «${esc(x.name)}»">
          <span class="top"><span class="ic-box">${icon(SCENE_ICONS[x.name.toLowerCase()] || "scenes", 22)}</span>${active ? `<span class="done">${icon("check", 14)}</span>` : ""}</span>
          <span class="txt"><span class="name">${esc(x.name)}</span><span class="desc">${esc(x.does)}</span><span class="meta">${esc(meta)}</span></span>
          ${running ? `<span class="run"></span>` : ""}
        </button>`;
      });
      html += `</div>`;
      if (!s.list.length) html += `<p class="scr-empty">В доме пока нет сценариев.</p>`;
    }
    html += `<button class="add-scene jv-press" data-new="scene">${icon("plus", 18)}Новый сценарий</button>`;
    html += `<div style="display:flex;flex-direction:column;gap:10px"><div class="scr-h2"><h2>Расписания</h2><button class="jv-press" data-new="schedule">${icon("plus", 16)}Добавить</button></div><div class="scheds">`;
    (s.scheds || []).forEach((r) => {
      const time = r.time
        ? `<label class="time">${esc(r.time)}<input type="time" value="${esc(r.time)}" data-time="${esc(r.name)}" aria-label="Время: ${esc(r.name)}"></label>`
        : `<span class="time sun">закат</span>`;
      const days = DAYS.map((d, i) => {
        const on = r.days.indexOf(i + 1) >= 0;
        return r.days_editable
          ? `<button class="day${on ? " on" : ""}" data-day="${r.id}:${i + 1}" aria-pressed="${on}">${d}</button>`
          : `<span class="day${on ? " on" : ""}">${d}</span>`;
      }).join("");
      html += `<div class="sched${r.on ? "" : " off"}">
        <div class="body"><div class="line">${time}<span class="name">${esc(r.name)}</span></div>
          <div class="days" aria-label="Дни: ${DAYS.filter((d, i) => r.days.indexOf(i + 1) >= 0).join(", ")}">${days}</div></div>
        <button class="switch${r.on ? " on" : ""}" role="switch" aria-checked="${r.on}" aria-label="${esc(r.name)}" data-sched="${esc(r.name)}"><span class="trk"><span class="knob"></span></span></button>
      </div>`;
    });
    if (s.scheds && !s.scheds.length) html += `<p class="scr-empty">Расписаний пока нет.</p>`;
    html += `</div></div>`;
    s.node.innerHTML = html;
  }

  async function loadScenes() {
    try {
      const [list, scheds] = await Promise.all([api("GET", "/api/scenes"), api("GET", "/api/schedules")]);
      scenes.list = list.scenes;
      scenes.scheds = scheds.schedules;
    } catch (e) {
      if (e.message !== "locked") toast(e.message, true);
    }
    drawScenes();
  }

  async function changeSchedule(body, ok) {
    try {
      const result = await api("POST", "/api/schedules", body);
      if (result.error) { toast(result.error, true); } else { scenes.scheds = result.schedules; if (ok) toast(ok); }
    } catch (e) { if (e.message !== "locked") toast(e.message, true); }
    drawScenes();
  }

  function wireScenes() {
    const node = scenes.node;
    node.addEventListener("click", async (e) => {
      const scene = e.target.closest("[data-scene]");
      if (scene && !scenes.running) {
        const name = scene.dataset.scene;
        scenes.running = name;
        drawScenes();
        const started = Date.now();
        let result = null;
        try { result = await api("POST", "/api/scenarios/run", { name }); } catch (err) { if (err.message !== "locked") toast(err.message, true); }
        // the bar runs its 1.2 s even when the house answers sooner - the design's rhythm
        setTimeout(async () => {
          scenes.running = "";
          if (result && result.error) toast(result.error, true);
          else if (result) { scenes.justRan = name; toast("«" + name + "» выполнен"); }
          await loadScenes();
        }, Math.max(0, 1250 - (Date.now() - started)));
        return;
      }
      const sw = e.target.closest("[data-sched]");
      if (sw) {
        const r = scenes.scheds.find((x) => x.name === sw.dataset.sched);
        r.on = !r.on;  // at once, then what the house says
        drawScenes();
        changeSchedule({ action: r.on ? "enable" : "disable", name: r.name }, r.on ? "Расписание включено" : "Расписание выключено");
        return;
      }
      const day = e.target.closest("[data-day]");
      if (day) {
        const [id, n] = day.dataset.day.split(":");
        const r = scenes.scheds.find((x) => x.id === id);
        const d = parseInt(n, 10);
        const days = r.days.indexOf(d) >= 0 ? r.days.filter((x) => x !== d) : r.days.concat([d]).sort();
        if (!days.length) { toast("Нужен хотя бы один день — или выключи расписание", true); return; }
        r.days = days;
        drawScenes();
        changeSchedule({ action: "set_days", id, days });
        return;
      }
      if (e.target.closest("[data-new]")) toast("Создание новых — в следующей версии");
    });
    node.addEventListener("change", (e) => {
      const input = e.target.closest("[data-time]");
      if (!input || !input.value) return;
      changeSchedule({ action: "set_time", name: input.dataset.time, time: input.value }, "Теперь в " + input.value);
    });
  }

  scenes.node = section("screen-scenes");
  wireScenes();
  window.JV.add("scenes", { open: () => { drawScenes(); loadScenes(); } });
})();
