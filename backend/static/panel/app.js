/* Jarvis panel: the "Дом" screen of the Claude Design mockup, on the real
   house (app/panel.py). Rooms and devices come from Home Assistant; where
   they stand on the plan comes from LAYOUT (the mockup's own geometry) -
   rooms or devices it doesn't know are placed automatically until the plan
   editor exists. */
(() => {
  "use strict";

  // ---------------------------------------------------------------- the plan's geometry (from the mockup)

  const LAYOUT = {
    rooms: {
      zal: { x: 0, y: 0, w: 210, h: 200, motion: [128, 150] },
      kitchen: { x: 210, y: 0, w: 140, h: 200, motion: [280, 140] },
      corridor: { x: 0, y: 200, w: 200, h: 90, motion: [60, 262] },
      hall: { x: 200, y: 200, w: 150, h: 120, motion: [240, 250] },
      bedroom: { x: 0, y: 290, w: 200, h: 250, motion: [60, 460] },
      office: { x: 200, y: 320, w: 150, h: 220, motion: [276, 378] },
    },
    devices: {
      "zal-curtain": [125, 22], "zal-ac": [180, 22], "zal-heat": [28, 80], "zal-light": [105, 105], "zal-vent": [182, 95],
      "zal-humid": [40, 165], "zal-socket": [178, 170],
      "kitchen-curtain": [325, 24], "kitchen-ac": [235, 72], "kitchen-heat": [325, 72], "kitchen-light": [280, 112],
      "kitchen-vent": [325, 122], "kitchen-water": [280, 170], "kitchen-gas": [325, 170], "kitchen-socket": [235, 172],
      "corridor-light": [115, 248], "corridor-vent": [165, 228], "corridor-heat": [165, 268],
      "hall-light": [255, 272], "hall-vent": [218, 300], "hall-heat": [300, 302],
      "bedroom-ac": [172, 314], "bedroom-curtain": [22, 420], "bedroom-light": [100, 410], "bedroom-socket": [172, 410],
      "bedroom-heat": [22, 478], "bedroom-humid": [100, 490], "bedroom-vent": [172, 490],
      "office-ac": [326, 346], "office-humid": [226, 398], "office-vent": [326, 402], "office-light": [276, 428],
      "office-socket": [226, 462], "office-heat": [326, 464], "office-curtain": [276, 516],
    },
    doors: [["h", 200, 118, 152], ["h", 200, 262, 296], ["v", 200, 212, 282], ["h", 290, 146, 180], ["h", 320, 232, 266], ["v", 350, 245, 285]],
    windows: [["h", 0, 40, 170, "zal"], ["h", 0, 250, 330, "kitchen"], ["v", 0, 375, 465, "bedroom"], ["h", 540, 236, 316, "office"]],
    front: { room: "hall", x: 310, y: 245 },
  };
  const ROOM_IDS = { "Зал": "zal", "Кухня": "kitchen", "Коридор": "corridor", "Прихожая": "hall", "Спальня": "bedroom", "Кабинет": "office" };
  const AT = { "Зал": "в зале", "Кухня": "на кухне", "Коридор": "в коридоре", "Прихожая": "в прихожей", "Спальня": "в спальне",
    "Кабинет": "в кабинете", "Гостиная": "в гостиной", "Детская": "в детской", "Ванная": "в ванной", "Балкон": "на балконе" };
  // Home Assistant's device types (app/domains/home.py) <-> the mockup's
  const TYPE = { light: "light", socket: "socket", ac: "ac", heating: "heat", ventilation: "vent", humidifier: "humid",
    curtains: "curtain", water_valve: "water", gas_valve: "gas" };
  const NAMES = { light: "Свет", socket: "Розетка", ac: "Кондиционер", heat: "Отопление", vent: "Вентиляция", humid: "Увлажнитель",
    curtain: "Шторы", water: "Кран воды", gas: "Кран газа" };
  const DANGER = { smoke: "дым", moisture: "протечка", gas: "газ", carbon_monoxide: "угарный газ", intrusion: "движение в пустом доме" };
  const PLAN_W = 350, PLAN_H = 540, CANVAS_W = 390, REFRESH_MS = 5000;

  // ---------------------------------------------------------------- icons (the mockup's set)

  const ICONS = {  // every icon of the design's Icon component
    ac: '<rect x="3" y="5" width="18" height="8" rx="2.5"/><path d="M7 10h10"/><path d="M8 16.5l-1 3M12 16.5v3M16 16.5l1 3"/>',
    alert: '<path d="M12 3.5L2.5 20h19z"/><path d="M12 10v4.5M12 17.2h.01"/>',
    back: '<path d="M15 5l-7 7 7 7"/>',
    bell: '<path d="M6 16.5V11a6 6 0 0 1 12 0v5.5l1.5 1.5h-15z"/><path d="M10 20.5a2 2 0 0 0 4 0"/>',
    bolt: '<path d="M13 2.5L4.5 13.5H11l-1 8 8.5-11H12z"/>',
    calendar: '<rect x="3.5" y="5" width="17" height="15.5" rx="2"/><path d="M3.5 10h17M8 3v4M16 3v4"/>',
    chart: '<path d="M4 20V11M9.5 20V5M15 20v-6M20.5 20V9"/>',
    check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    chev: '<path d="M9 5l7 7-7 7"/>',
    clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    close: '<path d="M6 6l12 12M18 6L6 18"/>',
    cloud: '<path d="M7 18.5a4.5 4.5 0 0 1-.4-9A6 6 0 0 1 18 8.6a5 5 0 0 1-.5 9.9z"/>',
    co2: '<path d="M3 8h11a3 3 0 1 0-3-3"/><path d="M3 12h16a3 3 0 1 1-3 3"/><path d="M3 16h7"/>',
    curtain: '<path d="M3 4h18"/><path d="M5 4v16h3c0-5 2-9 3-16"/><path d="M19 4v16h-3c0-5-2-9-3-16"/>',
    cycle: '<path d="M20 11a8 8 0 0 0-14.5-4.5M4 13a8 8 0 0 0 14.5 4.5"/><path d="M5 3v4h4M19 21v-4h-4"/>',
    door: '<path d="M6 21V4.5A1.5 1.5 0 0 1 7.5 3h9A1.5 1.5 0 0 1 18 4.5V21"/><path d="M3.5 21h17"/><path d="M14.5 12.5h.01"/>',
    down: '<path d="M5 9l7 7 7-7"/>',
    edit: '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13.5 6.5l4 4"/>',
    exit: '<path d="M14 4h4.5a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1H14"/><path d="M9.5 16.5L5 12l4.5-4.5M5 12h10.5"/>',
    gas: '<path d="M12 3c.8 3.2 5 5.2 5 10a5 5 0 0 1-10 0c0-2.6 1.4-4.3 2.6-5.2.2 2 1.2 3.2 2.4 3.4C12 8.8 11 6 12 3z"/>',
    grip: '<path d="M9 6h.01M15 6h.01M9 12h.01M15 12h.01M9 18h.01M15 18h.01"/>',
    heat: '<rect x="4" y="5" width="4" height="14" rx="2"/><rect x="10" y="5" width="4" height="14" rx="2"/><rect x="16" y="5" width="4" height="14" rx="2"/>',
    home: '<path d="M3.5 10.5L12 4l8.5 6.5V20a1 1 0 0 1-1 1H15v-6H9v6H4.5a1 1 0 0 1-1-1z"/>',
    hub: '<rect x="3.5" y="13" width="17" height="7" rx="2"/><path d="M7.5 16.5h.01M11 16.5h.01"/><path d="M8.5 9.5a5 5 0 0 1 7 0M6 7a8.5 8.5 0 0 1 12 0"/>',
    humid: '<path d="M12 3.5s6 6.3 6 10.5a6 6 0 0 1-12 0c0-4.2 6-10.5 6-10.5z"/><path d="M9.5 14.5a2.5 2.5 0 0 0 2.5 2.5"/>',
    kettle: '<path d="M6 10h10.5l-1 9.5h-8.5z"/><path d="M7.5 10a4 4 0 0 1 7.5 0"/><path d="M16.3 12.5H18a2 2 0 0 1 0 4h-2"/><path d="M10 4.5h2.5"/>',
    key: '<circle cx="8" cy="15" r="4"/><path d="M11 12l9-9M17 6l2.5 2.5M14.5 8.5l2 2"/>',
    layout: '<rect x="3.5" y="3.5" width="17" height="17" rx="1.5"/><path d="M3.5 11h8V3.5M11.5 15v5.5M11.5 11h9"/>',
    leak: '<path d="M12 3s4.5 4.8 4.5 8a4.5 4.5 0 0 1-9 0c0-3.2 4.5-8 4.5-8z"/><path d="M3 19c1.5 0 1.5 1.2 3 1.2S7.5 19 9 19s1.5 1.2 3 1.2 1.5-1.2 3-1.2 1.5 1.2 3 1.2 1.5-1.2 3-1.2"/>',
    light: '<path d="M9 18h6"/><path d="M10 21h4"/><path d="M12 3a6 6 0 0 0-3.6 10.8c.7.6 1.1 1.3 1.1 2.2h5c0-.9.4-1.6 1.1-2.2A6 6 0 0 0 12 3z"/>',
    lock: '<rect x="5" y="10.5" width="14" height="10" rx="2.5"/><path d="M8 10.5V8a4 4 0 0 1 8 0v2.5"/>',
    mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21"/>',
    minus: '<path d="M5 12h14"/>',
    moon: '<path d="M19.5 14.5A8 8 0 0 1 9.5 4.5a8 8 0 1 0 10 10z"/>',
    motion: '<circle cx="12" cy="12" r="2"/><path d="M8.2 8.2a5.4 5.4 0 0 0 0 7.6M15.8 8.2a5.4 5.4 0 0 1 0 7.6"/><path d="M5.3 5.3a9.5 9.5 0 0 0 0 13.4M18.7 5.3a9.5 9.5 0 0 1 0 13.4"/>',
    move: '<path d="M12 3v18M3 12h18M9 6l3-3 3 3M9 18l3 3 3-3M6 9l-3 3 3 3M18 9l3 3-3 3"/>',
    palette: '<path d="M12 3.5a8.5 8.5 0 0 0 0 17c1.2 0 1.8-.8 1.8-1.7 0-1.3-1-1.5-1-2.6 0-1 .8-1.7 1.8-1.7h2.1a3.8 3.8 0 0 0 3.8-3.8c0-4-3.8-7.2-8.5-7.2z"/><path d="M7.5 11.5h.01M10 7.5h.01M14.5 7.5h.01"/>',
    phone: '<path d="M5 3.5h3.5l2 5-2.5 1.5a11 11 0 0 0 6 6l1.5-2.5 5 2V19a2 2 0 0 1-2 2A17 17 0 0 1 3 5.5a2 2 0 0 1 2-2z"/>',
    play: '<path d="M8 5.5v13l10.5-6.5z"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    power: '<path d="M12 3v8"/><path d="M6.5 6.5a8 8 0 1 0 11 0"/>',
    redo: '<path d="M15 7l4.5 4.5L15 16"/><path d="M19.5 11.5H9a4.5 4.5 0 0 0 0 9h2"/>',
    room: '<path d="M4 4h16v16H4z"/><path d="M4 10h4M12 4v4M14 20v-5h6"/>',
    scenes: '<rect x="3.5" y="3.5" width="7" height="7" rx="2"/><rect x="13.5" y="3.5" width="7" height="7" rx="2"/><rect x="3.5" y="13.5" width="7" height="7" rx="2"/><path d="M17 14v6.5M13.8 17.2h6.5"/>',
    send: '<path d="M5 12h13M13 6l6 6-6 6"/>',
    shield: '<path d="M12 3l7.5 3v5.5c0 4.7-3.2 8.2-7.5 9.5-4.3-1.3-7.5-4.8-7.5-9.5V6z"/>',
    shieldok: '<path d="M12 3l7.5 3v5.5c0 4.7-3.2 8.2-7.5 9.5-4.3-1.3-7.5-4.8-7.5-9.5V6z"/><path d="M9 12l2.2 2.2L15.5 10"/>',
    sliders: '<path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/>',
    smoke: '<path d="M4 5h16v2.5A3.5 3.5 0 0 1 16.5 11h-9A3.5 3.5 0 0 1 4 7.5z"/><path d="M8.5 14c-1 1.5 1 2.5 0 4M12 14c-1 1.5 1 2.5 0 4M15.5 14c-1 1.5 1 2.5 0 4"/>',
    socket: '<rect x="4" y="4" width="16" height="16" rx="5"/><path d="M9.5 10v3.5M14.5 10v3.5"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4"/>',
    sunrise: '<path d="M4 18h16M7 18a5 5 0 0 1 10 0"/><path d="M12 5v4M5.6 10.6l1.4 1.4M18.4 10.6L17 12M3 14.5h1.5M19.5 14.5H21"/>',
    thermo: '<path d="M14 14.6V5a2 2 0 0 0-4 0v9.6a4 4 0 1 0 4 0z"/><path d="M12 9v7"/>',
    undo: '<path d="M9 7L4.5 11.5 9 16"/><path d="M4.5 11.5H15a4.5 4.5 0 0 1 0 9h-2"/>',
    user: '<circle cx="12" cy="8" r="3.8"/><path d="M4.5 20.5a7.5 7.5 0 0 1 15 0"/>',
    users: '<circle cx="9" cy="8.5" r="3.3"/><path d="M2.8 19.5a6.2 6.2 0 0 1 12.4 0"/><path d="M15.5 5.5a3.3 3.3 0 0 1 0 6.3M17.5 14.2a6.2 6.2 0 0 1 3.7 5.3"/>',
    vent: '<circle cx="12" cy="12" r="1.8"/><path d="M12 10.2C11 6 12.5 3.5 15 3.8c2.3.3 2.2 3.6-1.2 6.4"/><path d="M13.8 12c4.2-1 6.7.5 6.4 3-.3 2.3-3.6 2.2-6.4-1.2"/><path d="M12 13.8c1 4.2-.5 6.7-3 6.4-2.3-.3-2.2-3.6 1.2-6.4"/><path d="M10.2 12C6 13 3.5 11.5 3.8 9c.3-2.3 3.6-2.2 6.4 1.2"/>',
    voice: '<path d="M4 10v4M8 7v10M12 4v16M16 8v8M20 11v2"/>',
    water: '<path d="M12 3v4"/><path d="M8.5 3h7"/><circle cx="12" cy="13" r="5"/><path d="M3 13h4M17 13h4"/><path d="M12 13l2.5-2"/>',
    wifioff: '<path d="M3 3l18 18"/><path d="M8.5 16.4a5 5 0 0 1 6.2-.6"/><path d="M5.2 12.9a9.7 9.7 0 0 1 4-2.4M18.8 12.9a9.6 9.6 0 0 0-2.3-1.6"/><path d="M2 9.3a15 15 0 0 1 4-2.6M22 9.3A15 15 0 0 0 11 5.1"/><path d="M12 20h.01"/>',
    window: '<rect x="4.5" y="3.5" width="15" height="17" rx="1.5"/><path d="M12 3.5v17M4.5 12h15"/>',
  };;
  const icon = (type, size) => `<svg class="ic" viewBox="0 0 24 24" width="${size}" height="${size}" aria-hidden="true">${ICONS[type] || ""}</svg>`;

  // ---------------------------------------------------------------- small helpers

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const num = (v) => { const n = parseFloat(v); return isNaN(n) ? null : n; };
  const ru1 = (n) => (Math.round(n * 10) / 10).toFixed(1).replace(".", ",");
  const ruN = (n) => String(Math.round(n * 10) / 10).replace(".", ",");
  const at = (name) => AT[name] || ("в комнате «" + name + "»");
  const store = { get: (k) => { try { return localStorage.getItem(k) || ""; } catch (e) { return ""; } },
                  set: (k, v) => { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } } };

  let pin = store.get("jarvis-panel-pin");
  const state = { rooms: [], house: null, room: "", last: "", offline: false, loading: true, weather: null,
                  busy: false, series: {}, geometry: null };

  async function api(method, path, body) {
    const response = await fetch(path, {
      method, headers: Object.assign({ "X-Pin": pin }, body ? { "Content-Type": "application/json" } : {}),
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await response.json().catch(() => null);
    if (response.status === 401 || response.status === 429) {
      showLock(response.status === 401 ? "Неверный PIN." : (data && data.detail) || "Подожди минуту.");
      throw new Error("locked");
    }
    if (!response.ok) throw new Error((data && data.detail) || ("Ошибка " + response.status));
    return data;
  }

  function toast(text, bad) {
    const old = document.querySelector(".toast");
    if (old) old.remove();
    const node = document.createElement("div");
    node.className = "toast" + (bad ? " bad" : "");
    node.setAttribute("role", "status");
    node.textContent = text;
    $("app").appendChild(node);
    setTimeout(() => node.remove(), 1900);
  }

  // ---------------------------------------------------------------- the canvas: the mockup's 390 points, scaled to the screen

  let H = 844;
  function fit() {
    const scale = window.innerWidth / CANVAS_W;
    H = Math.round(window.innerHeight / scale);
    const app = $("app");
    app.style.height = H + "px";
    app.style.transform = "scale(" + scale + ")";
    $("sheetWrap").style.height = sheetH() + "px";
    $("orb").style.bottom = "98px";
    placePlan();
  }
  const sheetH = () => Math.round(H * 0.535);

  // ---------------------------------------------------------------- the house -> rooms with places on the plan

  function geometry(rooms) {
    const known = rooms.filter((r) => ROOM_IDS[r.name] && LAYOUT.rooms[ROOM_IDS[r.name]]);
    const unknown = rooms.filter((r) => !known.includes(r));
    const placed = {};
    known.forEach((r) => { placed[r.name] = Object.assign({ id: ROOM_IDS[r.name] }, LAYOUT.rooms[ROOM_IDS[r.name]]); });
    let bottom = known.length ? Math.max(...known.map((r) => placed[r.name].y + placed[r.name].h)) : 0;
    unknown.forEach((r, i) => {  // rooms the mockup doesn't have: rows of two under the plan
      const col = i % 2, row = Math.floor(i / 2);
      placed[r.name] = { id: "x" + i, x: col * 175, y: bottom + row * 130, w: 175, h: 130, motion: [col * 175 + 140, bottom + row * 130 + 30] };
    });
    if (unknown.length) bottom += Math.ceil(unknown.length / 2) * 130;
    const width = PLAN_W, height = Math.max(PLAN_H, bottom);
    rooms.forEach((r) => {
      const g = placed[r.name];
      let free = 0;
      g.devices = r.devices.filter((d) => TYPE[d.type]).map((d) => {
        const type = TYPE[d.type];
        const spot = LAYOUT.devices[g.id + "-" + type];
        const xy = spot || [g.x + 24 + (free % 4) * 38, g.y + g.h - 24 - Math.floor(free++ / 4) * 38];
        return { type, raw: d, x: xy[0], y: xy[1] };
      });
    });
    return { placed, width, height };
  }

  function buildPlan() {
    const plan = $("plan");
    const g = state.geometry;
    plan.style.width = g.width + "px";
    plan.style.height = g.height + "px";
    let html = "";
    state.rooms.forEach((r) => {
      const p = g.placed[r.name];
      html += `<button class="p-room" data-room="${esc(r.name)}" style="left:${p.x}px;top:${p.y}px;width:${p.w}px;height:${p.h}px">
        <span class="name">${esc(r.name)}</span><span class="vals mono"><span class="t"></span><span class="h"></span></span></button>`;
    });
    html += `<div class="p-outer" style="left:-3px;top:-3px;width:${g.width + 6}px;height:${g.height + 6}px"></div>`;
    if (!Object.values(g.placed).some((p) => p.id.startsWith("x"))) {
      LAYOUT.doors.forEach((d) => {
        html += d[0] === "h"
          ? `<span class="p-door" style="left:${d[2]}px;top:${d[1] - 3}px;width:${d[3] - d[2]}px;height:6px"></span>`
          : `<span class="p-door" style="left:${d[1] - 3}px;top:${d[2]}px;width:6px;height:${d[3] - d[2]}px"></span>`;
      });
      const f = LAYOUT.front;
      html += `<span class="p-front-arc" style="left:${f.x}px;top:${f.y}px"></span><span class="p-front" id="frontDoor" style="left:${f.x}px;top:${f.y - 1}px"></span>`;
      LAYOUT.windows.forEach((w, i) => {
        html += w[0] === "h"
          ? `<span class="p-win" data-win="${w[4]}" style="left:${w[2]}px;top:${w[1] - 3}px;width:${w[3] - w[2]}px;height:6px;border-width:1.5px 0"></span>`
          : `<span class="p-win" data-win="${w[4]}" style="left:${w[1] - 3}px;top:${w[2]}px;width:6px;height:${w[3] - w[2]}px;border-width:0 1.5px"></span>`;
      });
    }
    state.rooms.forEach((r) => {
      const p = g.placed[r.name];
      html += `<span class="p-motion" data-motion="${esc(r.name)}" hidden style="left:${p.motion[0] - 5}px;top:${p.motion[1] - 5}px"></span>`;
      p.devices.forEach((d) => {
        html += `<button class="p-dev" data-room="${esc(r.name)}" data-dev="${d.raw.type}" aria-label="${NAMES[d.type]}" style="left:${d.x - 17}px;top:${d.y - 17}px"><span>${icon(d.type, 15)}</span></button>`;
      });
    });
    plan.innerHTML = html;
    placePlan();
  }

  // the plan starts under the header's real bottom: a two-line summary or a bigger system font
  // makes the header taller than the mockup's 176, and the cards would cover the plan
  let headerBottom = 0;
  function planTop() {
    const header = document.querySelector(".header");
    headerBottom = header.offsetTop + header.offsetHeight;
    return Math.max(176, headerBottom + 14);
  }
  function placePlanIfHeaderMoved() {
    const header = document.querySelector(".header");
    if (header.offsetTop + header.offsetHeight !== headerBottom && !state.room) placePlan();
  }

  function placePlan() {
    const g = state.geometry;
    if (!g) return;
    const stage = $("stage");
    const room = state.room && g.placed[state.room];
    if (room) {
      const visTop = 96, visBottom = H - sheetH() - 8;
      const availH = visBottom - visTop, availW = 390 - 40;
      const s = Math.min(2.4, Math.min(availW / room.w, availH / room.h) * 0.94);
      const tx = 195 - s * (room.x + room.w / 2), ty = visTop + availH / 2 - s * (room.y + room.h / 2);
      stage.style.transitionDuration = "480ms";
      stage.style.transform = `translate(${tx.toFixed(1)}px, ${ty.toFixed(1)}px) scale(${s.toFixed(3)})`;
      return;
    }
    const top = planTop(), bottomGap = 100;
    const s = Math.min(0.94, (H - top - bottomGap) / g.height, 350 / g.width);
    const left = (390 - g.width * s) / 2, y = top + Math.max(0, (H - top - bottomGap - g.height * s) / 2);
    stage.style.transitionDuration = "420ms";
    stage.style.transform = `translate(${left.toFixed(1)}px, ${y.toFixed(1)}px) scale(${s.toFixed(3)})`;
  }

  // ---------------------------------------------------------------- drawing the state

  const reading = (r, k) => (r.readings[k] ? r.readings[k].value : null);
  const devOf = (r, type) => r.devices.find((d) => d.type === type);
  const isOn = (d) => (d ? (d.type === "curtains" ? (d.position || 0) > 0 : !!d.on) : false);
  const dangersOf = (r) => Object.keys(r.dangers).filter((k) => r.dangers[k]);

  function drawPlanState() {
    const plan = $("plan");
    const focus = state.room;
    plan.classList.toggle("muted", state.offline);
    plan.querySelectorAll(".p-room").forEach((el) => {
      const r = state.rooms.find((x) => x.name === el.dataset.room);
      if (!r) return;
      const t = num(reading(r, "temperature")), h = num(reading(r, "humidity"));
      el.querySelector(".t").textContent = state.offline || t === null ? "—" : ru1(t) + "°";
      el.querySelector(".h").textContent = state.offline || h === null ? "" : Math.round(h) + "%";
      el.classList.toggle("dim", !!focus && focus !== r.name);
      el.classList.toggle("focus", focus === r.name);
      el.classList.toggle("alarm", dangersOf(r).length > 0);
      el.setAttribute("aria-label", r.name + (t !== null ? ", " + ru1(t) + " градуса" : ""));
    });
    plan.querySelectorAll(".p-dev").forEach((el) => {
      const r = state.rooms.find((x) => x.name === el.dataset.room);
      const d = r && devOf(r, el.dataset.dev);
      const on = isOn(d);
      const leak = r && (r.dangers.moisture || r.dangers.gas || r.dangers.smoke);
      el.classList.toggle("on", on);
      el.classList.toggle("danger", !!leak && ["water_valve", "gas_valve", "ventilation"].includes(el.dataset.dev));
      el.classList.toggle("dim", !!focus && focus !== el.dataset.room);
      el.setAttribute("aria-pressed", on ? "true" : "false");
    });
    plan.querySelectorAll(".p-win").forEach((el) => {
      const r = state.rooms.find((x) => ROOM_IDS[x.name] === el.dataset.win);
      el.classList.toggle("open", !!(r && reading(r, "window")) && !state.offline);
      el.classList.toggle("dim", !!focus && (!r || focus !== r.name));
    });
    plan.querySelectorAll(".p-motion").forEach((el) => {
      const r = state.rooms.find((x) => x.name === el.dataset.motion);
      el.hidden = state.offline || !(r && reading(r, "motion"));
      el.classList.toggle("dim", !!focus && focus !== el.dataset.motion);
    });
    const front = $("frontDoor");
    if (front) {
      const hall = state.rooms.find((x) => ROOM_IDS[x.name] === LAYOUT.front.room);
      front.classList.toggle("open", !!(hall && reading(hall, "door")));
    }
  }

  function drawHeader() {
    if (state.loading) return;  // the header's text is a skeleton until the house first answers
    const now = new Date();
    const days = ["Вс", "Пн", "Вт", "Ср", "Чт", "Пт", "Сб"];
    const months = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];
    $("date").textContent = `${days[now.getDay()]}, ${now.getDate()} ${months[now.getMonth()]} · ${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
    const summary = $("summary");
    const alarms = [];
    state.rooms.forEach((r) => dangersOf(r).forEach((k) => alarms.push((DANGER[k] || k) + " " + at(r.name))));
    if (state.offline) { summary.textContent = "Нет связи — показаны последние данные"; summary.className = "summary warn"; }
    else if (alarms.length) { summary.textContent = "Тревога: " + alarms.join(", "); summary.className = "summary alarm"; }
    else {
      const moving = state.rooms.filter((r) => reading(r, "motion")).map((r) => at(r.name));
      summary.textContent = "Всё спокойно" + (moving.length ? " · движение " + moving.join(" и ") : "");
      summary.className = "summary";
    }
    $("bellDot").hidden = !alarms.length;
    const guard = state.house && devOf(state.house, "security");
    const armed = !!(guard && guard.on);
    const card = $("armCard");
    card.classList.toggle("armed", armed);
    card.setAttribute("aria-pressed", armed ? "true" : "false");
    $("armIcon").innerHTML = icon(armed ? "shieldok" : "shield", 20);
    $("armTxt").textContent = state.offline || !guard ? "—" : armed ? "Под охраной" : "Снята";
    const hs = (state.house && state.house.house) || {};
    const watts = hs.power_now ? num(hs.power_now.value) : null;
    const today = hs.electricity_today ? num(hs.electricity_today.value) : null;
    $("powerNow").textContent = state.offline || watts === null ? "— кВт" : ru1(watts / 1000) + " кВт";
    $("powerDay").textContent = state.offline || today === null ? "нет данных" : ru1(today) + " кВт·ч сегодня";
    const w = state.weather;
    const temp = w && num(w.temperature);
    $("weatherT").textContent = temp === null || temp === undefined ? "—" : (temp > 0 ? "+" : "") + Math.round(temp) + "°";
    $("weatherW").textContent = (w && w.weather) || "";
    $("offline").hidden = !state.offline;
    $("app").classList.toggle("muted-app", state.offline);
  }

  function drawSkeleton() {
    const box = $("skeleton");
    box.hidden = !state.loading;
    $("stage").hidden = state.loading;
    if (!state.loading) return;
    let html = "";
    Object.values(LAYOUT.rooms).forEach((r) => {
      html += `<span class="skel" style="left:${30 + r.x * 0.94}px;top:${planTop() + r.y * 0.94}px;width:${r.w * 0.94 - 4}px;height:${r.h * 0.94 - 4}px"></span>`;
    });
    box.innerHTML = html;
    $("headText").innerHTML = '<span class="skel-line" style="width:120px;height:12px;margin-top:2px"></span><span class="skel-line" style="width:170px;height:26px;margin-top:6px"></span>';
  }

  // ---------------------------------------------------------------- the room's panel

  function sw(on, act, label) {
    return `<button class="switch${on ? " on" : ""}" role="switch" aria-checked="${on}" aria-label="${esc(label)}" data-act="${act}"><span class="trk"><span class="knob"></span></span></button>`;
  }

  function slider(value, min, max, step, act, label) {
    const pct = ((value - min) / (max - min)) * 100;
    return `<span class="slider"><span class="trk0"></span><span class="fill" style="width:${pct}%"></span><span class="thumb" style="left:${pct}%"></span>
      <input type="range" min="${min}" max="${max}" step="${step}" value="${value}" data-act="${act}" aria-label="${esc(label)}"></span>`;
  }

  let lastSheet = "";
  function drawSheet() {
    const sheet = $("sheet");
    const r = state.rooms.find((x) => x.name === (state.room || state.last));
    if (!r) { sheet.innerHTML = ""; return; }
    const body = sheet.querySelector(".sheet-body");
    const scroll = body ? body.scrollTop : 0;
    const t = num(reading(r, "temperature")), h = num(reading(r, "humidity")), co2 = num(reading(r, "carbon_dioxide"));
    const light = devOf(r, "light"), ac = devOf(r, "ac"), heat = devOf(r, "heating"), vent = devOf(r, "ventilation");
    const humid = devOf(r, "humidifier"), curtain = devOf(r, "curtains");
    const tNorm = r.norms.temperature, hNorm = r.norms.humidity_min, cNorm = r.norms.co2_max;
    let html = `<div class="sheet-head"><span class="handle"></span><div class="sheet-title"><div>
      <h2>${esc(r.name)}</h2><div class="meta mono"><span>${t === null ? "" : ru1(t) + "°C"}</span><span>${h === null ? "" : Math.round(h) + "%"}</span><span>${co2 === null ? "" : "CO₂ " + Math.round(co2)}</span></div></div>
      <button class="close jv-press" data-act="close" aria-label="Закрыть панель комнаты">${icon("close", 20)}</button></div></div><div class="sheet-body">`;

    if (light) {
      const bright = light.brightness_pct || 100;
      html += `<section class="sec"><h3>Свет</h3><div class="box">
        <div class="row"><span class="tile${light.on ? " on" : ""}">${icon("light", 20)}</span>
          <div class="lbl"><span class="a">Основной свет</span><span class="b">${light.on ? "Включён · " + bright + "%" : "Выключен"}</span></div>${sw(light.on, "dev:light", "Свет")}</div>
        <div class="row" style="opacity:${light.on ? 1 : 0.4}"><span class="b" style="font-size:12px;color:var(--text2);width:56px">Яркость</span>
          ${slider(bright, 1, 100, 1, "bright", "Яркость света")}<span class="mono" style="font-size:13px;width:40px;text-align:right">${bright}%</span></div>
      </div></section>`;
    }

    if (t !== null || tNorm || ac || heat) {
      const target = tNorm ? tNorm.value : null;
      let txt = "", color = "var(--text2)";
      if (target !== null && t !== null) {
        const diff = t - target;
        txt = "В норме — дом держит " + ru1(target) + "°";
        if (diff < -0.3) { txt = "Нагревается до " + ru1(target) + "°"; color = "var(--cyanText)"; }
        if (diff > 0.3) { txt = "Охлаждается до " + ru1(target) + "°"; color = "var(--cyanText)"; }
      }
      const cards = [];
      if (ac) cards.push(`<button class="clim-card jv-press${ac.on ? " on" : ""}" data-act="dev:ac" aria-pressed="${ac.on}"><span class="top">${icon("ac", 20)}<span class="dot"></span></span>
        <span><span class="n">Кондиционер</span><br><span class="s">${ac.on ? "Охлаждение" + (ac.target_temperature ? " до " + ruN(ac.target_temperature) + "°" : "") : "Выключен"}</span></span></button>`);
      if (heat) cards.push(`<button class="clim-card jv-press${heat.on ? " on" : ""}" data-act="dev:heating" aria-pressed="${heat.on}"><span class="top">${icon("heat", 20)}<span class="dot"></span></span>
        <span><span class="n">Отопление</span><br><span class="s">${heat.on ? (heat.action === "heating" ? "Греет" : "Держит") + (heat.target_temperature ? " " + ruN(heat.target_temperature) + "°" : "") : "Выключено"}</span></span></button>`);
      html += `<section class="sec"><h3>Климат</h3><div class="box" style="padding:16px;gap:14px">
        <div class="row between"><div style="display:flex;flex-direction:column;gap:2px"><span style="font-size:12px;color:var(--text2)">Сейчас</span>
          <span class="big-temp">${t === null ? "—" : ru1(t)}<small>°C</small></span><span style="font-size:12px;line-height:16px;color:${color}">${txt}</span></div>
          ${tNorm ? `<div style="display:flex;flex-direction:column;align-items:center;gap:6px"><span style="font-size:12px;color:var(--text2)">Норма</span>
            <div class="stepper"><button class="jv-press" data-act="norm:temperature:-0.5" aria-label="Понизить норму температуры">${icon("minus", 18)}</button>
            <span class="v" aria-live="polite">${ru1(target)}°</span><button class="jv-press" data-act="norm:temperature:0.5" aria-label="Повысить норму температуры">${icon("plus", 18)}</button></div></div>` : ""}</div>
        ${cards.length ? `<div class="clim" style="grid-template-columns:repeat(${cards.length},minmax(0,1fr))">${cards.join("")}</div>` : ""}
      </div></section>`;
    }

    if (h !== null || humid || vent || co2 !== null) {
      const hT = hNorm ? hNorm.value : null;
      const co2Max = cNorm ? cNorm.value : 800;
      const co2Color = co2 !== null && co2 > co2Max ? "var(--warn)" : "var(--cyan)";
      html += `<section class="sec"><h3>Воздух</h3><div class="box" style="padding:16px;gap:16px">
        <div class="row between"><div class="row"><span class="tile${humid && humid.on ? " on" : ""}">${icon("humid", 20)}</span>
          <div class="lbl"><span class="a">Влажность <span class="mono">${h === null ? "—" : Math.round(h) + "%"}</span></span>
          <span class="b">${humid ? (humid.on ? "Увлажнитель держит " + (hT || humid.target_humidity) + "%" : "Норма " + hT + "%") : "Только датчик"}</span></div></div>
          ${hNorm ? `<div class="stepper sm"><button class="jv-press" data-act="norm:humidity_min:-5" aria-label="Понизить норму влажности">${icon("minus", 16)}</button>
            <span class="v">${hT}%</span><button class="jv-press" data-act="norm:humidity_min:5" aria-label="Повысить норму влажности">${icon("plus", 16)}</button></div>` : ""}</div>
        ${humid ? `<div class="row between sep" style="padding-top:4px"><span style="font-size:14px">Увлажнитель</span>${sw(humid.on, "dev:humidifier", "Увлажнитель")}</div>` : ""}
        <div class="sep" style="display:flex;flex-direction:column;gap:10px">
          ${vent ? `<div class="row between"><div class="row" style="gap:10px"><span style="display:flex;color:${vent.on ? "var(--cyan)" : "var(--offIc)"}">${icon("vent", 20)}</span><span style="font-size:14px">Вентиляция</span></div>${sw(vent.on, "dev:ventilation", "Вентиляция")}</div>` : ""}
          ${co2 !== null ? `<div style="display:flex;flex-direction:column;gap:6px"><div class="row between" style="font-size:12px;color:var(--text2)"><span>CO₂ <span class="mono" style="color:${co2Color}">${Math.round(co2)} ppm</span></span><span>норма до ${Math.round(co2Max)}</span></div>
            <span class="co2-bar"><span class="v" style="width:${Math.max(0, Math.min(100, (co2 - 400) / 1100 * 100))}%;background:${co2Color}"></span><span class="mark" style="left:${Math.max(0, Math.min(100, (co2Max - 400) / 1100 * 100))}%"></span></span></div>` : ""}
        </div></div></section>`;
    }

    if (curtain) {
      const pos = curtain.position || 0;
      html += `<section class="sec"><h3>Шторы</h3><div class="box"><div class="row between"><span style="font-size:15px;font-weight:500">${pos === 0 ? "Закрыты" : pos >= 100 ? "Открыты полностью" : "Открыты на " + pos + "%"}</span>
        <div class="row" style="gap:6px"><button class="btn-s jv-press" data-act="curtain:0">Закрыть</button><button class="btn-s jv-press" data-act="curtain:100">Открыть</button></div></div>
        ${slider(pos, 0, 100, 5, "curtain", "Положение штор, процент открытия")}</div></section>`;
    }

    const apps = r.devices.filter((d) => ["socket", "water_valve", "gas_valve"].includes(d.type));
    if (apps.length) {
      html += `<section class="sec"><h3>Розетки и приборы</h3><div class="apps">` + apps.map((d) => {
        const type = TYPE[d.type];
        let sub = d.on ? "Включена" : "Выключена";
        if (d.type === "water_valve") sub = d.on ? (r.dangers.moisture ? "Открыт · ПРОТЕЧКА" : "Открыт · протечек нет") : "Перекрыт";
        if (d.type === "gas_valve") sub = d.on ? (r.dangers.gas ? "Открыт · УТЕЧКА" : "Открыт · утечек нет") : "Перекрыт · открытие с подтверждением";
        return `<div class="app-row"><span class="tile small${d.on ? " on" : ""}">${icon(type, 18)}</span>
          <div class="lbl"><span class="a" style="font-size:14px">${NAMES[type]}</span><span class="b">${sub}</span></div>${sw(d.on, "dev:" + d.type, NAMES[type])}</div>`;
      }).join("") + `</div></section>`;
    }

    html += `<section class="sec" style="padding-bottom:8px"><h3>За сутки</h3><div class="box chart" id="chart">${chartHtml(r.name)}</div></section>`;
    html += `</div>`;
    if (html !== lastSheet) {  // redrawn only when something changed - a tap mid-redraw would be lost
      lastSheet = html;
      sheet.innerHTML = html;
      const nb = sheet.querySelector(".sheet-body");
      if (nb) nb.scrollTop = scroll;
    }
    loadSeries(r.name);
  }

  function chartHtml(room) {
    const s = state.series[room];
    if (!s || !s.temperature) return '<span style="font-size:12px;color:var(--text2)">Загружаю историю…</span>';
    const temps = s.temperature.filter((v) => v !== null), hums = (s.humidity || []).filter((v) => v !== null);
    if (!temps.length) return '<span style="font-size:12px;color:var(--text2)">За сутки записей нет</span>';
    const line = (values, lo, hi) => values.map((v, i) => v === null ? null :
      `${(i * 300 / (values.length - 1)).toFixed(1)},${(76 - ((v - lo) / ((hi - lo) || 1)) * 72).toFixed(1)}`).filter(Boolean).join(" ");
    const tLo = Math.floor(Math.min(...temps) - 0.5), tHi = Math.ceil(Math.max(...temps) + 0.5);
    const hLo = hums.length ? Math.min(...hums) - 3 : 0, hHi = hums.length ? Math.max(...hums) + 3 : 1;
    return `<div class="chart-legend"><span><i style="background:var(--cyan)"></i>Температура ${tLo}–${tHi}°</span><span><i style="background:var(--blueLine)"></i>Влажность</span></div>
      <svg viewBox="0 0 300 80" preserveAspectRatio="none" role="img" aria-label="График температуры и влажности за сутки">
        <line x1="0" y1="20" x2="300" y2="20" class="grid"/><line x1="0" y1="40" x2="300" y2="40" class="grid"/><line x1="0" y1="60" x2="300" y2="60" class="grid"/>
        ${hums.length ? `<polyline points="${line(s.humidity, hLo, hHi)}" class="ln" stroke="var(--blueLine)"/>` : ""}
        <polyline points="${line(s.temperature, tLo, tHi)}" class="ln" stroke="var(--cyan)"/></svg>
      <div class="axis mono"><span>−24 ч</span><span>−18 ч</span><span>−12 ч</span><span>−6 ч</span><span>сейчас</span></div>`;
  }

  async function loadSeries(room) {
    const s = state.series[room];
    if (s && Date.now() - s.at < 5 * 60 * 1000) return;
    state.series[room] = Object.assign({}, s || {}, { at: Date.now() });
    try {
      const [t, h] = await Promise.all([api("GET", "/api/series?what=temperature&room=" + encodeURIComponent(room)),
                                        api("GET", "/api/series?what=humidity&room=" + encodeURIComponent(room))]);
      state.series[room] = { at: Date.now(), temperature: t.values, humidity: h.values };
      if ((state.room || state.last) === room) { lastSheet = ""; drawSheet(); }
    } catch (e) { /* the chart just stays as it is */ }
  }

  // ---------------------------------------------------------------- acting on the house

  async function control(room, device, action, extra) {
    const body = Object.assign({ room, device, action }, extra || {});
    try {
      let result = await api("POST", "/api/control", body);
      if (result.error && /confirmed=true/.test(result.error)) {
        if (!window.confirm("Нужно подтверждение. Сделать?")) { refresh(); return; }
        result = await api("POST", "/api/control", Object.assign(body, { confirmed: true }));
      }
      if (result.error) toast(result.error.split(" - ")[0], true);
    } catch (e) { if (e.message !== "locked") toast(e.message, true); }
    refresh();
  }

  function setLocal(roomName, type, patch) {  // the screen answers at once; the next refresh says what's true
    const r = state.rooms.find((x) => x.name === roomName) || state.house;
    const d = r && devOf(r, type);
    if (d) Object.assign(d, patch);
    drawPlanState();
    if (state.room || state.last) drawSheet();
    drawHeader();
  }

  function toggleDevice(roomName, type) {
    if (state.offline) return;
    const r = state.rooms.find((x) => x.name === roomName);
    const d = r && devOf(r, type);
    if (!d) return;
    const on = isOn(d);
    if (type === "gas_valve" && !on) { openGasConfirm(roomName); return; }
    if (type === "curtains") {
      setLocal(roomName, type, { position: on ? 0 : 100 });
      control(roomName, "curtains", on ? "off" : "on");
    } else {
      setLocal(roomName, type, { on: !on });
      control(roomName, type, on ? "off" : "on");
    }
    toast(`${NAMES[TYPE[type]]} · ${roomName} — ${on ? "выкл" : "вкл"}`);
  }

  async function setNorm(roomName, kind, delta) {
    const r = state.rooms.find((x) => x.name === roomName);
    const n = r && r.norms[kind];
    if (!n) return;
    const next = Math.round((n.value + delta) * 10) / 10;
    if ((n.min !== null && next < n.min) || (n.max !== null && next > n.max)) return;
    n.value = next;
    drawSheet();
    try {
      const body = { room: roomName };
      body[kind] = next;
      const result = await api("POST", "/api/norm", body);
      if (result.error) toast(result.error, true);
    } catch (e) { if (e.message !== "locked") toast(e.message, true); }
    refresh();
  }

  // --- the gas: open only by holding for two seconds

  let holdTimer = null, gasRoom = "";
  function openGasConfirm(roomName) {
    gasRoom = roomName;
    const r = state.rooms.find((x) => x.name === roomName);
    $("gasFacts").innerHTML = `<span><span>Датчик газа</span><b>${r && r.dangers.gas ? "ОБНАРУЖЕН ГАЗ" : "норма"}</b></span><span><span>Кто открывает</span><b>панель · телефон</b></span>`;
    $("confirm").hidden = false;
  }
  function closeGasConfirm() { clearTimeout(holdTimer); $("hold").classList.remove("holding"); $("confirm").hidden = true; }
  function holdStart(e) {
    if ($("confirm").hidden) return;
    e.preventDefault();
    clearTimeout(holdTimer);
    $("hold").classList.add("holding");
    state.busy = true;
    holdTimer = setTimeout(async () => {
      closeGasConfirm();
      state.busy = false;
      try {
        const result = await api("POST", "/api/control", { room: gasRoom, device: "gas_valve", action: "on", confirmed: true });
        toast(result.error ? result.error.split(" - ")[0] : "Кран газа открыт", !!result.error);
      } catch (err) { if (err.message !== "locked") toast(err.message, true); }
      refresh();
    }, 2000);
  }
  function holdEnd() { if (!$("hold").classList.contains("holding")) return; clearTimeout(holdTimer); $("hold").classList.remove("holding"); state.busy = false; }

  // ---------------------------------------------------------------- the room: zoom in and out

  function openRoom(name) {
    if (state.loading) return;
    state.room = name;
    state.last = name;
    $("app").classList.add("zoomed");
    $("zoomOut").hidden = false;
    drawSheet();
    drawPlanState();
    placePlan();
  }
  function closeRoom() {
    state.room = "";
    $("app").classList.remove("zoomed");
    $("zoomOut").hidden = true;
    drawPlanState();
    placePlan();
  }

  // ---------------------------------------------------------------- talking to the house

  async function refresh() {
    if (state.busy) return;
    try {
      const data = await api("GET", "/api/house");
      const rooms = data.rooms.filter((r) => r.name !== "Весь дом");
      const house = data.rooms.find((r) => r.name === "Весь дом") || null;
      const changed = !state.geometry || rooms.map((r) => r.name + ":" + r.devices.map((d) => d.type).join(",")).join("|") !==
        state.rooms.map((r) => r.name + ":" + r.devices.map((d) => d.type).join(",")).join("|");
      state.rooms = rooms;
      state.house = house;
      state.offline = false;
      if (state.loading) { state.loading = false; restoreHeadText(); }
      if (changed) { state.geometry = geometry(rooms); buildPlan(); }
    } catch (e) {
      if (e.message === "locked") return;
      state.offline = true;
    }
    drawSkeleton();
    drawPlanState();
    drawHeader();
    placePlanIfHeaderMoved();
    if (state.room) drawSheet();
    const shown = screens[$("app").getAttribute("data-screen")];
    if (shown && shown.tick) shown.tick();  // the open screen redraws from the fresh house too
  }

  async function loadWeather() {
    try {
      const w = await api("GET", "/api/weather");
      if (w.now) { state.weather = { temperature: w.now.temperature, weather: w.now.weather }; drawHeader(); }
    } catch (e) { /* the card keeps a dash */ }
  }

  function restoreHeadText() {
    $("headText").innerHTML = '<span class="date mono" id="date"></span><h1 class="title">Квартира</h1><span class="summary" id="summary"></span>';
  }

  // ---------------------------------------------------------------- the PIN

  function showLock(message) {
    $("lock").hidden = false;
    $("lockErr").textContent = message || "";
    $("pin").value = "";
  }

  $("lockForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    pin = $("pin").value.trim();
    try {
      await api("GET", "/api/ping");
      store.set("jarvis-panel-pin", pin);
      $("lock").hidden = true;
      start();
    } catch (err) { if (err.message !== "locked") $("lockErr").textContent = err.message; }
  });

  // ---------------------------------------------------------------- wiring

  function wire() {
    document.querySelectorAll("[data-icon]").forEach((el) => { el.innerHTML = icon(el.dataset.icon, el.dataset.size || 20); });
    const tabs = [["home", "Дом"], ["scenes", "Сценарии"], ["shield", "Охрана"], ["chart", "Энергия"], ["sliders", "Настройки"]];
    $("nav").innerHTML = tabs.map((t, i) => `<button class="${i === 0 ? "cur" : ""}" data-tab="${t[0]}" aria-current="${i === 0 ? "page" : "false"}"><span class="bar"></span>${icon(t[0], 22)}${t[1]}</button>`).join("");
    $("nav").addEventListener("click", (e) => {
      const b = e.target.closest("button");
      if (b) showScreen(b.dataset.tab);
    });
    $("plan").addEventListener("click", (e) => {
      const dev = e.target.closest(".p-dev");
      if (dev) { e.stopPropagation(); toggleDevice(dev.dataset.room, dev.dataset.dev); return; }
      const room = e.target.closest(".p-room");
      if (room) openRoom(room.dataset.room);
    });
    $("zoomOut").addEventListener("click", closeRoom);
    $("back").addEventListener("click", closeRoom);
    $("armCard").addEventListener("click", () => {
      if (state.offline || !state.house) return;
      const guard = devOf(state.house, "security");
      if (!guard) return;
      setLocal("Весь дом", "security", { on: !guard.on });
      control("весь дом", "security", guard.on ? "on" : "off");
      toast(guard.on ? "Охрана включена" : "Охрана снята");
    });
    $("powerCard").addEventListener("click", () => toast("Раздел «Энергия» — в следующей версии"));
    $("bell").addEventListener("click", () => toast("Журнал событий — в следующей версии"));
    $("orb").addEventListener("click", () => toast("Чат с Jarvis — в следующей версии"));
    $("retry").addEventListener("click", refresh);
    $("cancel").addEventListener("click", closeGasConfirm);
    const hold = $("hold");
    hold.addEventListener("pointerdown", holdStart);
    hold.addEventListener("pointerup", holdEnd);
    hold.addEventListener("pointerleave", holdEnd);
    hold.addEventListener("pointercancel", holdEnd);

    const sheet = $("sheet");
    sheet.addEventListener("click", (e) => {
      const b = e.target.closest("[data-act]");
      if (!b || b.tagName === "INPUT") return;
      const room = state.room || state.last;
      const [act, a, c] = b.dataset.act.split(":");
      if (act === "close") closeRoom();
      if (act === "dev") toggleDevice(room, a);
      if (act === "norm") setNorm(room, a, parseFloat(c));
      if (act === "curtain") { setLocal(room, "curtains", { position: +a }); control(room, "curtains", +a ? "on" : "off"); }
    });
    sheet.addEventListener("input", (e) => {  // the slider moves with the finger
      const input = e.target;
      if (!input.dataset.act) return;
      state.busy = true;
      const s = input.closest(".slider");
      const pct = ((input.value - input.min) / (input.max - input.min)) * 100;
      s.querySelector(".fill").style.width = pct + "%";
      s.querySelector(".thumb").style.left = pct + "%";
    });
    sheet.addEventListener("change", (e) => {
      const input = e.target;
      if (!input.dataset.act) return;
      state.busy = false;
      const room = state.room || state.last, value = parseInt(input.value, 10);
      if (input.dataset.act === "bright") { setLocal(room, "light", { on: true, brightness_pct: value }); control(room, "light", "on", { brightness_pct: value }); }
      if (input.dataset.act === "curtain") {
        setLocal(room, "curtains", { position: value });
        control(room, "curtains", value ? "on" : "off", value > 0 && value < 100 ? { position: value } : null);
      }
    });
    window.addEventListener("resize", fit);
    const theme = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)");
    const applyTheme = () => $("root").setAttribute("data-theme", theme && theme.matches ? "light" : "dark");
    applyTheme();
    if (theme && theme.addEventListener) theme.addEventListener("change", applyTheme);
  }

  let started = false;
  function start() {
    if (started) { refresh(); return; }
    started = true;
    refresh();
    loadWeather();
    setInterval(() => { if (!document.hidden) refresh(); }, REFRESH_MS);
    setInterval(() => { drawHeader(); placePlanIfHeaderMoved(); }, 20000);
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(placePlanIfHeaderMoved);  // Onest is taller than the fallback
    setInterval(loadWeather, 10 * 60 * 1000);
  }

  // ---------------------------------------------------------------- the other screens (screens.js)

  const screens = {};
  function showScreen(name) {
    if (name !== "home" && !screens[name]) { toast("Раздел — в следующей версии"); return; }
    if (state.room) closeRoom();
    $("app").setAttribute("data-screen", name);
    $("nav").querySelectorAll("button").forEach((b) => {
      const cur = b.dataset.tab === name;
      b.classList.toggle("cur", cur);
      b.setAttribute("aria-current", cur ? "page" : "false");
    });
    if (screens[name]) screens[name].open();
  }
  // what screens.js builds on: one way to ask the server, one toast, one icon set
  window.JV = { api, toast, icon, esc, $, store, show: showScreen, add: (name, screen) => { screens[name] = screen; },
    house: () => ({ rooms: state.rooms, house: state.house, offline: state.offline }) };

  // the phone's back button (the Android app asks first): close what is open, else leave the app
  window.jarvisBack = () => {
    if (!$("confirm").hidden) { closeGasConfirm(); return true; }
    if (state.room) { closeRoom(); return true; }
    const current = $("app").getAttribute("data-screen");
    if (current && current !== "home") { showScreen("home"); return true; }
    return false;
  };

  wire();
  fit();
  drawSkeleton();
  if (pin) start(); else showLock();
})();
