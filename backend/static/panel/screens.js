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

  // ---------------------------------------------------------------- the guard, the danger sensors, the journal

  const sec = { node: null, data: null, filter: "all", ticks: 0, busy: false };

  const hhmm = (iso) => { const t = new Date(iso); return pad(t.getHours()) + ":" + pad(t.getMinutes()); };
  /** the journal's time: "21:46" today, "Вчера 14:12", "28 сен 08:41". */
  const logTime = (iso) => when(iso).replace("Сегодня, ", "").replace(", ", " ");

  function sensorCards(rooms, offline) {
    const withKey = (k) => rooms.filter((r) => k in r.dangers);
    const fired = (k) => rooms.filter((r) => r.dangers[k]).map((r) => r.name);
    const lower = (names) => names.map((n) => n.toLowerCase()).join(", ");
    const cards = [];
    const danger = (ic, name, key, quiet) => {
      const all = withKey(key);
      if (!all.length) return;
      const bad = fired(key);
      cards.push({ icon: ic, name, alarm: bad.length > 0,
        state: bad.length ? "ТРЕВОГА: " + lower(bad) : (all.length > 1 ? all.length + " датчиков · " : all[0].name + " · ") + quiet });
    };
    danger("smoke", "Дым", "smoke", "норма");
    danger("leak", "Протечка", "moisture", "сухо");
    danger("gas", "Газ", "gas", "норма");
    const moving = rooms.filter((r) => r.readings.motion && r.readings.motion.value).map((r) => r.name);
    cards.push({ icon: "motion", name: "Движение", live: moving.length > 0, state: moving.length ? "Сейчас: " + lower(moving) : "Тихо" });
    const doors = rooms.filter((r) => r.readings.door);
    const openDoors = doors.filter((r) => r.readings.door.value);
    const openWins = rooms.filter((r) => r.readings.window && r.readings.window.value).map((r) => r.name);
    const doorText = !doors.length ? "" : openDoors.length ? "Дверь открыта" : "Дверь закрыта";
    const winText = openWins.length ? "окно открыто: " + lower(openWins) : "окна закрыты";
    cards.push({ icon: "door", name: "Двери и окна", live: openDoors.length + openWins.length > 0,
      state: doorText ? doorText + " · " + winText : winText.charAt(0).toUpperCase() + winText.slice(1) });
    const devices = rooms.reduce((n, r) => n + r.devices.length, 0);
    cards.push({ icon: "hub", name: "Связь", alarm: offline, state: offline ? "Нет связи с домом" : "Дом на связи · " + devices + " устройств" });
    return cards;
  }

  function heroHtml(d) {
    const mode = !d ? "off" : d.armed ? "on" : d.arming ? "arming" : "off";
    let sub = d ? "" : "Загружаю…";
    let countdown = "";
    if (d && mode === "on") sub = "Все датчики активны · с " + hhmm(d.since);
    else if (d && mode === "arming") {
      const elapsed = Math.max(0, (Date.now() - new Date(d.arming_since)) / 1000);
      const left = Math.max(0, Math.ceil(d.arm_seconds - elapsed));
      sub = "Охрана включится через " + Math.floor(left / 60) + ":" + pad(left % 60) + " — успейте выйти";
      // the ring runs the real countdown: as long as the wait, started as long ago as it did
      countdown = `<svg class="count" viewBox="0 0 92 92" width="72" height="72" aria-hidden="true"><circle cx="46" cy="46" r="44" class="bg"/>` +
        `<circle cx="46" cy="46" r="44" class="fg" style="animation-duration:${d.arm_seconds}s;animation-delay:-${elapsed.toFixed(1)}s"/></svg>`;
    } else if (d && d.since) sub = "Снята · с " + hhmm(d.since);
    const title = { off: "Охрана снята", arming: "Включается…", on: "Под охраной" }[mode];
    const btn = { off: "Поставить на охрану", arming: "Отменить", on: "Снять с охраны" }[mode];
    return `<div class="hero ${mode}">
        <div class="row"><span class="badge">${mode === "on" ? `<span class="ring"></span>` : ""}${countdown}<span class="disc">${icon(mode === "on" ? "shieldok" : "shield", 28)}</span></span>
          <div class="words"><b>${title}</b><span>${esc(sub)}</span></div></div>
        <button class="act jv-press" data-guard="${mode}"${d ? "" : " disabled"}>${btn}</button>
      </div>`;
  }

  function journalHtml(d) {
    let html = `<div class="seg" role="group" aria-label="Фильтр журнала">` + [["all", "Все"], ["alarm", "Тревоги"], ["guard", "Охрана"]].map((f) =>
      `<button class="${sec.filter === f[0] ? "on" : ""}" aria-pressed="${sec.filter === f[0]}" data-filter="${f[0]}">${f[1]}</button>`).join("") + `</div>`;
    if (!d) return html + `<p class="scr-empty">Загружаю…</p>`;
    const rows = d.journal.filter((e) => sec.filter === "all" || e.k === sec.filter);
    if (!rows.length) return html + `<p class="scr-empty">За двое суток — ничего.</p>`;
    html += `<div class="log">`;
    rows.forEach((e) => {
      let detail = e.detail || "";
      if (e.k === "alarm") detail = e.cleared ? "Отбой в " + hhmm(e.cleared) : "Тревога ещё идёт";
      const acts = e.acts && e.acts.length ? `<div class="acts"><span>Что сделал дом</span>` + e.acts.map((a) =>
        `<span class="act"><span class="ok">${icon("check", 14)}</span><span class="t">${esc(a.t)}</span><span class="at">${esc(a.at)}</span></span>`).join("") + `</div>` : "";
      html += `<div class="item ${e.k}"><span class="ic-box">${icon(e.icon, 18)}</span><div class="body">
        <div class="line"><span class="title">${esc(e.title)}</span><span class="time">${esc(logTime(e.at))}</span></div>
        ${detail ? `<span class="detail">${esc(detail)}</span>` : ""}${acts}</div></div>`;
    });
    return html + `</div>`;
  }

  function drawSecurity() {
    const h = window.JV.house();
    let html = `<h1 class="scr-title">Охрана</h1>` + heroHtml(sec.data);
    html += `<section style="display:flex;flex-direction:column;gap:10px"><h2 class="sec-label">Датчики опасности</h2><div class="sensors">`;
    sensorCards(h.rooms || [], h.offline).forEach((c) => {
      html += `<div class="sensor${c.live ? " live" : ""}${c.alarm ? " alarm" : ""}"><span class="top">${icon(c.icon, 20)}<span class="dot">${c.live ? `<i class="ping"></i>` : ""}<i></i></span></span>
        <span style="display:flex;flex-direction:column;gap:2px"><span class="name">${esc(c.name)}</span><span class="state">${esc(c.state)}</span></span></div>`;
    });
    html += `</div></section><section style="display:flex;flex-direction:column;gap:10px"><div class="scr-h2"><h2>Журнал</h2></div>` +
      journalHtml(sec.data) + `</section>`;
    sec.node.innerHTML = html;
  }

  async function loadSecurity() {
    try { sec.data = await api("GET", "/api/security"); } catch (e) { if (e.message !== "locked") toast(e.message, true); }
    drawSecurity();
  }

  function wireSecurity() {
    sec.node.addEventListener("click", async (e) => {
      const f = e.target.closest("[data-filter]");
      if (f) { sec.filter = f.dataset.filter; drawSecurity(); return; }
      const g = e.target.closest("[data-guard]");
      if (!g || sec.busy) return;
      const action = { off: "arm", arming: "cancel", on: "disarm" }[g.dataset.guard];
      sec.busy = true;
      try {
        const result = await api("POST", "/api/security", { action });
        if (result.error) toast(result.error, true);
        else { sec.data = result; toast({ arm: "Охрана включится через 2 минуты", cancel: "Отменено", disarm: "Охрана снята" }[action]); }
      } catch (err) { if (err.message !== "locked") toast(err.message, true); }
      sec.busy = false;
      drawSecurity();
    });
  }

  sec.node = section("screen-shield");
  wireSecurity();
  window.JV.add("shield", {
    open: () => { sec.ticks = 0; drawSecurity(); loadSecurity(); },
    // every refresh (5 s): the sensors from the fresh house; the journal and the countdown every third
    tick: () => { sec.ticks += 1; if (sec.ticks % 3 === 0) loadSecurity(); else drawSecurity(); },
  });
})();
