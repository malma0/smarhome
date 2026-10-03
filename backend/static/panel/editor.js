/* Jarvis panel - the layout editor (the design's PhoneEditor and PhoneOnboarding):
   draw the flat's rooms with a finger, size them, put in doors and windows, then drag
   the devices to where they stand. Saved to Jarvis (PUT /api/layout, app/layout.py) -
   the home screen's plan is drawn from it. Plain ES2017, like app.js. */
(function () {
  "use strict";
  const { api, toast, icon, esc } = window.JV;
  const UNIT = 50;  // plan units in a metre
  const SNAP = 5;  // 10 cm
  const MIN_ROOM = 40;
  const DOOR = 36, WINDOW = 80, EDGE = 16;  // lengths; how near a wall a tap counts as on it
  const AREA_TOP = 112, SHEET = 236;
  const TOOLS = [["move", "move", "Двигать"], ["room", "room", "Комната"], ["door", "door", "Дверь"], ["window", "window", "Окно"]];

  const ed = {
    node: null, plan: null, rooms: [], mode: "rooms", tool: "move", sel: null, undo: [], redo: [],
    drag: null, scale: 1, size: { w: 350, h: 540 }, start: false, done: false, dirty: false, firstTime: false,
  };
  const snap = (v) => Math.round(v / SNAP) * SNAP;
  const STICK = 14;  // a wall this near another room's wall lands on it
  /** v on the grid - or on the nearest wall of another room along that axis, when it's within STICK. */
  function stick(v, axis, except) {
    let best = null;
    Object.keys(ed.plan.rooms).forEach((name) => {
      if (name === except) return;
      const r = ed.plan.rooms[name];
      (axis === "x" ? [r.x, r.x + r.w] : [r.y, r.y + r.h]).forEach((edge) => {
        if (Math.abs(edge - v) <= STICK && (best === null || Math.abs(edge - v) < Math.abs(best - v))) best = edge;
      });
    });
    return best !== null ? best : snap(v);
  }
  const metres = (v) => String((v / UNIT).toFixed(1)).replace(".", ",");
  const roomNames = () => ed.rooms.map((r) => r.name);

  function emptyPlan() { return { rooms: {}, devices: {}, doors: [], windows: [] }; }

  function remember() {  // before every change: what undo brings back
    ed.undo.push(JSON.stringify(ed.plan));
    if (ed.undo.length > 60) ed.undo.shift();
    ed.redo = [];
    ed.dirty = true;
  }

  // ---------------------------------------------------------------- the canvas: the plan fitted into the space

  function canvasSize() {
    let w = 350, h = 540;
    Object.values(ed.plan.rooms).forEach((r) => { w = Math.max(w, r.x + r.w + 60); h = Math.max(h, r.y + r.h + 60); });
    return { w, h };
  }

  function fitCanvas() {
    const app = document.getElementById("app");
    const height = parseFloat(app.style.height) || 844;
    const width = ed.node.offsetWidth || 390;  // the phone's 390 points, or the tablet's column
    const availW = width - 24, availH = height - AREA_TOP - SHEET - 16;
    ed.size = canvasSize();
    ed.scale = Math.min(availW / ed.size.w, availH / ed.size.h);
    const left = (width - ed.size.w * ed.scale) / 2, top = AREA_TOP + 8 + (availH - ed.size.h * ed.scale) / 2;
    const canvas = ed.node.querySelector(".ed-canvas");
    canvas.style.transform = `translate(${left.toFixed(1)}px, ${top.toFixed(1)}px) scale(${ed.scale.toFixed(4)})`;
    const plan = ed.node.querySelector(".ed-plan");
    plan.style.width = ed.size.w + "px";
    plan.style.height = ed.size.h + "px";
  }

  /** A pointer's place on the plan, whatever the screen's and the canvas's scales. */
  function toPlan(e) {
    const rect = ed.node.querySelector(".ed-plan").getBoundingClientRect();
    const k = rect.width / ed.size.w;
    return { x: (e.clientX - rect.left) / k, y: (e.clientY - rect.top) / k };
  }

  // ---------------------------------------------------------------- drawing

  function segStyle(s, cls) {
    return s[0] === "h"
      ? `left:${s[2]}px;top:${s[1] - 3}px;width:${s[3] - s[2]}px;height:6px;border-width:${cls === "win" ? "1.5px 0" : "0"}`
      : `left:${s[1] - 3}px;top:${s[2]}px;width:6px;height:${s[3] - s[2]}px;border-width:${cls === "win" ? "0 1.5px" : "0"}`;
  }

  function drawPlan() {
    fitCanvas();
    const p = ed.plan;
    let html = "";
    Object.keys(p.rooms).forEach((name) => {
      const r = p.rooms[name];
      const sel = ed.sel && ed.sel.kind === "room" && ed.sel.name === name;
      const known = roomNames().indexOf(name) >= 0;
      html += `<div class="ed-room${sel ? " sel" : ""}${known ? "" : " new"}" data-room="${esc(name)}" style="left:${r.x}px;top:${r.y}px;width:${r.w}px;height:${r.h}px">
        <b>${esc(known ? name : "Какая это комната?")}</b><span>${metres(r.w)} × ${metres(r.h)} м</span>${sel && ed.tool === "move" ? `<i class="ed-handle" data-handle></i>` : ""}</div>`;
    });
    (p.doors || []).forEach((d, i) => {
      const sel = ed.sel && ed.sel.kind === "door" && ed.sel.i === i;
      html += `<span class="ed-seg door${sel ? " sel" : ""}" data-door="${i}" style="${segStyle(d, "door")}"></span>`;
    });
    (p.windows || []).forEach((w, i) => {
      const sel = ed.sel && ed.sel.kind === "window" && ed.sel.i === i;
      html += `<span class="ed-seg win${sel ? " sel" : ""}" data-window="${i}" style="${segStyle(w, "win")}"></span>`;
    });
    Object.keys(p.devices).forEach((key) => {
      const [room, type] = key.split("|");
      if (!p.rooms[room]) return;
      const xy = p.devices[key];
      const dragging = ed.drag && ed.drag.kind === "dev" && ed.drag.key === key;
      html += `<button class="ed-dev${dragging ? " drag" : ""}" data-dev="${esc(key)}" style="left:${xy[0]}px;top:${xy[1]}px" aria-label="${esc(window.JV.devName(type))}"><span>${icon(window.JV.devIcon(type), 15)}</span></button>`;
    });
    if (ed.drag && ed.drag.kind === "draw") {
      const d = ed.drag.rect;
      html += `<div class="ed-room new sel" style="left:${d.x}px;top:${d.y}px;width:${d.w}px;height:${d.h}px"><b></b><span>${metres(d.w)} × ${metres(d.h)} м</span></div>`;
    }
    const plan = ed.node.querySelector(".ed-plan");
    plan.className = "ed-plan " + ed.mode;
    plan.innerHTML = html;
  }

  /** Every device of the house: on the plan already or still to place. */
  function allDevices() {
    const list = [];
    ed.rooms.forEach((r) => r.devices.forEach((d) => {
      const key = r.name + "|" + d.type;
      if (window.JV.devIcon(d.type) === "power") return;  // not drawn on the plan (the guard)
      list.push({ key, room: r.name, type: d.type, placed: !!(ed.plan.rooms[r.name] && ed.plan.devices[key]) });
    }));
    return list.sort((a, b) => a.placed - b.placed);
  }

  function drawSheet() {
    const sheet = ed.node.querySelector(".ed-sheet");
    let html = `<span class="grab"></span>`;
    if (ed.mode === "rooms") {
      const s = ed.sel;
      if (s && s.kind === "room") {
        const r = ed.plan.rooms[s.name];
        html += `<div class="ed-sel"><span class="what">${roomNames().indexOf(s.name) >= 0 ? esc(s.name) : "Выбери, какая это комната"}</span>
          <span class="size">${metres(r.w)} × ${metres(r.h)} м</span><button class="del" data-delete>Удалить</button></div>
          <div class="ed-names">${ed.rooms.map((room) => {
            const used = !!ed.plan.rooms[room.name] && room.name !== s.name;
            return `<button class="${room.name === s.name ? "on" : ""}${used ? " used" : ""}" data-name="${esc(room.name)}">${used || room.name === s.name ? icon("check", 14) : ""}${esc(room.name)}</button>`;
          }).join("")}</div>`;
      } else if (s && (s.kind === "door" || s.kind === "window")) {
        html += `<div class="ed-sel"><span class="what">${s.kind === "door" ? "Дверь" : "Окно"}</span><button class="del" data-delete>Удалить</button></div>`;
      } else {
        const left = ed.rooms.filter((r) => !ed.plan.rooms[r.name]).length;
        html += `<span class="label">${{ move: "Нажми на комнату, чтобы двигать её и менять размер за кружок",
          room: left ? "Проведи пальцем от угла до угла — получится комната. Осталось нарисовать: " + left : "Все комнаты дома уже на плане",
          door: "Нажми на стену между комнатами — там будет дверь", window: "Нажми на наружную стену комнаты — там будет окно" }[ed.tool]}</span>`;
      }
      html += `<div class="ed-tools">${TOOLS.map((t) => `<button class="jv-press${ed.tool === t[0] ? " on" : ""}" aria-pressed="${ed.tool === t[0]}" data-tool="${t[0]}">${icon(t[1], 20)}${t[2]}</button>`).join("")}</div>`;
    } else {
      const list = allDevices();
      const free = list.filter((d) => !d.placed).length;
      html += `<div class="ed-palette-head"><b>${free ? "Не размещены · " + free : "Все устройства на плане"}</b><span>Перетащи на план</span></div>
        <div class="ed-palette">${list.map((d) => `<button class="${d.placed ? "placed" : "free"}" data-palette="${esc(d.key)}"><i>${icon(window.JV.devIcon(d.type), 19)}</i>
          <b>${esc(window.JV.devName(d.type))}</b><small>${esc(d.room)}${d.placed ? "" : " · нет на плане"}</small></button>`).join("")}</div>`;
    }
    sheet.innerHTML = html;
    const head = ed.node.querySelector(".ed-head");
    head.querySelector("[data-undo]").disabled = !ed.undo.length;
    head.querySelector("[data-redo]").disabled = !ed.redo.length;
    head.querySelectorAll("[data-mode]").forEach((b) => {
      b.classList.toggle("on", b.dataset.mode === ed.mode);
      b.setAttribute("aria-pressed", b.dataset.mode === ed.mode);
    });
  }

  function draw() { drawPlan(); drawSheet(); }

  function skeleton() {
    ed.node.innerHTML = `<div class="ed-grid"></div><div class="ed-canvas"><div class="ed-plan"></div></div>
      <header class="ed-head"><div class="bar"><button data-cancel>Отмена</button><b>Планировка</b><button class="done" data-done>Готово</button></div>
        <div class="tools"><button class="jv-press" aria-label="Отменить действие" data-undo>${icon("undo", 18)}</button>
          <button class="jv-press" aria-label="Повторить действие" data-redo>${icon("redo", 18)}</button>
          <div class="seg small" role="group" aria-label="Режим"><button data-mode="rooms">Комнаты</button><button data-mode="devices">Устройства</button></div></div></header>
      <div class="ed-sheet"></div><div class="ed-overlay"></div>`;
  }

  // ---------------------------------------------------------------- the first time: start empty or from the sketch; the end

  /** "Не нарисованы: ..." - save anyway, or go on drawing. */
  function drawAsk() {
    let box = ed.node.querySelector(".ed-ask");
    if (!ed.askMissing) { if (box) box.remove(); return; }
    if (!box) { box = document.createElement("div"); box.className = "ed-ask"; ed.node.appendChild(box); }
    const n = ed.askMissing.length;
    box.innerHTML = `<button class="scrim" data-ask-no aria-label="Продолжить рисовать"></button><div class="ask" role="alertdialog">
      <div style="display:flex;flex-direction:column;gap:8px"><h2>${n === 1 ? "Одна комната не нарисована" : "Не нарисованы " + n + " комнаты"}</h2>
      <p>${esc(ed.askMissing.join(", "))} — на главном экране ${n === 1 ? "она встанет" : "они встанут"} рядом под планом. Можно дорисовать сейчас или позже.</p></div>
      <button class="yes jv-press" data-ask-no>Дорисовать</button><button class="no jv-press" data-ask-yes>Сохранить так</button></div>`;
  }

  function drawStart() {
    const o = ed.node.querySelector(".ed-overlay");
    if (ed.start) {
      o.innerHTML = `<div class="ed-start"><div class="orb"><span class="halo"></span><span class="ball"></span><span class="swirl"></span></div>
        <div style="display:flex;flex-direction:column;gap:10px"><h1>Давай нарисуем квартиру</h1>
        <p>Так я буду знать, где что стоит. Нарисуй комнаты пальцем — размеры потом поправишь, а устройства перетащишь туда, где они на самом деле.</p></div>
        <div class="empty"><i>${icon("plus", 26)}</i>План пока пуст</div>
        <div style="display:flex;flex-direction:column;gap:10px"><button class="go jv-press" data-begin="empty">Нарисовать квартиру</button>
        <button class="alt jv-press" data-begin="sketch">Начать с типовой планировки</button></div>
        <button class="later" data-cancel>Позже</button></div>`;
    } else if (ed.done) {
      const h = window.JV.house();
      const norms = (h.rooms || []).find((r) => r.norms.temperature);
      o.innerHTML = `<div class="ed-start"><div class="orb"><span class="halo"></span><span class="ball"></span><span class="swirl"></span></div>
        <div style="display:flex;flex-direction:column;gap:10px"><h1>Дом настроен</h1><p>Я буду держать нормы и сообщу, если что-то пойдёт не так.</p></div>
        <div class="facts"><div><span>Комнаты</span><b>${Object.keys(ed.plan.rooms).length}</b></div>
          <div><span>Устройства на плане</span><b>${allDevices().filter((d) => d.placed).length}</b></div>
          ${norms ? `<div><span>Нормы</span><b>${String(norms.norms.temperature.value).replace(".", ",")} °C</b></div>` : ""}</div>
        <span style="flex:1"></span><button class="go jv-press" data-finish>Открыть план</button></div>`;
    } else {
      o.innerHTML = "";
    }
  }

  // ---------------------------------------------------------------- editing

  /** The wall nearest a point: {dir, along, from, to, room} - a room's edge within EDGE. */
  function wallAt(pt) {
    let best = null;
    Object.keys(ed.plan.rooms).forEach((name) => {
      const r = ed.plan.rooms[name];
      const edges = [["h", r.y, r.x, r.x + r.w], ["h", r.y + r.h, r.x, r.x + r.w], ["v", r.x, r.y, r.y + r.h], ["v", r.x + r.w, r.y, r.y + r.h]];
      edges.forEach((e) => {
        const [dir, along, from, to] = e;
        const off = dir === "h" ? Math.abs(pt.y - along) : Math.abs(pt.x - along);
        const pos = dir === "h" ? pt.x : pt.y;
        if (off <= EDGE && pos >= from && pos <= to && (!best || off < best.off)) best = { dir, along, from, to, room: name, off, pos };
      });
    });
    return best;
  }

  function addSegment(pt, kind) {
    const wall = wallAt(pt);
    if (!wall) { toast(kind === "door" ? "Нажми точно на стену" : "Нажми точно на стену комнаты", true); return; }
    const len = kind === "door" ? DOOR : WINDOW;
    if (wall.to - wall.from < len) { toast("Стена слишком короткая", true); return; }
    const a = Math.min(Math.max(snap(wall.pos - len / 2), wall.from), wall.to - len);
    const list = kind === "door" ? ed.plan.doors : ed.plan.windows;
    const taken = list.findIndex((x) => x[0] === wall.dir && Math.abs(x[1] - wall.along) < 2 && x[2] < a + len && a < x[3]);
    if (taken >= 0) {  // a tap where one already is: that one, not a second on top of it
      ed.sel = { kind, i: taken };
      toast(kind === "door" ? "Здесь уже есть дверь" : "Здесь уже есть окно");
      draw();
      return;
    }
    remember();
    if (kind === "door") {
      ed.plan.doors.push([wall.dir, wall.along, a, a + len]);
      ed.sel = { kind: "door", i: ed.plan.doors.length - 1 };
    } else {
      ed.plan.windows.push([wall.dir, wall.along, a, a + len, wall.room]);
      ed.sel = { kind: "window", i: ed.plan.windows.length - 1 };
    }
    draw();
  }

  function renameRoom(from, to) {
    const p = ed.plan;
    if (from === to || p.rooms[to]) return;
    remember();
    p.rooms[to] = p.rooms[from];
    delete p.rooms[from];
    Object.keys(p.devices).forEach((k) => {
      const [room, type] = k.split("|");
      if (room === from) { p.devices[to + "|" + type] = p.devices[k]; delete p.devices[k]; }
    });
    p.windows.forEach((w) => { if (w[4] === from) w[4] = to; });
    if (p.front && p.front.room === from) p.front.room = to;
    ed.sel = { kind: "room", name: to };
    placeMissingDevices(to);
    draw();
  }

  /** A room just named: its devices go in a row along its bottom, to be dragged where they stand. */
  function placeMissingDevices(name) {
    const r = ed.plan.rooms[name], room = ed.rooms.find((x) => x.name === name);
    if (!r || !room) return;
    let i = 0;
    room.devices.forEach((d) => {
      const key = name + "|" + d.type;
      if (ed.plan.devices[key] || window.JV.devIcon(d.type) === "power") return;
      const perRow = Math.max(1, Math.floor((r.w - 20) / 38));
      ed.plan.devices[key] = [r.x + 27 + (i % perRow) * 38, r.y + r.h - 22 - Math.floor(i / perRow) * 38];
      i++;
    });
  }

  function deleteSelected() {
    const s = ed.sel, p = ed.plan;
    if (!s) return;
    remember();
    if (s.kind === "room") {
      delete p.rooms[s.name];
      Object.keys(p.devices).forEach((k) => { if (k.split("|")[0] === s.name) delete p.devices[k]; });
      p.windows = p.windows.filter((w) => w[4] !== s.name);
    } else if (s.kind === "door") p.doors.splice(s.i, 1);
    else if (s.kind === "window") p.windows.splice(s.i, 1);
    ed.sel = null;
    draw();
  }

  function clampInto(name, x, y) {
    const r = ed.plan.rooms[name];
    return [Math.min(Math.max(x, r.x + 17), r.x + r.w - 17), Math.min(Math.max(y, r.y + 17), r.y + r.h - 17)];
  }

  // ---------------------------------------------------------------- the finger on the plan

  function onDown(e) {
    if (ed.start || ed.done) return;
    const pt = toPlan(e);
    const target = e.target;
    if (ed.mode === "devices") {
      const dev = target.closest("[data-dev]");
      if (!dev) return;
      const key = dev.dataset.dev, xy = ed.plan.devices[key];
      remember();
      ed.drag = { kind: "dev", key, dx: pt.x - xy[0], dy: pt.y - xy[1] };
    } else if (ed.tool === "room") {
      if (!ed.rooms.some((r) => !ed.plan.rooms[r.name])) { toast("Все комнаты дома уже на плане"); return; }
      const x0 = stick(pt.x, "x"), y0 = stick(pt.y, "y");
      ed.drag = { kind: "draw", x0, y0, rect: { x: x0, y: y0, w: 0, h: 0 } };
    } else if (ed.tool === "door" || ed.tool === "window") {
      addSegment(pt, ed.tool);
      return;
    } else {
      const handle = target.closest("[data-handle]");
      const room = target.closest("[data-room]"), door = target.closest("[data-door]"), win = target.closest("[data-window]");
      if (handle && ed.sel && ed.sel.kind === "room") {
        remember();
        ed.drag = { kind: "resize", name: ed.sel.name };
      } else if (door) { ed.sel = { kind: "door", i: +door.dataset.door }; draw(); return; }
      else if (win) { ed.sel = { kind: "window", i: +win.dataset.window }; draw(); return; }
      else if (room) {
        const name = room.dataset.room, r = ed.plan.rooms[name];
        ed.sel = { kind: "room", name };
        remember();
        ed.drag = { kind: "move", name, dx: pt.x - r.x, dy: pt.y - r.y, moved: false, x0: r.x, y0: r.y };
      } else { ed.sel = null; draw(); return; }
    }
    try { e.currentTarget.setPointerCapture(e.pointerId); } catch (err) { /* a pointer the browser no longer tracks */ }
    draw();
  }

  function onMove(e) {
    const d = ed.drag;
    if (!d) return;
    const pt = toPlan(e);
    const p = ed.plan;
    if (d.kind === "draw") {
      const x = stick(pt.x, "x"), y = stick(pt.y, "y");
      d.rect = { x: Math.min(d.x0, x), y: Math.min(d.y0, y), w: Math.abs(x - d.x0), h: Math.abs(y - d.y0) };
    } else if (d.kind === "move") {
      const r = p.rooms[d.name];
      // the room's left or right wall, top or bottom, whichever comes near a neighbour's
      let nx = stick(pt.x - d.dx, "x", d.name), ny = stick(pt.y - d.dy, "y", d.name);
      const rx = stick(pt.x - d.dx + r.w, "x", d.name), by = stick(pt.y - d.dy + r.h, "y", d.name);
      if (Math.abs(rx - (pt.x - d.dx + r.w)) < Math.abs(nx - (pt.x - d.dx))) nx = rx - r.w;
      if (Math.abs(by - (pt.y - d.dy + r.h)) < Math.abs(ny - (pt.y - d.dy))) ny = by - r.h;
      nx = Math.max(0, nx); ny = Math.max(0, ny);
      const dx = nx - r.x, dy = ny - r.y;
      if (!dx && !dy) return;
      d.moved = true;
      r.x = nx; r.y = ny;
      Object.keys(p.devices).forEach((k) => { if (k.split("|")[0] === d.name) { p.devices[k][0] += dx; p.devices[k][1] += dy; } });
    } else if (d.kind === "resize") {
      const r = p.rooms[d.name];
      r.w = Math.max(MIN_ROOM, stick(pt.x, "x", d.name) - r.x);
      r.h = Math.max(MIN_ROOM, stick(pt.y, "y", d.name) - r.y);
    } else if (d.kind === "dev") {
      const room = d.key.split("|")[0];
      p.devices[d.key] = clampInto(room, pt.x - d.dx, pt.y - d.dy);
    }
    drawPlan();
    if (d.kind === "resize" || d.kind === "move") {
      const size = ed.node.querySelector(".ed-sel .size");
      const r = p.rooms[d.name];
      if (size && r) size.textContent = metres(r.w) + " × " + metres(r.h) + " м";
    }
  }

  function onUp() {
    const d = ed.drag;
    if (!d) return;
    ed.drag = null;
    if (d.kind === "draw") {
      const r = d.rect;
      if (r.w >= MIN_ROOM && r.h >= MIN_ROOM) {
        remember();
        const name = ed.rooms.find((x) => !ed.plan.rooms[x.name]).name;  // the next one not drawn; renamed with a tap
        ed.plan.rooms[name] = { x: r.x, y: r.y, w: r.w, h: r.h };
        placeMissingDevices(name);
        ed.sel = { kind: "room", name };
        ed.tool = "move";
      } else if (r.w || r.h) toast("Слишком маленькая — проведи от угла до угла");
    } else if (d.kind === "move" && !d.moved) {
      ed.undo.pop();  // a tap that only selected: nothing to undo
    }
    draw();
  }

  /** A device dragged from the palette: a card follows the finger, dropped onto its room. */
  function paletteDrag(e, key) {
    e.preventDefault();
    const [room, type] = key.split("|");
    if (!ed.plan.rooms[room]) { toast("Сначала нарисуй комнату «" + room + "»", true); return; }
    const app = document.getElementById("app");
    const k = app.getBoundingClientRect().width / app.offsetWidth;  // the screen's own scale
    const ghost = document.createElement("div");
    ghost.className = "ed-ghost";
    ghost.innerHTML = `<i>${icon(window.JV.devIcon(type), 17)}</i><span>${esc(window.JV.devName(type))} → ${esc(room)}</span>`;
    app.appendChild(ghost);
    const at = (ev) => {
      const box = app.getBoundingClientRect();
      ghost.style.left = ((ev.clientX - box.left) / k - 20) + "px";
      ghost.style.top = ((ev.clientY - box.top) / k - 52) + "px";
    };
    at(e);
    const move = (ev) => at(ev);
    const up = (ev) => {
      document.removeEventListener("pointermove", move);
      document.removeEventListener("pointerup", up);
      ghost.remove();
      const pt = toPlan(ev);
      if (pt.x < 0 || pt.y < 0 || pt.x > ed.size.w || pt.y > ed.size.h) return;  // let go off the plan
      remember();
      ed.plan.devices[key] = clampInto(room, pt.x, pt.y);
      draw();
    };
    document.addEventListener("pointermove", move);
    document.addEventListener("pointerup", up);
  }

  // ---------------------------------------------------------------- open, save, leave

  function open() {
    const l = window.JV.layout();
    ed.rooms = l.rooms.filter((r) => r.name !== "Весь дом");
    ed.plan = l.custom ? l.plan : l.sketch;
    ed.start = !l.custom;
    ed.firstTime = !l.custom;
    ed.done = false;
    ed.undo = []; ed.redo = []; ed.sel = null; ed.drag = null; ed.dirty = false; ed.askMissing = null; ed.confirmedMissing = false;
    drawAsk();
    ed.mode = "rooms"; ed.tool = "move";
    ed.plan.doors = ed.plan.doors || []; ed.plan.windows = ed.plan.windows || []; ed.plan.devices = ed.plan.devices || {};
    drawStart();
    draw();
  }

  async function save() {
    const unnamed = Object.keys(ed.plan.rooms).filter((n) => roomNames().indexOf(n) < 0);
    if (!Object.keys(ed.plan.rooms).length) { toast("Нарисуй хотя бы одну комнату", true); return; }
    if (unnamed.length) { toast("Выбери название каждой комнате", true); return; }
    const missing = ed.rooms.filter((r) => !ed.plan.rooms[r.name]).map((r) => r.name);
    if (missing.length && !ed.confirmedMissing) {
      ed.askMissing = missing;
      drawAsk();
      return;
    }
    ed.confirmedMissing = false;
    try {
      const r = await api("PUT", "/api/layout", { layout: ed.plan });
      if (r.error) { toast(r.error, true); return; }
      window.JV.setLayout(r.layout);
      if (ed.firstTime) { ed.done = true; drawStart(); return; }
      toast("Планировка сохранена");
      window.JV.show("home");
    } catch (e) { if (e.message !== "locked") toast(e.message, true); }
  }

  ed.node = document.createElement("section");
  ed.node.className = "screen";
  ed.node.id = "screen-editor";
  document.getElementById("app").insertBefore(ed.node, document.getElementById("nav"));
  skeleton();

  const planEl = ed.node.querySelector(".ed-plan");
  planEl.addEventListener("pointerdown", onDown);
  planEl.addEventListener("pointermove", onMove);
  planEl.addEventListener("pointerup", onUp);
  planEl.addEventListener("pointercancel", onUp);
  ed.node.addEventListener("pointerdown", (e) => {
    const card = e.target.closest("[data-palette]");
    if (card) paletteDrag(e, card.dataset.palette);
  });
  ed.node.addEventListener("click", (e) => {
    const t = e.target;
    if (ed.askMissing) {
      if (t.closest("[data-ask-yes]")) { ed.askMissing = null; drawAsk(); ed.confirmedMissing = true; save(); }
      else if (t.closest("[data-ask-no]")) { ed.askMissing = null; drawAsk(); ed.tool = "room"; draw(); }
      return;
    }
    if (t.closest("[data-cancel]")) { window.JV.show("sliders"); return; }
    if (t.closest("[data-done]")) { save(); return; }
    if (t.closest("[data-finish]")) { ed.done = false; drawStart(); window.JV.show("home"); return; }
    const begin = t.closest("[data-begin]");
    if (begin) {
      if (begin.dataset.begin === "empty") { ed.plan = emptyPlan(); ed.tool = "room"; }
      ed.start = false;
      drawStart();
      draw();
      return;
    }
    if (t.closest("[data-undo]") && ed.undo.length) { ed.redo.push(JSON.stringify(ed.plan)); ed.plan = JSON.parse(ed.undo.pop()); ed.sel = null; draw(); return; }
    if (t.closest("[data-redo]") && ed.redo.length) { ed.undo.push(JSON.stringify(ed.plan)); ed.plan = JSON.parse(ed.redo.pop()); ed.sel = null; draw(); return; }
    const mode = t.closest("[data-mode]");
    if (mode) { ed.mode = mode.dataset.mode; ed.sel = null; draw(); return; }
    const tool = t.closest("[data-tool]");
    if (tool) { ed.tool = tool.dataset.tool; ed.sel = null; draw(); return; }
    if (t.closest("[data-delete]")) { deleteSelected(); return; }
    const name = t.closest("[data-name]");
    if (name && ed.sel && ed.sel.kind === "room") renameRoom(ed.sel.name, name.dataset.name);
  });
  window.JV.add("editor", { tab: "sliders", back: "sliders", open });
})();
