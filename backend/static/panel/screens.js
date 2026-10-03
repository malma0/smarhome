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
  /** A screen redrawn only when it changed: the 5-second refresh would restart every animation and lose taps. */
  function paint(node, html) {
    if (node._html === html) return;
    node._html = html;
    node.innerHTML = html;
  }
  const plural = (n, one, few, many) => {
    const t = n % 10, h = n % 100;
    return n + " " + (t === 1 && h !== 11 ? one : t >= 2 && t <= 4 && (h < 12 || h > 14) ? few : many);
  };
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
  const sceneIcon = (x) => x.icon || SCENE_ICONS[x.name.toLowerCase()] || "play";

  const scenes = { node: null, list: null, scheds: null, running: "", justRan: "", busy: false, holding: "", menu: "", ask: null, lp: 0, lpFired: false };
  const steps = (n) => plural(n, "шаг", "шага", "шагов");

  function sceneCard(x, latest) {
    const s = scenes;
    const running = s.running === x.id;
    const active = !running && !s.running && latest && latest.id === x.id;
    let meta = when(x.last) || "Ещё не запускался";
    if (running) meta = "Выполняется…";
    else if (active) meta = "Запущен · " + (s.justRan === x.id ? "только что" : meta.split(", ").pop());
    const lifted = s.menu === x.id || s.holding === x.id;
    return `<button class="scene jv-press${active ? " active" : ""}${running ? " running" : ""}${lifted ? " active" : ""}" data-run="${esc(x.id)}" data-hold="${esc(x.id)}" aria-label="Запустить сценарий «${esc(x.name)}». Удерживай для меню">
      <span class="top"><span class="ic-box">${icon(sceneIcon(x), 22)}</span>${active ? `<span class="done">${icon("check", 14)}</span>` : ""}</span>
      <span class="txt"><span class="name">${esc(x.name)}</span><span class="desc">${esc(x.does)}</span><span class="meta">${esc(meta)}</span></span>
      ${running ? `<span class="run"></span>` : ""}</button>`;
  }

  function mineCard(x) {
    const s = scenes;
    const running = s.running === x.id, ran = s.justRan === x.id && !running, holding = s.holding === x.id, lifted = s.menu === x.id;
    const meta = running ? "Выполняется…" : ran ? "Запущен · только что"
      : (x.phrases.length ? "«" + x.phrases[0] + "» · " : "") + steps(x.steps.length);
    const cls = (running ? " running" : "") + (ran ? " ran" : "") + (holding ? " holding" : "") + (lifted ? " lifted" : "");
    return `<div class="mine-card${cls}"><button class="go" data-mine="${esc(x.id)}" data-hold="${esc(x.id)}" aria-label="Запустить «${esc(x.name)}». Удерживай для меню">
        <span class="ic-box">${icon(sceneIcon(x), 21)}</span><span class="words"><b>${esc(x.name)}</b><span>${esc(x.does)}</span>
        <small>${icon("mic", 12)}${esc(meta)}</small></span></button>
      <button class="edit jv-press" data-edit="${esc(x.id)}" aria-label="Изменить сценарий «${esc(x.name)}»">${icon("edit", 19)}</button>
      ${holding ? `<span class="hold-bar"></span>` : ""}${running ? `<span class="run"></span>` : ""}</div>`;
  }

  function schedRow(r) {
    const scene = r.app ? (scenes.list || []).find((x) => x.id === r.scene) : null;
    const runs = r.app ? (scene ? scene.name : "сценарий удалён") : r.time ? "встроенное" : "на закате";
    const days = DAYS.map((d, i) => `<span class="day${r.days.indexOf(i + 1) >= 0 ? " on" : ""}">${d}</span>`).join("");
    return `<div class="sched${r.on ? "" : " off"}">
      <button class="open" data-sched-edit="${esc(r.id)}" data-hold-sched="${esc(r.id)}" aria-label="Изменить расписание «${esc(r.name)}». Удерживай для меню">
        <span class="line"><span class="time${r.time ? "" : " sun"}">${esc(r.time || "закат")}</span><span class="name">${esc(r.name)}</span></span>
        <span class="runs"><i>${icon(scene ? sceneIcon(scene) : r.time ? "clock" : "sun", 14)}</i>→ ${esc(runs)}</span>
        <span class="days" aria-hidden="true">${days}</span></button>
      <button class="switch${r.on ? " on" : ""}" role="switch" aria-checked="${r.on}" aria-label="Расписание «${esc(r.name)}»" data-sched="${esc(r.id)}"><span class="trk"><span class="knob"></span></span></button>
    </div>`;
  }

  function drawScenes() {
    const s = scenes;
    let html = `<div class="scr-head"><h1>Сценарии</h1><span>Одним касанием или голосом: «Jarvis, я ушёл»</span></div>`;
    if (!s.list) {
      paint(s.node, html + `<p class="scr-empty">Загружаю…</p>`);
      return;
    }
    const builtIn = s.list.filter((x) => x.builtin), mine = s.list.filter((x) => !x.builtin);
    const latest = builtIn.filter((x) => x.last).sort((a, b) => new Date(b.last) - new Date(a.last))[0];
    html += `<div class="scenes">${builtIn.map((x) => sceneCard(x, latest)).join("")}</div>`;
    if (builtIn.length) html += `<span class="scr-empty" style="margin-top:-14px">Удерживай карточку, чтобы изменить или удалить</span>`;
    html += `<section class="se-sec" style="gap:10px"><div class="scr-h2"><div class="sub"><h2>Мои сценарии</h2>${mine.length ? `<span>Удерживай карточку для меню</span>` : ""}</div>
      ${mine.length ? `<button class="jv-press" data-new-scene>${icon("plus", 16)}Новый</button>` : ""}</div>`;
    html += mine.length ? `<div class="mine">${mine.map(mineCard).join("")}</div>`
      : `<div class="empty-box"><i>${icon("play", 24)}</i><b>Своих сценариев пока нет</b>
        <span>Собери свой из шагов. Например, «Кино»: приглушить свет в зале и закрыть шторы — по фразе «Jarvis, включи кино».</span>
        <button class="jv-press" data-new-scene>${icon("plus", 18)}Создать сценарий</button></div>`;
    html += `</section><section class="se-sec" style="gap:10px"><div class="scr-h2"><h2>Расписания</h2>
      ${(s.scheds || []).length ? `<button class="jv-press" data-new-sched>${icon("plus", 16)}Добавить</button>` : ""}</div>`;
    html += (s.scheds || []).length ? `<div class="scheds">${s.scheds.map(schedRow).join("")}</div>`
      : `<div class="empty-box"><i>${icon("clock", 24)}</i><b>Расписаний пока нет</b><span>Сценарии могут запускаться сами — например, «Доброе утро» в 7:00 по будням.</span>
        <button class="quiet jv-press" data-new-sched>${icon("plus", 18)}Добавить расписание</button></div>`;
    html += `</section>`;
    const m = s.menu && s.list.find((x) => x.id === s.menu);
    const r = s.schedMenu && (s.scheds || []).find((x) => x.id === s.schedMenu);
    if (r) {
      html += `<button class="scrim" data-menu-close aria-label="Закрыть меню"></button>
        <div class="menu-sheet" role="dialog" aria-label="${esc(r.name)}"><span class="grab"></span>
          <div class="who"><i>${icon("clock", 24)}</i><span><b>${esc(r.name)}</b><span>${esc((r.time || "на закате") + " · " + DAYS.filter((d, i) => r.days.indexOf(i + 1) >= 0).join(", "))}</span></span></div>
          <div class="items"><button data-smenu="edit"><span>${icon("edit", 20)}</span>Изменить</button>
            <button data-smenu="toggle"><span class="cy">${icon(r.on ? "close" : "check", 20)}</span>${r.on ? "Выключить" : "Включить"}</button>
            ${r.app ? `<button data-smenu="delete"><span>${icon("close", 20)}</span>Удалить</button>` : ""}</div></div>`;
    }
    if (m) {
      html += `<button class="scrim" data-menu-close aria-label="Закрыть меню"></button>
        <div class="menu-sheet" role="dialog" aria-label="${esc(m.name)}"><span class="grab"></span>
          <div class="who"><i>${icon(sceneIcon(m), 24)}</i><span><b>${esc(m.name)}</b><span>${esc((m.phrases.length ? "«" + m.phrases[0] + "»" : "") + (m.builtin ? " · встроенный" : " · " + steps(m.steps.length)))}</span></span></div>
          <div class="items"><button data-menu="run"><span class="cy">${icon("play", 20)}</span>Запустить сейчас</button>
            <button data-menu="edit"><span>${icon("edit", 20)}</span>Изменить</button>
            <button data-menu="dup"><span>${icon("scenes", 20)}</span>Дублировать</button>
            <button data-menu="sched"><span>${icon("clock", 20)}</span>Запускать по расписанию</button>
            <button data-menu="delete"><span>${icon("close", 20)}</span>Удалить</button></div></div>`;
    }
    if (s.ask) html += askHtml(s.ask);
    paint(s.node, html);
  }

  /** The design's confirm dialog: {title, text, yes, act}. */
  function askHtml(a) {
    return `<button class="scrim" data-ask-no aria-label="Отмена"></button><div class="ask" role="alertdialog"><div style="display:flex;flex-direction:column;gap:8px">
      <h2>${esc(a.title)}</h2><p>${esc(a.text)}</p></div><button class="yes jv-press" data-ask-yes>${esc(a.yes)}</button><button class="no jv-press" data-ask-no>Отмена</button></div>`;
  }

  /** "Удалить «Кино»?" - what stops working with it. */
  function deleteAsk(x, then) {
    const runs = (scenes.scheds || []).filter((r) => r.app && r.scene === x.id).map((r) => "«" + r.name + "»");
    let text = x.phrases.length ? "Фразы «" + x.phrases.slice(0, 2).join("», «") + "» перестанут работать." : "Сценарий пропадёт из дома.";
    if (x.builtin) text += " Это встроенный сценарий: связанные с ним действия дома (шторы, охрана) тоже перестанут срабатывать.";
    if (runs.length) text += (runs.length > 1 ? " Расписания " : " Расписание ") + runs.join(", ") + (runs.length > 1 ? ", которые запускают" : ", которое запускает") + " этот сценарий, отключ" + (runs.length > 1 ? "атся." : "ится.");
    return { title: "Удалить «" + x.name + "»?", text, yes: "Удалить сценарий", act: async () => {
      try {
        const r = await api("DELETE", "/api/scenes/" + encodeURIComponent(x.id));
        if (r.error) { toast(r.error, true); return; }
        toast("«" + x.name + "» удалён");
      } catch (e) { if (e.message !== "locked") toast(e.message, true); }
      if (then) then();
      await loadScenes();
    } };
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

  async function runScene(x) {
    if (scenes.running) return;
    scenes.running = x.id;
    scenes.menu = "";
    drawScenes();
    const started = Date.now();
    let result = null;
    try { result = await api("POST", "/api/scenarios/run", { name: x.name }); } catch (err) { if (err.message !== "locked") toast(err.message, true); }
    // the bar runs its 1.2 s even when the house answers sooner - the design's rhythm
    setTimeout(async () => {
      scenes.running = "";
      if (result && result.error) toast(result.error, true);
      else if (result) { scenes.justRan = x.id; toast("«" + x.name + "» выполнен"); }
      await loadScenes();
    }, Math.max(0, 1250 - (Date.now() - started)));
  }

  function wireScenes() {
    const node = scenes.node;
    const byId = (id) => (scenes.list || []).find((x) => x.id === id);
    node.addEventListener("pointerdown", (e) => {  // a long press on a scenario or a schedule: its menu
      const card = e.target.closest("[data-hold]"), sched = e.target.closest("[data-hold-sched]");
      if (!card && !sched) return;
      clearTimeout(scenes.lp);
      scenes.lpFired = false;
      if (card) { scenes.holding = card.dataset.hold; drawScenes(); }
      scenes.lp = setTimeout(() => {
        scenes.lpFired = true;
        if (card) { scenes.menu = scenes.holding; scenes.holding = ""; } else scenes.schedMenu = sched.dataset.holdSched;
        drawScenes();
      }, 500);
    });
    const cancelHold = () => { clearTimeout(scenes.lp); if (scenes.holding) { scenes.holding = ""; drawScenes(); } };
    node.addEventListener("pointerup", cancelHold);
    node.addEventListener("pointercancel", cancelHold);
    node.addEventListener("contextmenu", (e) => {
      const card = e.target.closest("[data-hold]"), sched = e.target.closest("[data-hold-sched]");
      if (!card && !sched) return;
      e.preventDefault();
      clearTimeout(scenes.lp);
      scenes.lpFired = true; scenes.holding = "";
      if (card) scenes.menu = card.dataset.hold; else scenes.schedMenu = sched.dataset.holdSched;
      drawScenes();
    });
    node.addEventListener("click", async (e) => {
      const t = e.target;
      const run = t.closest("[data-run]");
      if (run) { if (scenes.lpFired) { scenes.lpFired = false; return; } runScene(byId(run.dataset.run)); return; }
      const mine = t.closest("[data-mine]");
      if (mine) { if (scenes.lpFired) { scenes.lpFired = false; return; } runScene(byId(mine.dataset.mine)); return; }
      const edit = t.closest("[data-edit]");
      if (edit) { window.JV.editScene(byId(edit.dataset.edit)); return; }
      if (t.closest("[data-new-scene]")) { window.JV.editScene(null); return; }
      if (t.closest("[data-new-sched]")) { window.JV.editSchedule(null); return; }
      const se = t.closest("[data-sched-edit]");
      if (se) {
        if (scenes.lpFired) { scenes.lpFired = false; return; }
        window.JV.editSchedule((scenes.scheds || []).find((r) => r.id === se.dataset.schedEdit));
        return;
      }
      if (t.closest("[data-menu-close]")) { scenes.menu = ""; scenes.schedMenu = ""; drawScenes(); return; }
      const sitem = t.closest("[data-smenu]");
      if (sitem) {
        const r = (scenes.scheds || []).find((x) => x.id === scenes.schedMenu);
        scenes.schedMenu = "";
        const what = sitem.dataset.smenu;
        if (what === "edit") { window.JV.editSchedule(r); return; }
        if (what === "toggle") {
          r.on = !r.on;
          drawScenes();
          changeSchedule({ action: r.on ? "enable" : "disable", id: r.id, name: r.name }, r.on ? "Расписание включено" : "Расписание выключено");
          return;
        }
        if (what === "delete") {
          const scene = (scenes.list || []).find((x) => x.id === r.scene);
          scenes.ask = { title: "Удалить расписание?", yes: "Удалить расписание",
            text: "«" + r.name + "» в " + r.time + " больше не будет запускаться." + (scene ? " Сам сценарий «" + scene.name + "» останется." : ""),
            act: () => changeSchedule({ action: "delete", id: r.id }, "Расписание удалено") };
        }
        drawScenes();
        return;
      }
      const item = t.closest("[data-menu]");
      if (item) {
        const x = byId(scenes.menu);
        scenes.menu = "";
        const what = item.dataset.menu;
        if (what === "run") runScene(x);
        else if (what === "edit") window.JV.editScene(x);
        else if (what === "dup") window.JV.editScene(x, { copy: true });
        else if (what === "sched") window.JV.editSchedule(null, { scene: x.id });
        else if (what === "delete") { scenes.ask = deleteAsk(x); drawScenes(); }
        if (what !== "delete" && what !== "run") drawScenes();
        return;
      }
      if (t.closest("[data-ask-no]")) { scenes.ask = null; drawScenes(); return; }
      if (t.closest("[data-ask-yes]") && scenes.ask) { const a = scenes.ask; scenes.ask = null; drawScenes(); a.act(); return; }
      const sw = t.closest("[data-sched]");
      if (sw) {
        const r = scenes.scheds.find((x) => x.id === sw.dataset.sched);  // by id: two can share a name
        r.on = !r.on;  // at once, then what the house says
        drawScenes();
        changeSchedule({ action: r.on ? "enable" : "disable", id: r.id, name: r.name }, r.on ? "Расписание включено" : "Расписание выключено");
      }
    });
  }

  scenes.node = section("screen-scenes");
  wireScenes();
  window.JV.add("scenes", { open: () => { scenes.menu = ""; scenes.schedMenu = ""; scenes.ask = null; drawScenes(); loadScenes(); } });
  // what the editors (scene_editor.js) work with
  window.JV.scenesData = { list: () => scenes.list || [], scheds: () => scenes.scheds || [], reload: loadScenes, icon: sceneIcon, askHtml, deleteAsk };

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
    paint(sec.node, html);
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
        else {
          sec.data = result;
          const minutes = Math.round(result.arm_seconds / 60);
          toast({ arm: minutes ? "Охрана включится через " + plural(minutes, "минуту", "минуты", "минут") : "Охрана включена",
            cancel: "Отменено", disarm: "Охрана снята" }[action]);
        }
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

  // ---------------------------------------------------------------- electricity and the rooms' day

  const SPLIT_COLORS = ["#2F5BEA", "#22D3EE", "#A9B4C2", "#4B5565"];
  const METRICS = [
    { key: "temperature", label: "Температура", reading: "temperature", unit: "°", digits: 1, norm: "temperature" },
    { key: "humidity", label: "Влажность", reading: "humidity", unit: "%", digits: 0, norm: "humidity_min", normWord: "от " },
    { key: "co2", label: "CO₂", reading: "carbon_dioxide", unit: " ppm", digits: 0, norm: "co2_max", normWord: "до " },
  ];
  const en = { node: null, period: "day", data: {}, room: "", metric: "temperature", series: {}, ticks: 0 };
  const fmt = (n, d) => (n == null || isNaN(n) ? "—" : Number(n).toFixed(d === undefined ? 1 : d).replace(".", ","));

  function barsHtml(d) {
    const max = Math.max.apply(null, d.bars.map((b) => b || 0).concat([0.001]));
    const gap = { day: 3, week: 10, month: 2 }[d.period];
    return `<div class="en-bars" style="gap:${gap}px" role="img" aria-label="Расход, всего ${fmt(d.total)} кВт·ч">` + d.bars.map((b, i) => {
      const cls = i === d.current ? "cur" : b == null ? "none" : "";
      return `<span class="${cls}" style="height:${b == null ? 2 : Math.max(2, Math.round((b / max) * 100))}%"></span>`;
    }).join("") + `</div><div class="en-axis">${d.axis.map((a) => `<span>${esc(a)}</span>`).join("")}</div>`;
  }

  function compareText(d) {
    if (d.period !== "day") return d.per_day != null ? "В среднем " + fmt(d.per_day) + " кВт·ч в день" : "";
    if (!d.yesterday_so_far) return "";
    const diff = Math.round(((d.total - d.yesterday_so_far) / d.yesterday_so_far) * 100);
    if (diff === 0) return "Столько же, сколько вчера к этому часу";
    return "На " + Math.abs(diff) + "% " + (diff < 0 ? "меньше" : "больше") + ", чем вчера к этому часу";
  }

  function splitHtml(d) {
    const total = d.split.reduce((n, s) => n + s.kwh, 0);
    if (!d.split.length || total <= 0) return `<p class="scr-empty">Разбивка появится, когда счётчики групп накопят данные.</p>`;
    const parts = d.split.map((s, i) => ({ name: s.name, p: Math.round((s.kwh / total) * 100), c: SPLIT_COLORS[i % SPLIT_COLORS.length] }));
    return `<div class="en-split"><div class="stack">${parts.map((s) => `<span style="width:${s.p}%;background:${s.c}"></span>`).join("")}</div>
      <div class="legend">${parts.map((s) => `<div><i style="background:${s.c}"></i><span>${esc(s.name)}</span><b>${s.p}%</b></div>`).join("")}</div></div>`;
  }

  function roomChartHtml(rooms) {
    const m = METRICS.find((x) => x.key === en.metric);
    const withData = rooms.filter((r) => r.readings[m.reading]);
    if (!withData.length) return `<p class="scr-empty">В доме нет таких датчиков.</p>`;
    if (!withData.some((r) => r.name === en.room)) en.room = withData[0].name;
    const r = withData.find((x) => x.name === en.room);
    const norm = r.norms[m.norm] ? r.norms[m.norm].value : null;
    const values = (en.series[en.room + "|" + m.key] || []).filter((v) => v != null);
    const now = parseFloat(r.readings[m.reading].value);
    let html = `<div class="rh-now"><b>${fmt(now, m.digits)}${m.unit}</b><span>${norm != null ? "норма " + (m.normWord || "") + fmt(norm, m.digits === 1 ? 1 : 0) + m.unit : ""}</span></div>`;
    if (values.length < 2) return html + `<p class="scr-empty">Загружаю сутки…</p>`;
    const all = values.concat(norm != null ? [norm] : []);
    const lo = Math.min.apply(null, all), hi = Math.max.apply(null, all);
    const margin = (hi - lo) * 0.15 || 1;
    const y = (v) => (96 - ((v - (lo - margin)) / ((hi + margin) - (lo - margin))) * 92).toFixed(1);
    const step = 300 / (values.length - 1);
    const pts = values.map((v, i) => (i * step).toFixed(1) + "," + y(v)).join(" ");
    const avg = values.reduce((a, b) => a + b, 0) / values.length;
    const hour = new Date().getHours();
    const label = (back) => pad((hour - back + 24) % 24) + ":00";
    html += `<svg class="rh-chart" viewBox="0 0 300 100" preserveAspectRatio="none" role="img" aria-label="${esc(m.label)} за сутки, ${esc(r.name)}">
      <polygon class="area" points="0,100 ${pts} 300,100"/>${norm != null ? `<line class="norm" x1="0" x2="300" y1="${y(norm)}" y2="${y(norm)}"/>` : ""}<polyline class="line" points="${pts}"/></svg>
      <div class="en-axis"><span>${label(24)}</span><span>${label(18)}</span><span>${label(12)}</span><span>${label(6)}</span><span>сейчас</span></div>
      <div class="rh-stats"><div><small>Мин</small><b>${fmt(Math.min.apply(null, values), m.digits)}${m.unit}</b></div>
        <div><small>Средн</small><b>${fmt(avg, m.digits)}${m.unit}</b></div><div><small>Макс</small><b>${fmt(Math.max.apply(null, values), m.digits)}${m.unit}</b></div></div>`;
    return html;
  }

  function drawEnergy() {
    const h = window.JV.house();
    const d = en.data[en.period];
    const rooms = (h.rooms || []).filter((r) => r.readings.temperature || r.readings.humidity || r.readings.carbon_dioxide);
    const watts = h.house && h.house.house && h.house.house.power_now ? parseFloat(h.house.house.power_now.value) : null;
    const seg = (list, cur, attr, cls) => `<div class="seg ${cls}" role="group">` + list.map((f) =>
      `<button class="${cur === f[0] ? "on" : ""}" aria-pressed="${cur === f[0]}" data-${attr}="${f[0]}">${f[1]}</button>`).join("") + `</div>`;
    let html = `<h1 class="scr-title">Энергия</h1>` + seg([["day", "День"], ["week", "Неделя"], ["month", "Месяц"]], en.period, "period", "big");
    html += `<div class="en-card"><div class="en-top"><div class="l"><small>${{ day: "Сегодня", week: "Эта неделя", month: "Последние 30 дней" }[en.period]}</small>
      <span class="en-total">${d ? fmt(d.total) : "—"}<i>кВт·ч</i></span><span class="en-compare">${d ? esc(compareText(d)) : ""}</span></div>
      <div class="r"><small>Сейчас</small><span class="en-now">${watts != null ? fmt(watts / 1000) + " кВт" : "—"}</span></div></div>
      ${d ? barsHtml(d) : `<p class="scr-empty">Загружаю…</p>`}</div>`;
    html += `<section style="display:flex;flex-direction:column;gap:10px"><h2 class="sec-label">На что ушло</h2>${d ? splitHtml(d) : ""}</section>`;
    html += `<section style="display:flex;flex-direction:column;gap:10px"><div class="scr-h2"><h2>История по комнатам</h2></div>
      <div class="chips">${rooms.map((r) => `<button class="${r.name === en.room ? "on" : ""}" aria-pressed="${r.name === en.room}" data-room="${esc(r.name)}">${esc(r.name)}</button>`).join("")}</div>
      <div class="rh-card">${seg(METRICS.map((m) => [m.key, m.label]), en.metric, "metric", "small")}${roomChartHtml(rooms)}</div></section>`;
    paint(en.node, html);
  }

  async function loadEnergy() {
    const period = en.period;
    try { en.data[period] = await api("GET", "/api/energy?period=" + period); } catch (e) { if (e.message !== "locked") toast(e.message, true); }
    drawEnergy();
  }

  async function loadRoomSeries() {
    if (!en.room) return;
    const key = en.room + "|" + en.metric;
    try {
      const r = await api("GET", "/api/series?room=" + encodeURIComponent(en.room) + "&what=" + en.metric);
      en.series[key] = r.values;
    } catch (e) { if (e.message !== "locked") toast(e.message, true); }
    drawEnergy();
  }

  en.node = section("screen-chart");
  en.node.addEventListener("click", (e) => {
    const p = e.target.closest("[data-period]");
    if (p) { en.period = p.dataset.period; drawEnergy(); loadEnergy(); return; }
    const r = e.target.closest("[data-room]");
    if (r) { en.room = r.dataset.room; drawEnergy(); loadRoomSeries(); return; }
    const m = e.target.closest("[data-metric]");
    if (m) { en.metric = m.dataset.metric; drawEnergy(); loadRoomSeries(); }
  });
  window.JV.add("chart", {
    open: () => { en.ticks = 0; drawEnergy(); loadEnergy(); loadRoomSeries(); },  // drawing picks the first room
    // the power now with every refresh; the bars and the room's day once a minute
    tick: () => { en.ticks += 1; if (en.ticks % 12 === 0) { loadEnergy(); loadRoomSeries(); } else drawEnergy(); },
  });

  // ---------------------------------------------------------------- settings: where each thing is set

  const set = { node: null, ping: null, counts: null };

  function settingsRows() {
    const c = set.counts;
    const p = window.JV.people && window.JV.people();
    return [
      { title: "Дом", rows: [
        { icon: "layout", name: "Планировка и устройства", sub: "Нарисовать комнаты, расставить устройства", go: "editor" },
        { icon: "cycle", name: "Автоматика и нормы", sub: "Свет по движению, ночная подсветка, нормы", go: "automation" },
        { icon: "scenes", name: "Сценарии и расписания",
          sub: c ? plural(c.scenes, "сценарий", "сценария", "сценариев") + " · " + plural(c.schedulesOn, "расписание включено", "расписания включены", "расписаний включено") : "…", go: "scenes" },
      ] },
      { title: "Люди", rows: [
        { icon: "users", name: "Жильцы и голоса", go: "people",
          sub: p ? (p.residents.length ? p.residents.map((r) => r.name + (r.samples ? "" : " (без голоса)")).join(", ") : "Пока никого") : "Кто живёт дома и как звучит" },
        { icon: "key", name: "Гости и временный доступ", go: "guests",
          sub: p ? (p.guests.length ? plural(p.guests.length, "код действует", "кода действуют", "кодов действуют") : "Кодов нет") : "Код для гостя на время" },
      ] },
      { title: "Ассистент", rows: [
        { icon: "voice", name: "Голос ассистента", go: "voice",
          sub: p ? (p.voice.enabled ? "Отвечает голосом" : "Отвечает только текстом") : "Отвечать вслух или текстом" },
        { icon: "bell", name: "Тревоги на телефон", go: "watch",
          sub: !(window.JarvisApp && window.JarvisApp.watch) ? "Работают в приложении Jarvis на телефоне"
            : window.JarvisApp.watching() ? "Включены — даже когда приложение закрыто" : "Выключены — нажми, чтобы включить" },
      ] },
      { title: "Безопасность и связь", rows: [
        { icon: "lock", name: "PIN-код", sub: "Задаётся на компьютере: APP_PIN в .env", go: "pin" },
        { icon: "hub", name: "Подключение к дому",
          sub: "Домашняя сеть · " + location.host + (set.ping != null ? " · отклик " + set.ping + " мс" : ""), go: "connection" },
      ] },
    ];
  }

  function drawSettings() {
    const h = window.JV.house();
    const rooms = (h.rooms || []);
    const devices = rooms.reduce((n, r) => n + r.devices.length, 0);
    let html = `<h1 class="scr-title">Настройки</h1>
      <div class="hub${h.offline ? " off" : ""}"><span class="ball"></span><div class="words"><b>Квартира</b>
        <span>Джарвис ${h.offline ? "не отвечает" : "в сети"} · ${plural(devices, "устройство", "устройства", "устройств")} · ${plural(rooms.length, "комната", "комнаты", "комнат")}</span></div>
        <span class="state"><i></i>${h.offline ? "Нет связи" : "Онлайн"}</span></div>`;
    settingsRows().forEach((g) => {
      html += `<section style="display:flex;flex-direction:column;gap:8px"><h2 class="sec-label">${g.title}</h2><div class="rows">` +
        g.rows.map((r) => `<button class="row" data-go="${r.go}"><span class="ic-box">${icon(r.icon, 19)}</span><span class="words"><b>${esc(r.name)}</b><span>${esc(r.sub)}</span></span><span class="chev">${icon("chev", 18)}</span></button>`).join("") +
        `</div></section>`;
    });
    const theme = window.JV.theme();
    html += `<section style="display:flex;flex-direction:column;gap:8px"><h2 class="sec-label">Оформление</h2><div class="theme-box"><span>Тема</span>
      <div class="seg small" role="group" aria-label="Тема оформления">${[["dark", "Тёмная"], ["light", "Светлая"], ["auto", "Как в системе"]].map((m) =>
        `<button class="${theme === m[0] ? "on" : ""}" aria-pressed="${theme === m[0]}" data-pick-theme="${m[0]}">${m[1]}</button>`).join("")}</div></div></section>`;
    html += `<span class="version">Jarvis · пульт 0.2</span>`;
    paint(set.node, html);
  }

  async function loadSettings() {
    const started = performance.now();
    try {
      const me = await api("GET", "/api/ping");
      set.ping = Math.round(performance.now() - started);
      set.guest = me.guest;
      if (set.guest) { drawSettings(); return; }
      if (window.JV.loadPeople) window.JV.loadPeople(drawSettings);
      const [scn, sch] = await Promise.all([api("GET", "/api/scenes"), api("GET", "/api/schedules")]);
      set.counts = { scenes: scn.scenes.length, schedulesOn: sch.schedules.filter((x) => x.on).length };
    } catch (e) { if (e.message !== "locked") toast(e.message, true); }
    drawSettings();
  }

  set.node = section("screen-sliders");
  set.node.addEventListener("click", (e) => {
    const t = e.target.closest("[data-pick-theme]");
    if (t) { window.JV.theme(t.dataset.pickTheme); drawSettings(); return; }
    const row = e.target.closest("[data-go]");
    if (!row) return;
    const go = row.dataset.go;
    if (set.guest && ["scenes", "automation", "editor", "people", "guests", "voice"].indexOf(go) >= 0) { toast("Гостевой доступ: это меняют только жильцы"); return; }
    if (["scenes", "automation", "editor", "people", "guests", "voice"].indexOf(go) >= 0) { window.JV.show(go); return; }
    if (go === "watch" && window.JarvisApp && window.JarvisApp.watch) {
      if (window.JarvisApp.watching()) {
        window.JarvisApp.unwatch();
        window.JV.store.set("jarvis-panel-watch", "off");
        toast("Тревоги на телефон выключены");
      } else {
        window.JV.store.set("jarvis-panel-watch", "on");
        window.JarvisApp.watch(window.JV.pin());
        toast("Тревоги на телефон включены");
      }
      drawSettings();
      return;
    }
    if (go === "connection" && window.JarvisApp && window.JarvisApp.forget) {  // the Android app: look for the house again
      window.JarvisApp.forget();  // (no confirm(): a bare WebView never shows it)
      return;
    }
    toast({ pin: "PIN меняется в .env на компьютере (APP_PIN)",
      connection: "Пульт подключён к " + location.host }[go] || "Этот раздел — в следующей версии");
  });
  window.JV.add("sliders", { open: () => { drawSettings(); loadSettings(); }, tick: drawSettings });

  // ---------------------------------------------------------------- automation and norms: the house's own behaviour

  const au = { node: null, data: null };
  const NORM_CARDS = [
    { kind: "temperature", icon: "thermo", name: "Температура", step: 0.5, unit: "°C", digits: 1 },
    { kind: "co2_max", icon: "co2", name: "CO₂ до", step: 50, unit: "ppm", digits: 0 },
    { kind: "humidity_min", icon: "humid", name: "Влажность от", step: 5, unit: "%", digits: 0 },
  ];

  const stepper = (key, text) => `<div class="stepper"><button class="jv-press" aria-label="Меньше" data-step="${key}:-1">${icon("minus", 16)}</button><b>${text}</b>` +
    `<button class="jv-press" aria-label="Больше" data-step="${key}:1">${icon("plus", 16)}</button></div>`;

  function drawAutomation() {
    const d = au.data;
    let html = `<div class="au-head"><button class="sub-back" data-back>${icon("back", 20)}Настройки</button>
      <h1>Автоматика и нормы</h1><span>Как дом ведёт себя сам. Меняется сразу — дом подхватывает без перезапуска.</span></div>`;
    if (!d) { au.node.innerHTML = html + `<p class="scr-empty">Загружаю…</p>`; return; }
    const pct = d.night_pct || 0;
    html += `<section style="display:flex;flex-direction:column;gap:8px"><h2 class="sec-label">Свет</h2><div class="au-box">
      <div class="au-row"><span class="words"><b>Гасить свет без движения</b><span>Если в комнате никого нет</span></span>${stepper("light_off_minutes", d.light_off_minutes + " мин")}</div>
      <div class="night"><div class="line"><span class="words" style="flex:1;display:flex;flex-direction:column;gap:2px"><b style="font-size:15px;font-weight:500">Ночная подсветка</b>
        <span style="font-size:12px;color:var(--text2)">Мягкий свет по движению вместо полного</span></span>
        <button class="switch${d.night_light ? " on" : ""}" role="switch" aria-checked="${d.night_light}" aria-label="Ночная подсветка" data-night><span class="trk"><span class="knob"></span></span></button></div>`;
    if (d.night_light) {
      html += `<div class="more">
        <div class="line"><span class="lbl">Яркость</span><span class="slider"><span class="trk0"></span><span class="fill" style="width:${pct}%"></span><span class="thumb" style="left:${pct}%"></span>
          <input type="range" min="5" max="100" step="5" value="${pct}" data-range="night_pct" aria-label="Яркость ночной подсветки"></span><span class="pct">${pct}%</span></div>
        <div class="line"><span class="lbl">Время</span><div class="times">
          <label>${esc(d.night_start)}<input type="time" value="${esc(d.night_start)}" data-time-key="night_start" aria-label="Начало"></label><span>—</span>
          <label>${esc(d.night_end)}<input type="time" value="${esc(d.night_end)}" data-time-key="night_end" aria-label="Конец"></label></div></div>
        <div style="display:flex;flex-direction:column;gap:8px"><span style="font-size:13px;color:var(--text2)">Комнаты</span><div class="room-pills">` +
        d.rooms.map((r) => { const on = d.night_rooms.indexOf(r.slug) >= 0; return `<button class="${on ? "on" : ""}" aria-pressed="${on}" data-night-room="${r.slug}"><i>${on ? icon("check", 11) : ""}</i>${esc(r.name)}</button>`; }).join("") +
        `</div></div></div>`;
    }
    html += `</div></div></section>`;
    html += `<section style="display:flex;flex-direction:column;gap:8px"><h2 class="sec-label">Охрана</h2><div class="au-box">
      <div class="au-row"><span class="words"><b>Задержка постановки</b><span>Сколько ждать после «Я ушёл»</span></span>${stepper("arm_delay_minutes", d.arm_delay_minutes + " мин")}</div>
      <div class="au-row"><span class="words"><b>Время на «Я дома»</b><span>Сколько ждать, прежде чем движение станет тревогой</span></span>${stepper("entry_delay_minutes", d.entry_delay_minutes + " мин")}</div>
    </div></section>`;
    html += `<section style="display:flex;flex-direction:column;gap:8px"><h2 class="sec-label">Нормы для всех комнат</h2><div class="norm-cards">` +
      NORM_CARDS.filter((n) => d.norms[n.kind]).map((n) => {
        const v = d.norms[n.kind];
        return `<div class="norm-card"><span>${icon(n.icon, 20)}</span><small>${n.name}</small>
          <b>${String(Number(v.value).toFixed(n.digits)).replace(".", ",")}<i>${n.unit}</i></b>${v.same ? "" : `<span class="mixed">в комнатах разные</span>`}
          <div class="btns"><button class="jv-press" aria-label="${n.name}: меньше" data-norm="${n.kind}:-1">${icon("minus", 16)}</button>
          <button class="jv-press" aria-label="${n.name}: больше" data-norm="${n.kind}:1">${icon("plus", 16)}</button></div></div>`;
      }).join("") + `</div><span class="au-note">Ставит одно значение всем комнатам. Свою норму комнате — в её панели на плане. При выходе за норму дом сам включает отопление, кондиционер, вентиляцию или увлажнитель.</span></section>`;
    paint(au.node, html);
  }

  async function loadAutomation() {
    try { au.data = await api("GET", "/api/automation"); } catch (e) { if (e.message !== "locked") toast(e.message, true); }
    drawAutomation();
  }

  async function changeAutomation(key, value, ok) {
    try {
      const result = await api("POST", "/api/automation", { key, value });
      if (result.error) toast(result.error, true);
      else { au.data = result; if (ok) toast(ok); }
    } catch (e) { if (e.message !== "locked") toast(e.message, true); }
    drawAutomation();
  }

  const LIMITS = { light_off_minutes: [1, 60], arm_delay_minutes: [0, 10], entry_delay_minutes: [1, 5] };
  au.node = section("screen-automation");
  au.node.addEventListener("click", (e) => {
    if (e.target.closest("[data-back]")) { window.JV.show("sliders"); return; }
    const st = e.target.closest("[data-step]");
    if (st && au.data) {
      const [key, dir] = st.dataset.step.split(":");
      const value = Math.min(LIMITS[key][1], Math.max(LIMITS[key][0], au.data[key] + parseInt(dir, 10)));
      if (value === au.data[key]) return;
      au.data[key] = value;  // at once; the house confirms
      drawAutomation();
      changeAutomation(key, value);
      return;
    }
    if (e.target.closest("[data-night]") && au.data) {
      au.data.night_light = !au.data.night_light;
      drawAutomation();
      changeAutomation("night_light", au.data.night_light, au.data.night_light ? "Ночная подсветка включена" : "Ночная подсветка выключена");
      return;
    }
    const room = e.target.closest("[data-night-room]");
    if (room && au.data) {
      const slug = room.dataset.nightRoom;
      const rooms = au.data.night_rooms.indexOf(slug) >= 0 ? au.data.night_rooms.filter((r) => r !== slug) : au.data.night_rooms.concat([slug]);
      au.data.night_rooms = rooms;
      drawAutomation();
      changeAutomation("night_rooms", rooms);
      return;
    }
    const norm = e.target.closest("[data-norm]");
    if (norm && au.data) {
      const [kind, dir] = norm.dataset.norm.split(":");
      const card = NORM_CARDS.find((n) => n.kind === kind);
      const v = au.data.norms[kind];
      const value = Math.min(v.max, Math.max(v.min, Math.round((v.value + card.step * parseInt(dir, 10)) * 10) / 10));
      v.value = value; v.same = true;
      drawAutomation();
      changeAutomation("norm_" + kind, value, card.name + ": " + String(value).replace(".", ",") + " " + card.unit + " во всех комнатах");
    }
  });
  au.node.addEventListener("input", (e) => {  // the brightness slider follows the finger
    const input = e.target.closest("[data-range]");
    if (!input) return;
    const s = input.closest(".slider");
    s.querySelector(".fill").style.width = input.value + "%";
    s.querySelector(".thumb").style.left = input.value + "%";
    s.parentNode.querySelector(".pct").textContent = input.value + "%";
  });
  au.node.addEventListener("change", (e) => {
    const range = e.target.closest("[data-range]");
    if (range) { changeAutomation("night_pct", parseInt(range.value, 10)); return; }
    const time = e.target.closest("[data-time-key]");
    if (time && time.value) changeAutomation(time.dataset.timeKey, time.value);
  });
  window.JV.add("automation", { tab: "sliders", back: "sliders", open: () => { drawAutomation(); loadAutomation(); } });

  // ---------------------------------------------------------------- the chat: words or voice, the same Jarvis as at home

  const app = window.JarvisApp && window.JarvisApp.startListening ? window.JarvisApp : null;  // the phone app's microphone
  const CHAT_KEY = "jarvis-panel-chat";
  const ch = { node: null, msgs: [], mode: "idle", awaiting: false };  // mode: idle / listening / thinking
  try { ch.msgs = JSON.parse(window.JV.store.get(CHAT_KEY) || "[]"); } catch (e) { ch.msgs = []; }
  const CHIPS = ["Что дома?", "Я ушёл", "Спокойной ночи", "Выключи везде свет"];

  function remember() { window.JV.store.set(CHAT_KEY, JSON.stringify(ch.msgs.slice(-30))); }

  function chatSkeleton() {
    ch.node.innerHTML = `<span class="ch-glow"></span>
      <header class="ch-head"><button data-chat-back aria-label="Назад">${icon("back", 22)}</button>
        <div class="who"><b>Jarvis</b><span id="chStatus"></span></div>
        <button data-chat-settings aria-label="Настройки" style="color:var(--text2)">${icon("voice", 20)}</button></header>
      <div class="ch-msgs" id="chMsgs"></div>
      <div class="ch-bottom">
        <button class="ch-orb" id="chOrb" aria-label="Говорить с Jarvis"><span class="halo"></span><span class="core"></span><span class="swirl"></span></button>
        <span class="ch-hint" id="chHint"></span>
        <div class="ch-chips">${CHIPS.map((c) => `<button class="jv-press" data-chip="${esc(c)}">${esc(c)}</button>`).join("")}</div>
        <form class="ch-input" id="chForm"><input id="chText" aria-label="Сообщение для Jarvis" placeholder="Напишите Jarvis…" autocomplete="off">
          <button class="jv-press" aria-label="Отправить" id="chSend">${icon("send", 18)}</button></form>
      </div>`;
  }

  function drawChat() {
    const box = ch.node.querySelector("#chMsgs");
    if (!box) return;
    let html = `<span class="ch-day">Сегодня</span>`;
    ch.msgs.slice(-20).forEach((m) => {
      html += `<div class="ch-msg ${m.me ? "me" : "jv"}${m.bad ? " bad" : ""}"><div class="bubble">${m.voice ? `<span class="by-voice">${icon("mic", 12)}Голосом</span>` : ""}${esc(m.text)}</div>` +
        (m.acts && m.acts.length ? `<div class="ch-acts">${m.acts.map((a) => `<span><i>${icon(a.icon, 12)}</i>${esc(a.t)}</span>`).join("")}</div>` : "") + `</div>`;
    });
    if (ch.mode === "thinking") html += `<div class="ch-dots"><i></i><i></i><i></i></div>`;
    box.innerHTML = html;
    box.scrollTop = box.scrollHeight;
    const orb = ch.node.querySelector("#chOrb");
    orb.className = "ch-orb" + (ch.mode === "listening" ? " listening" : "") + (ch.mode === "thinking" ? " thinking" : "") + (app ? "" : " muted");
    orb.setAttribute("aria-pressed", ch.mode === "listening" ? "true" : "false");
    orb.innerHTML = (ch.mode === "listening" ? `<span class="wave"></span><span class="wave"></span>` : "") + `<span class="halo"></span><span class="core"></span><span class="swirl"></span>`;
    const status = ch.node.querySelector("#chStatus");
    const offline = window.JV.house().offline;
    status.textContent = { listening: "Слушает", thinking: "Думает…" }[ch.mode] || (offline ? "Нет связи с домом" : "На связи · дом онлайн");
    status.className = ch.mode !== "idle" ? "live" : "";
    ch.node.querySelector("#chHint").textContent = !app ? "Голос — в приложении Jarvis на телефоне"
      : { listening: "Говори… нажми на шар, чтобы отправить", thinking: "" }[ch.mode] || "Нажми на шар и говори";
    const text = ch.node.querySelector("#chText");
    ch.node.querySelector("#chSend").classList.toggle("ready", !!text.value.trim());
  }

  function add(msg) { ch.msgs.push(msg); remember(); drawChat(); }

  async function ask(path, body, voice) {
    ch.mode = "thinking";
    drawChat();
    try {
      const r = await api("POST", path, body);
      if (r.error) add({ me: false, text: r.error, bad: true });
      else {
        if (voice) add({ me: true, text: r.heard, voice: true });
        add({ me: false, text: r.response, acts: r.acts });
        if (r.acts && r.acts.length) window.JV.refresh();  // the house changed: the plan and the cards too
      }
    } catch (e) {
      if (e.message !== "locked") add({ me: false, text: "Джарвис не ответил: " + e.message, bad: true });
    }
    ch.mode = "idle";
    drawChat();
  }

  function say(text) {
    text = (text || "").trim();
    if (!text || ch.mode !== "idle") return;
    add({ me: true, text });
    ask("/api/chat", { message: text });
  }

  // what the phone app's microphone says back (MainActivity)
  window.onListening = () => { ch.mode = "listening"; drawChat(); };
  window.onVoice = (b64) => { if (ch.awaiting) { ch.awaiting = false; ask("/api/voice", { audio: b64 }, true); } };
  window.onVoiceError = (msg) => { ch.mode = "idle"; drawChat(); toast(msg, true); };

  ch.node = section("screen-chat");
  chatSkeleton();
  ch.node.addEventListener("click", (e) => {
    if (e.target.closest("[data-chat-back]")) { window.JV.show("home"); return; }
    if (e.target.closest("[data-chat-settings]")) { window.JV.show("sliders"); return; }
    const chip = e.target.closest("[data-chip]");
    if (chip) { say(chip.dataset.chip); return; }
    if (e.target.closest("#chOrb")) {
      if (!app) { toast("Голос работает в приложении Jarvis на телефоне"); return; }
      if (ch.mode === "idle") app.startListening();  // onListening once the microphone is open
      else if (ch.mode === "listening") { ch.awaiting = true; ch.mode = "thinking"; drawChat(); app.stopListening(); }  // -> onVoice
    }
  });
  ch.node.addEventListener("submit", (e) => {
    e.preventDefault();
    const input = ch.node.querySelector("#chText");
    const text = input.value;
    input.value = "";
    say(text);
  });
  ch.node.addEventListener("input", () => {  // only the send button reacts to typing
    ch.node.querySelector("#chSend").classList.toggle("ready", !!ch.node.querySelector("#chText").value.trim());
  });
  // ---------------------------------------------------------------- the alarm: over every screen while a danger lasts

  const al = { node: null, list: [], dismissed: {}, checking: false, shownKey: "" };
  const alarmKey = (a) => a.key + "@" + a.since;

  async function checkAlarm() {
    const h = window.JV.house();
    const danger = (h.rooms || []).some((r) => Object.keys(r.dangers).some((k) => r.dangers[k]));
    if (!danger) { if (al.list.length) { al.list = []; drawAlarm(); } return; }
    if (al.checking) return;
    al.checking = true;
    try { al.list = (await api("GET", "/api/alerts")).alerts; } catch (e) { /* next refresh tries again */ }
    al.checking = false;
    drawAlarm();
  }

  function drawAlarm() {
    const shown = al.list.filter((a) => !al.dismissed[alarmKey(a)]);
    const key = shown.map(alarmKey).join("|") + "|" + shown.map((a) => a.acts.length).join(",");
    if (key === al.shownKey) return;  // the same alarm: its animations keep running
    al.shownKey = key;
    if (!shown.length) { al.node.hidden = true; al.node.innerHTML = ""; return; }
    const a = shown[0];
    const since = new Date(a.since);
    const clock = pad(since.getHours()) + ":" + pad(since.getMinutes()) + ":" + pad(since.getSeconds());
    const more = shown.length > 1 ? ` · ещё ${shown.length - 1}` : "";
    const action = a.kind === "safety"
      ? `<button class="call jv-press" data-alarm-disarm>${icon("shield", 20)}Снять охрану</button>`
      : `<a class="call jv-press" href="tel:112">${icon("phone", 20)}Позвонить 112</a>`;
    al.node.innerHTML = `<span class="glow"></span><div class="body" role="alertdialog" aria-labelledby="alTitle">
      <div class="top"><b><i></i>Тревога${more}</b><span>${clock}</span></div>
      <div class="what"><span class="badge"><span class="ring"></span><span class="ring"></span><span class="disc">${icon(a.icon, 36)}</span></span>
        <h1 id="alTitle">${esc(a.title)}<br>${esc(a.where)}</h1><p class="desc">Датчик всё ещё срабатывает · с ${clock.slice(0, 5)}</p></div>
      ${a.acts.length ? `<div class="did"><span>Дом уже сделал</span>${a.acts.map((x, i) =>
        `<div class="act" style="animation-delay:${0.2 + i * 0.15}s"><i>${icon("check", 16)}</i><b>${esc(x.t)}</b><span>${esc(x.at)}</span></div>`).join("")}</div>` : ""}
      <div class="place"><div class="mini" id="alMini"></div><div class="txt"><b>${esc(a.room)}</b><span>${esc(a.advice)}</span></div></div>
      <div class="btns">${action}<button class="ok jv-press" data-alarm-ok>Понятно</button></div></div>`;
    al.node.hidden = false;
    const plan = document.getElementById("plan");  // the house's own plan, small, the alarm's room red on it
    if (plan) {
      const copy = plan.cloneNode(true);
      copy.removeAttribute("id");
      const w = parseFloat(plan.style.width) || 350, hgt = parseFloat(plan.style.height) || 540;
      const holder = document.createElement("div");
      holder.style.width = w + "px";
      holder.style.height = hgt + "px";
      const box = al.node.querySelector("#alMini");  // the phone's small frame, or the tablet's big card
      const bw = box.offsetWidth || 133, bh = box.offsetHeight || 205, k = Math.min(bw / w, bh / hgt);
      holder.style.transform = `translate(${((bw - w * k) / 2).toFixed(1)}px, ${((bh - hgt * k) / 2).toFixed(1)}px) scale(${k.toFixed(3)})`;
      holder.appendChild(copy);
      box.appendChild(holder);
    }
  }

  al.node = document.createElement("div");
  al.node.className = "alarm-screen";
  al.node.hidden = true;
  document.getElementById("app").appendChild(al.node);
  al.node.addEventListener("click", async (e) => {
    if (e.target.closest("[data-alarm-ok]")) {
      al.list.forEach((a) => { al.dismissed[alarmKey(a)] = true; });  // until a new danger begins
      drawAlarm();
      return;
    }
    if (e.target.closest("[data-alarm-disarm]")) {
      try { await api("POST", "/api/security", { action: "disarm" }); toast("Охрана снята"); } catch (err) { if (err.message !== "locked") toast(err.message, true); }
      window.JV.refresh();
    }
  });
  window.JV.listen(checkAlarm);
  const backBefore = window.jarvisBack;  // the phone's back button on the alarm: "Понятно"
  window.jarvisBack = () => {
    if (!al.node.hidden) { al.node.querySelector("[data-alarm-ok]").click(); return true; }
    return backBefore ? backBefore() : false;
  };

  window.JV.add("chat", {
    tab: "home", back: "home",
    open: () => drawChat(),
    leave: () => { if (ch.mode === "listening" && app) { app.cancelListening(); ch.mode = "idle"; } },
  });
})();
