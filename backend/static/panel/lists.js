/* Jarvis panel - reminders and the shopping list (the design's «Напоминания», «Покупки» and the
   tablet's two columns side by side), and their chips under the home screen's title.
   The backend is the same as by voice: /api/reminders, /api/shopping (app/panel.py). Plain ES2017. */
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
  function layer(cls) {
    const node = document.createElement("div");
    node.className = cls;
    document.getElementById("app").appendChild(node);
    return node;
  }
  function paint(node, html) {
    if (node._html === html) return;
    node._html = html;
    node.innerHTML = html;
  }
  const plural = (n, one, few, many) => {
    const t = n % 10, h = n % 100;
    return n + " " + (t === 1 && h !== 11 ? one : t >= 2 && t <= 4 && (h < 12 || h > 14) ? few : many);
  };
  const pad = (n) => (n < 10 ? "0" : "") + n;
  const WD = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"];
  const MON = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];
  const DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
  const DAY_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
  const hhmm = (d) => pad(d.getHours()) + ":" + pad(d.getMinutes());
  const dayLabel = (d) => WD[d.getDay()] + ", " + d.getDate() + " " + MON[d.getMonth()];
  const isoDate = (d) => d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
  const startOf = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const owner = () => (window.JV.owner ? window.JV.owner() : "");
  const initial = (name) => esc((name || "?").slice(0, 1).toUpperCase());

  const L = {
    rem: null, shop: null, failed: false, filter: "mine",
    hidden: {}, ticked: {}, gone: {},  // cancelled or bought, waiting for "Вернуть" to run out
    snack: null, ed: null, ask: null, polls: 0,
  };

  async function loadRem() {
    try { L.rem = await api("GET", "/api/reminders"); L.failed = false; } catch (e) { if (e.message !== "locked") L.failed = true; }
    drawAll();
  }
  async function loadShop() {
    try { L.shop = (await api("GET", "/api/shopping")).items; L.failed = false; } catch (e) { if (e.message !== "locked") L.failed = true; }
    drawAll();
  }
  const loadBoth = () => Promise.all([loadRem(), loadShop()]);

  // ---------------------------------------------------------------- what the reminders say

  const isMine = (r) => !r.for || r.for === owner();  // mine, and no one's in particular
  function visibleReminders() {
    return ((L.rem && L.rem.reminders) || []).filter((r) => !L.hidden[r.id] && (L.filter === "all" || !owner() || isMine(r)));
  }
  function todayCount() {
    const end = new Date(startOf(new Date()).getTime() + 86400000);
    return ((L.rem && L.rem.reminders) || []).filter((r) => !L.hidden[r.id] && (!owner() || isMine(r)) && new Date(r.due) < end).length;
  }
  const liveTimers = () => ((L.rem && L.rem.timers) || []).filter((t) => !L.hidden[t.id]);

  /** "каждый день в 22:00" -> "каждый день"; "по понедельникам и средам в 9:00" -> "пн, ср". */
  function repeatShort(text) {
    let r = String(text || "").replace(/ в \d{1,2}:\d\d$/, "");
    const days = { "понедельникам": "пн", "вторникам": "вт", "средам": "ср", "четвергам": "чт", "пятницам": "пт", "субботам": "сб", "воскресеньям": "вс" };
    const found = Object.keys(days).filter((k) => r.indexOf(k) >= 0);
    if (found.length) r = found.map((k) => days[k]).join(", ");
    return r;
  }
  /** "Таймер на 8 минут: паста" -> "Паста · 8 минут"; "Таймер на 10 минут" as it is. */
  function timerLabel(text) {
    const m = /^Таймер на (.+?): (.+)$/.exec(text || "");
    return m ? m[2].slice(0, 1).toUpperCase() + m[2].slice(1) + " · " + m[1] : text;
  }
  function left(due) {
    const s = Math.max(0, Math.round((new Date(due) - Date.now()) / 1000));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    return (h ? h + ":" + pad(m) : pad(m)) + ":" + pad(s % 60);
  }
  const RING = 138.2;
  function ringOffset(t) {
    const total = new Date(t.due) - new Date(t.created || t.due), rest = new Date(t.due) - Date.now();
    return (RING * (1 - Math.max(0, Math.min(1, total > 0 ? rest / total : 0)))).toFixed(1);
  }

  function badge(r) {
    if (!r.for) return `<span class="ls-who all" title="Все" aria-label="Для всех">${icon("users", 14)}</span>`;
    if (r.for === owner()) return "";
    return `<span class="ls-who" title="${esc(r.for)}" aria-label="Напоминание: ${esc(r.for)}">${initial(r.for)}</span>`;
  }

  function row(r, later, last) {
    const d = new Date(r.due);
    return `<div class="ls-row${last ? " last" : ""}" data-row="${r.id}"><span class="ls-under">${icon("close", 16)}Отменить</span>
      <div class="ls-swipe" data-swipe="${r.id}"><span class="ls-time"><b>${hhmm(d)}</b>${later ? `<small>${dayLabel(d)}</small>` : ""}</span>
        <span class="ls-text"><b>${esc(r.text)}</b>${r.repeat ? `<span class="rep">${icon("repeat", 13)}${esc(repeatShort(r.repeat))}</span>` : ""}</span>
        ${badge(r)}<button class="ls-x jv-press" data-cancel="${r.id}" aria-label="Отменить: ${esc(r.text)}, ${hhmm(d)}">${icon("close", 18)}</button></div></div>`;
  }

  function topRow(other) {
    return `<div class="ls-top"><button class="sub-back" data-ls-home>${icon("back", 20)}Дом</button>
      <button class="ls-link jv-press" data-lists-go="${other.go}"><i>${icon(other.icon, 18)}</i>${esc(other.text)}</button></div>`;
  }

  const rm = { node: null };

  function drawReminders() {
    const shopN = L.shop ? L.shop.filter((i) => !L.gone[i.item]).length : null;
    let html = topRow({ go: "shopping", icon: "cart", text: "Покупки" + (shopN ? " · " + shopN : "") }) +
      `<div class="ls-head"><h1>Напоминания</h1><button class="ls-add jv-press" data-ls-new>${icon("plus", 18)}Добавить</button></div>`;
    if (!L.rem) { paint(rm.node, html + `<p class="scr-empty">${L.failed ? "Нет связи с домом" : "Загружаю…"}</p>`); return; }
    if (L.failed) html += `<p class="ls-off">Нет связи с домом — показываю последнее</p>`;
    const all = (L.rem.reminders || []).filter((r) => !L.hidden[r.id]);
    if (owner()) {
      const mine = all.filter(isMine).length;
      html += `<div class="ls-seg" role="radiogroup" aria-label="Чьи напоминания">` +
        [["mine", "Мои · " + mine], ["all", "Все · " + all.length]].map((o) =>
          `<button role="radio" aria-checked="${L.filter === o[0]}" class="${L.filter === o[0] ? "on" : ""}" data-filter="${o[0]}">${o[1]}</button>`).join("") + `</div>`;
    }
    const timers = liveTimers();
    if (timers.length) {
      html += `<section class="ls-sec"><h2 class="sec-label">Таймеры</h2>` + timers.map((t) =>
        `<div class="ls-timer"><span class="ring"><svg viewBox="0 0 52 52" width="52" height="52" aria-hidden="true">
          <circle cx="26" cy="26" r="22" class="jv-ring track"/><circle cx="26" cy="26" r="22" class="jv-ring run" stroke-dasharray="${RING}" style="stroke-dashoffset:${ringOffset(t)}" data-ring="${t.id}"/></svg>
          <i>${icon("timer", 20)}</i></span>
          <span class="words"><small>${esc(timerLabel(t.text))}</small><b role="timer" data-left="${t.id}">${left(t.due)}</b></span>
          <button class="ls-cancel jv-press" data-cancel="${t.id}" aria-label="Отменить таймер: ${esc(t.text)}">Отменить</button></div>`).join("") + `</section>`;
    }
    const items = visibleReminders();
    const today = startOf(new Date()), tomorrow = new Date(today.getTime() + 86400000), after = new Date(today.getTime() + 2 * 86400000);
    const groups = [["Сегодня", today, tomorrow, false], ["Завтра", tomorrow, after, false], ["Позже", after, null, true]];
    groups.forEach((g) => {
      const list = items.filter((r) => { const d = new Date(r.due); return d >= g[1] && (!g[2] || d < g[2]); });
      if (!list.length) return;
      html += `<section class="ls-sec"><div class="ls-day"><h2>${g[0]} <span>· ${list.length}</span></h2>${g[3] ? "" : `<span class="mono">${dayLabel(g[1])}</span>`}</div>
        <div class="ls-card">${list.map((r, i) => row(r, g[3], i === list.length - 1)).join("")}</div></section>`;
    });
    if (!items.length && !timers.length) {
      html += `<div class="ls-empty">${icon("bell", 28)}<b>Напоминаний нет</b><span>Скажи: «Джарвис, напомни в 8 выключить духовку» — или добавь здесь.</span></div>`;
    } else if (items.length) {
      html += `<p class="ls-hint">Смахните влево, чтобы отменить</p>`;
    }
    paint(rm.node, html);
  }

  // live countdowns without redrawing the screen (a redraw would drop a swipe in progress)
  setInterval(() => {
    if (!rm.node.offsetParent || !liveTimers().length) return;
    let over = false;
    liveTimers().forEach((t) => {
      const b = rm.node.querySelector(`[data-left="${t.id}"]`), ring = rm.node.querySelector(`[data-ring="${t.id}"]`);
      if (b) b.textContent = left(t.due);
      if (ring) ring.style.strokeDashoffset = ringOffset(t);
      if (new Date(t.due) <= Date.now()) over = true;
    });
    if (over) setTimeout(loadRem, 1500);  // it rang: gone from the list at home
  }, 1000);

  // ---------------------------------------------------------------- cancel / bought, with "Вернуть" for 5 s

  const sn = { node: layer("ls-snack-wrap") };

  function drawSnack() {
    const s = L.snack;
    paint(sn.node, s ? `<div class="ls-snack" role="status"><div><span>${esc(s.text)}</span><button class="jv-press" data-undo>Вернуть</button></div><i></i></div>` : "");
  }
  function snack(text, commit, undo) {
    if (L.snack) { clearTimeout(L.snack.timer); L.snack.commit(); }  // the one before: done now
    const s = { text, commit, undo };
    s.timer = setTimeout(() => { if (L.snack === s) { L.snack = null; drawSnack(); } commit(); }, 5000);
    L.snack = s;
    sn.node._html = null;  // the bar's countdown starts again
    drawSnack();
  }
  sn.node.addEventListener("click", (e) => {
    if (!e.target.closest("[data-undo]") || !L.snack) return;
    const s = L.snack;
    clearTimeout(s.timer);
    L.snack = null;
    drawSnack();
    s.undo();
  });

  function cancelReminder(id) {
    const all = ((L.rem && L.rem.reminders) || []).concat((L.rem && L.rem.timers) || []);
    const r = all.find((x) => String(x.id) === String(id));
    if (!r) return;
    const rowNode = rm.node.querySelector(`[data-row="${r.id}"]`);
    if (rowNode) rowNode.classList.add("going");
    setTimeout(() => {
      L.hidden[r.id] = true;
      drawAll();
      snack((r.kind === "timer" ? "Таймер отменён · " : "Отменено · ") + timerLabel(r.text), async () => {
        try { L.rem = await api("DELETE", "/api/reminders/" + r.id); } catch (e) { if (e.message !== "locked") toast(e.message, true); }
        delete L.hidden[r.id];
        drawAll();
      }, () => { delete L.hidden[r.id]; drawAll(); });
    }, rowNode ? 260 : 0);
  }

  // a finger sliding a row left: past 80 points it's cancelled
  let sw = null;
  rm.node = section("screen-reminders");
  rm.node.addEventListener("pointerdown", (e) => {
    const row = e.target.closest("[data-swipe]");
    if (!row || e.target.closest("button")) return;
    sw = { row, id: row.dataset.swipe, x: e.clientX, y: e.clientY, dx: 0, on: false };
  });
  rm.node.addEventListener("pointermove", (e) => {
    if (!sw) return;
    const k = sw.row.getBoundingClientRect().width / sw.row.offsetWidth || 1;
    const dx = (e.clientX - sw.x) / k, dy = (e.clientY - sw.y) / k;
    if (!sw.on && Math.abs(dy) > 10 && Math.abs(dy) > Math.abs(dx)) { sw = null; return; }  // scrolling
    if (Math.abs(dx) > 8) sw.on = true;
    if (!sw.on) return;
    sw.dx = Math.min(0, dx);
    sw.row.style.transition = "none";
    sw.row.style.transform = `translateX(${sw.dx}px)`;
  });
  function swipeEnd() {
    if (!sw) return;
    const s = sw;
    sw = null;
    s.row.style.transition = "";
    if (s.dx < -80) { s.row.style.transform = "translateX(-110%)"; cancelReminder(s.id); }
    else s.row.style.transform = "";
  }
  rm.node.addEventListener("pointerup", swipeEnd);
  rm.node.addEventListener("pointercancel", swipeEnd);
  rm.node.addEventListener("click", (e) => {
    const t = e.target;
    if (t.closest("[data-ls-home]")) { window.JV.show("home"); return; }
    if (t.closest("[data-ls-new]")) { openSheet(); return; }
    const f = t.closest("[data-filter]");
    if (f) { L.filter = f.dataset.filter; drawReminders(); return; }
    const c = t.closest("[data-cancel]");
    if (c) cancelReminder(c.dataset.cancel);
  });

  // ---------------------------------------------------------------- the sheet: a new reminder or a timer

  const sh = { node: layer("ls-sheet-wrap") };
  const REL = [[5, "через 5 мин"], [10, "10 мин"], [15, "15 мин"], [30, "30 мин"], [60, "1 час"]];
  const TIMERS = [1, 3, 5, 10, 15, 30, 45, 60];
  const REPEATS = [["none", "Не повторять"], ["daily", "Каждый день"], ["weekdays", "По будням"], ["weekends", "По выходным"], ["days", "Дни недели…"]];
  /** "на 10 минут", "через 1 минуту", "на 2 часа" - the accusative after на / через. */
  const minutesAcc = (m) => (m % 60 === 0 ? plural(m / 60, "час", "часа", "часов") : plural(m, "минуту", "минуты", "минут"));

  function residents() {
    const p = window.JV.people && window.JV.people();
    return p ? p.residents.map((r) => r.name) : [];
  }

  function openSheet() {
    const now = new Date();
    const next = new Date(now.getFullYear(), now.getMonth(), now.getDate(), now.getHours() + 1, 0);
    L.ed = { mode: "reminder", text: "", rel: 10, day: null, date: isoDate(next), time: hhmm(next), repeat: "none", days: {},
             whom: owner(), tmin: 10, tname: "", busy: false, err: "" };
    if (window.JV.loadPeople && !(window.JV.people && window.JV.people())) window.JV.loadPeople(drawSheet);
    drawSheet();
    setTimeout(() => { const i = sh.node.querySelector("#lsText"); if (i) i.focus({ preventScroll: true }); }, 380);
  }
  function closeSheet() { L.ed = null; drawSheet(); }

  /** When it would ring: a Date, or null when the choice can't be one (a time already passed). */
  function whenOf(ed) {
    if (ed.rel) return new Date(Date.now() + ed.rel * 60000);
    const [h, m] = ed.time.split(":").map(Number);
    const base = ed.day === "tomorrow" ? new Date(startOf(new Date()).getTime() + 86400000)
      : ed.day === "date" ? new Date(ed.date + "T00:00") : startOf(new Date());
    return new Date(base.getFullYear(), base.getMonth(), base.getDate(), h, m);
  }
  function repeatValue(ed) {
    if (ed.rel || ed.repeat === "none") return "";
    if (ed.repeat === "days") return DAY_KEYS.filter((k) => ed.days[k]).join(",");
    return ed.repeat;
  }
  /** The line over "Сохранить": what's missing, or what will happen. */
  function sheetHint(ed) {
    if (ed.err) return { text: ed.err, bad: true };
    if (ed.mode === "timer") return { text: "Таймер на " + minutesAcc(ed.tmin) };
    if (!ed.text.trim()) return { text: "Напишите, о чём напомнить", block: true };
    if (!ed.rel && ed.repeat === "days" && !repeatValue(ed)) return { text: "Выберите дни недели", block: true };
    const at = whenOf(ed);
    if (!ed.rel && ed.repeat === "none" && at <= new Date()) return { text: "Это время уже прошло — выберите позже", block: true, bad: true };
    if (ed.rel) return { text: "Напомню через " + (ed.rel === 60 ? "час" : minutesAcc(ed.rel)) };
    const rep = REPEATS.find((r) => r[0] === ed.repeat);
    if (ed.repeat !== "none") return { text: (ed.repeat === "days" ? "По " + DAY_KEYS.filter((k) => ed.days[k]).map((k) => DAY_SHORT[DAY_KEYS.indexOf(k)].toLowerCase()).join(", ") : rep[1]) + " в " + ed.time };
    const day = ed.day === "tomorrow" ? "завтра" : ed.day === "date" ? dayLabel(at) : "сегодня";
    return { text: "Напомню " + day + " в " + ed.time };
  }

  const chip = (on, attr, label, cls) => `<button class="ls-chip${cls ? " " + cls : ""}${on ? " on" : ""} jv-press" aria-pressed="${on}" ${attr}>${label}</button>`;

  function drawSheet() {
    const ed = L.ed;
    if (!ed) { paint(sh.node, ""); return; }
    const tabs = `<div class="ls-tabs" role="radiogroup" aria-label="Что добавить">` +
      [["reminder", "bell", "Напоминание"], ["timer", "timer", "Таймер"]].map((t) =>
        `<button role="radio" aria-checked="${ed.mode === t[0]}" class="${ed.mode === t[0] ? "on" : ""}" data-mode="${t[0]}">${icon(t[1], 16)}${t[2]}</button>`).join("") + `</div>`;
    let body = "";
    if (ed.mode === "reminder") {
      const fixed = !ed.rel;
      const dateText = ed.day === "date" ? dayLabel(new Date(ed.date + "T00:00")) : "Дата";
      body = `<label class="se-field"><span>О чём напомнить</span><input id="lsText" placeholder="Например, выключить духовку" maxlength="120" autocomplete="off" value="${esc(ed.text)}"></label>
        <div class="ls-group"><span class="sec-label">Когда</span><div class="ls-chips">${REL.map((r) => chip(ed.rel === r[0], `data-rel="${r[0]}"`, r[1])).join("")}</div>
          <div class="ls-when"><div class="days">${chip(ed.day === "today", 'data-day="today"', "Сегодня", "sq")}${chip(ed.day === "tomorrow", 'data-day="tomorrow"', "Завтра", "sq")}
            <label class="ls-chip sq jv-press${ed.day === "date" ? " on" : ""}">${icon("calendar", 15)}${esc(dateText)}<input type="date" id="lsDate" min="${isoDate(new Date())}" value="${ed.date}" aria-label="Дата"></label></div>
            <div class="ls-step${fixed ? " on" : ""}" role="group" aria-label="Время"><button class="jv-press" data-time="-15" aria-label="Раньше на 15 минут">${icon("minus", 16)}</button>
              <label class="mono">${ed.time}<input type="time" id="lsTime" value="${ed.time}" aria-label="Время"></label>
              <button class="jv-press" data-time="15" aria-label="Позже на 15 минут">${icon("plus", 16)}</button></div></div></div>
        <div class="ls-group${fixed ? "" : " off"}"><span class="sec-label">Повтор<em> — только для напоминаний ко времени</em></span>
          <div class="ls-chips">${REPEATS.map((r) => chip(ed.repeat === r[0], `data-repeat="${r[0]}"${fixed ? "" : " disabled"}`, r[1])).join("")}</div>
          ${fixed && ed.repeat === "days" ? `<div class="ls-week">${DAY_KEYS.map((k, i) => chip(!!ed.days[k], `data-wday="${k}"`, DAY_SHORT[i], "sq")).join("")}</div>` : ""}</div>
        <div class="ls-group"><span class="sec-label">Для кого</span><div class="ls-chips" role="radiogroup" aria-label="Для кого">` +
          (owner() ? whomChip(ed.whom === owner(), owner(), "Мне", true) : "") +
          residents().filter((n) => n !== owner()).map((n) => whomChip(ed.whom === n, n, n, false)).join("") +
          `<button role="radio" aria-checked="${ed.whom === ""}" class="ls-whom${ed.whom === "" ? " on" : ""} jv-press" data-whom=""><span class="av all">${icon("users", 15)}</span>Всем</button></div></div>`;
    } else {
      const at = new Date(Date.now() + ed.tmin * 60000);
      body = `<div class="ls-big"><div><button class="jv-press" data-tmin="-1" aria-label="Минус минута">${icon("minus", 20)}</button>
          <span class="mono" aria-live="polite">${ed.tmin >= 60 ? Math.floor(ed.tmin / 60) + ":" + pad(ed.tmin % 60) + ":00" : pad(ed.tmin) + ":00"}</span>
          <button class="jv-press" data-tmin="1" aria-label="Плюс минута">${icon("plus", 20)}</button></div><small>Прозвенит в ${hhmm(at)}</small></div>
        <div class="ls-grid">${TIMERS.map((m) => chip(ed.tmin === m, `data-tset="${m}"`, m === 60 ? "1 час" : m + " мин", "sq tall")).join("")}</div>
        <label class="se-field"><span>Название — необязательно</span><input id="lsName" placeholder="Например, паста" maxlength="40" autocomplete="off" value="${esc(ed.tname)}"></label>`;
    }
    const hint = sheetHint(ed);
    paint(sh.node, `<button class="scrim" data-sheet-close aria-label="Закрыть"></button>
      <div class="ls-sheet" role="dialog" aria-label="${ed.mode === "timer" ? "Новый таймер" : "Новое напоминание"}">
        <div class="ls-sheet-top"><span class="grab"></span><div class="row">${tabs}<button class="ls-close jv-press" data-sheet-close aria-label="Закрыть">${icon("close", 20)}</button></div></div>
        <div class="ls-sheet-body">${body}</div>
        <div class="ls-sheet-bar"><span aria-live="polite" id="lsHint" class="${hint.bad ? "bad" : ""}">${esc(hint.text)}</span>
          <button class="save jv-press" data-save ${hint.block || ed.busy ? "disabled" : ""}>${ed.mode === "timer" ? "Запустить таймер" : "Сохранить"}</button></div></div>`);
  }
  function whomChip(on, name, label, me) {
    return `<button role="radio" aria-checked="${on}" class="ls-whom${on ? " on" : ""} jv-press" data-whom="${esc(name)}"><span class="av${me ? " me" : ""}">${initial(name)}</span>${esc(label)}</button>`;
  }
  function refreshHint() {  // typing: only the line and the button, the input keeps its caret
    const hint = sheetHint(L.ed), line = sh.node.querySelector("#lsHint"), btn = sh.node.querySelector("[data-save]");
    if (line) { line.textContent = hint.text; line.className = hint.bad ? "bad" : ""; }
    if (btn) btn.disabled = !!(hint.block || L.ed.busy);
  }

  async function saveSheet() {
    const ed = L.ed;
    ed.busy = true;
    refreshHint();
    const body = { who: owner() };
    if (ed.mode === "timer") Object.assign(body, { kind: "timer", in_seconds: ed.tmin * 60, text: ed.tname.trim() });
    else {
      Object.assign(body, { kind: "reminder", text: ed.text.trim() });
      if (ed.rel) body.in_seconds = ed.rel * 60;
      else { const at = whenOf(ed); body.at = isoDate(at) + "T" + hhmm(at); }
      const rep = repeatValue(ed);
      if (rep) body.repeat = rep;
      if (ed.whom !== owner()) body.for = ed.whom || "всем";
    }
    try {
      const r = await api("POST", "/api/reminders", body);
      if (r.error) { ed.busy = false; ed.err = r.error; drawSheet(); return; }
      L.rem = { reminders: r.reminders, timers: r.timers };
      closeSheet();
      toast(ed.mode === "timer" ? "Таймер запущен" : "Напоминание добавлено");
      loadRem();  // the whole house's, not only this phone's
    } catch (e) {
      ed.busy = false;
      if (e.message !== "locked") { ed.err = e.message; drawSheet(); }
    }
  }

  sh.node.addEventListener("click", (e) => {
    const t = e.target, ed = L.ed;
    if (!ed) return;
    if (t.closest("[data-sheet-close]")) { closeSheet(); return; }
    if (t.closest("[data-save]")) { saveSheet(); return; }
    const pick = (sel) => t.closest(sel);
    let p;
    ed.err = "";
    if ((p = pick("[data-mode]"))) ed.mode = p.dataset.mode;
    else if ((p = pick("[data-rel]"))) { ed.rel = Number(p.dataset.rel); ed.day = null; ed.repeat = "none"; }
    else if ((p = pick("[data-day]"))) { ed.day = p.dataset.day; ed.rel = null; }
    else if ((p = pick("[data-time]"))) {
      const [h, m] = ed.time.split(":").map(Number);
      const mins = ((h * 60 + m + Number(p.dataset.time)) % 1440 + 1440) % 1440;
      ed.time = pad(Math.floor(mins / 60)) + ":" + pad(mins % 60);
      if (ed.rel) { ed.rel = null; ed.day = "today"; }
    }
    else if ((p = pick("[data-repeat]"))) ed.repeat = p.dataset.repeat;
    else if ((p = pick("[data-wday]"))) ed.days[p.dataset.wday] = !ed.days[p.dataset.wday];
    else if ((p = pick("[data-whom]"))) ed.whom = p.dataset.whom;
    else if ((p = pick("[data-tmin]"))) ed.tmin = Math.max(1, Math.min(180, ed.tmin + Number(p.dataset.tmin)));
    else if ((p = pick("[data-tset]"))) ed.tmin = Number(p.dataset.tset);
    else return;
    drawSheet();
  });
  sh.node.addEventListener("input", (e) => {
    if (!L.ed) return;
    if (e.target.id === "lsText") { L.ed.text = e.target.value; L.ed.err = ""; refreshHint(); }
    if (e.target.id === "lsName") L.ed.tname = e.target.value;
  });
  sh.node.addEventListener("change", (e) => {
    const ed = L.ed;
    if (!ed) return;
    if (e.target.id === "lsDate" && e.target.value) { ed.date = e.target.value; ed.day = "date"; ed.rel = null; drawSheet(); }
    if (e.target.id === "lsTime" && e.target.value) { ed.time = e.target.value; if (ed.rel) { ed.rel = null; ed.day = "today"; } drawSheet(); }
  });
  sh.node.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && e.target.id === "lsText" && !sheetHint(L.ed).block) saveSheet();
  });

  // ---------------------------------------------------------------- the shopping list

  const sp = { node: null };

  function drawShopping() {
    const items = (L.shop || []).filter((i) => !L.gone[i.item]);
    const remN = L.rem ? todayCount() : null;
    let html = `<div class="ls-scroll">` + topRow({ go: "reminders", icon: "bell", text: "Напоминания" + (remN ? " · " + remN : "") }) +
      `<div class="ls-head end"><div><h1>Покупки</h1><span>Общий список · ${plural(items.length, "позиция", "позиции", "позиций")}</span></div>
        ${items.length ? `<button class="ls-clear jv-press" data-clear aria-label="Очистить список">Очистить</button>` : ""}</div>`;
    if (!L.shop) html += `<p class="scr-empty">${L.failed ? "Нет связи с домом" : "Загружаю…"}</p>`;
    else if (!items.length) html += `<div class="ls-empty">${icon("cart", 28)}<b>Список пуст</b><span>Скажи: «Джарвис, добавь молоко».</span></div>`;
    else {
      if (L.failed) html += `<p class="ls-off">Нет связи с домом — показываю последнее</p>`;
      html += `<div class="ls-card">` + items.map((i, n) => {
        const done = !!L.ticked[i.item];
        const by = [i.who, i.via === "voice" ? "голосом" : ""].filter(Boolean).join(" · ");
        return `<div class="ls-item${done ? " done" : ""}${n === items.length - 1 ? " last" : ""}" data-item="${esc(i.item)}">
          <button role="checkbox" aria-checked="${done}" aria-label="${done ? "Куплено" : "Отметить купленным"}: ${esc(i.item)}" data-tick="${esc(i.item)}"><span class="box">${done ? `<i>${icon("check", 15)}</i>` : ""}</span></button>
          <span class="words"><b>${esc(i.item)}${done ? "<s></s>" : ""}</b>${by ? `<small>${esc(by)}</small>` : ""}</span></div>`;
      }).join("") + `</div>`;
    }
    html += `</div><form class="ls-bar" data-shop-add><span>Можно несколько через запятую: молоко, хлеб, сыр</span>
      <div><input id="lsShop" aria-label="Добавить в список" placeholder="Добавить в список" maxlength="200" autocomplete="off">
        <button class="jv-press" aria-label="Добавить" id="lsShopAdd">${icon("plus", 20)}</button></div></form>`;
    if (L.ask) html += window.JV.scenesData.askHtml(L.ask);
    const typed = sp.node.querySelector("#lsShop"), value = typed ? typed.value : "", focused = typed && document.activeElement === typed;
    paint(sp.node, html);
    const input = sp.node.querySelector("#lsShop");
    if (input && value) input.value = value;
    if (input && focused) input.focus();
    if (input) sp.node.querySelector("#lsShopAdd").classList.toggle("ready", !!input.value.trim());
  }

  function tick(item) {
    if (L.ticked[item]) return;
    L.ticked[item] = true;
    drawShopping();
    setTimeout(() => {
      const node = sp.node.querySelector(`[data-item="${CSS.escape(item)}"]`);
      if (node) node.classList.add("going");
      setTimeout(() => {
        L.gone[item] = true;
        delete L.ticked[item];
        drawAll();
        snack("Куплено: " + item, async () => {
          try { L.shop = (await api("POST", "/api/shopping", { action: "bought", items: [item] })).items; } catch (e) { if (e.message !== "locked") toast(e.message, true); }
          delete L.gone[item];
          drawAll();
        }, () => { delete L.gone[item]; drawAll(); });
      }, 320);
    }, 650);
  }

  sp.node = section("screen-shopping");
  sp.node.addEventListener("click", async (e) => {
    const t = e.target;
    if (t.closest("[data-ls-home]")) { window.JV.show("home"); return; }
    const tk = t.closest("[data-tick]");
    if (tk) { tick(tk.dataset.tick); return; }
    if (t.closest("[data-clear]")) {
      const n = (L.shop || []).filter((i) => !L.gone[i.item]).length;
      L.ask = { title: "Очистить список?", yes: "Очистить", text: "Удалю " + plural(n, "позицию", "позиции", "позиций") + ". Список общий — он очистится у всех жильцов.",
        act: async () => {
          try { L.shop = (await api("POST", "/api/shopping", { action: "clear" })).items; toast("Список очищен"); } catch (err) { if (err.message !== "locked") toast(err.message, true); }
          drawAll();
        } };
      drawShopping();
      return;
    }
    if (t.closest("[data-ask-no]")) { L.ask = null; drawShopping(); return; }
    if (t.closest("[data-ask-yes]") && L.ask) { const a = L.ask; L.ask = null; drawShopping(); a.act(); }
  });
  sp.node.addEventListener("input", (e) => {
    if (e.target.id === "lsShop") sp.node.querySelector("#lsShopAdd").classList.toggle("ready", !!e.target.value.trim());
  });
  sp.node.addEventListener("submit", async (e) => {
    e.preventDefault();
    const input = sp.node.querySelector("#lsShop");
    const items = input.value.split(",").map((s) => s.trim()).filter(Boolean);
    if (!items.length) return;
    input.value = "";
    try {
      const r = await api("POST", "/api/shopping", { action: "add", items, who: owner() });
      if (r.error) { toast(r.error, true); return; }
      L.shop = r.items;
      if (r.already_there && r.already_there.length) toast("Уже в списке: " + r.already_there.join(", "));
    } catch (err) { if (err.message !== "locked") toast(err.message, true); }
    drawAll();
  });

  // ---------------------------------------------------------------- the home screen's chips (and the tablet's)

  function chipsHtml() {
    if (!L.rem && !L.shop) return "";
    const n = L.rem ? todayCount() : 0, running = liveTimers().length > 0, shopN = L.shop ? L.shop.filter((i) => !L.gone[i.item]).length : 0;
    const rem = n ? plural(n, "напоминание", "напоминания", "напоминаний") + " сегодня" : running ? "Идёт таймер" : "Напоминания";
    return `<button class="ls-chiplink jv-press" data-lists-go="reminders" aria-label="Напоминания: ${esc(rem)}${running ? ", таймер идёт" : ""}">
        <i>${icon("bell", 18)}${running ? "<em></em>" : ""}</i>${esc(rem)}</button>
      <button class="ls-chiplink jv-press" data-lists-go="shopping" aria-label="Покупки: ${shopN} в списке"><i>${icon("cart", 18)}</i>${shopN ? shopN + " в покупках" : "Покупки"}</button>`;
  }
  function drawChips() {
    const box = document.getElementById("headLists");
    if (!box) return;
    const before = box.offsetHeight;
    paint(box, chipsHtml());
    if (box.offsetHeight !== before && window.JV.replan) window.JV.replan();  // the header grew: the plan moves down
  }

  function drawAll() {
    drawChips();
    if (rm.node.offsetParent) drawReminders();
    if (sp.node.offsetParent) drawShopping();
  }

  document.addEventListener("click", (e) => {
    const go = e.target.closest("[data-lists-go]");
    if (go) window.JV.show(go.dataset.listsGo);
  });

  // fresh with the house every 30 s (every 6th refresh), and whenever the app comes back
  window.JV.listen(() => { L.polls += 1; if (L.polls % 6 === 1) loadBoth(); });

  const both = () => { drawReminders(); drawShopping(); loadBoth(); };
  const back = () => { if (L.ed) { closeSheet(); return true; } if (L.ask) { L.ask = null; drawShopping(); return true; } return false; };
  const leave = () => { if (L.snack) { clearTimeout(L.snack.timer); const s = L.snack; L.snack = null; drawSnack(); s.commit(); } L.ed = null; drawSheet(); };
  window.JV.add("reminders", { tab: "home", back: "home", open: both, leave, onBack: back });
  window.JV.add("shopping", { tab: "home", back: "home", open: both, leave, onBack: back });
  window.JV.listsChips = chipsHtml;
})();
