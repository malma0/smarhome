/* Jarvis control panel. Plain ES5 and XMLHttpRequest on purpose: it has to
   run on an old iPad's Safari as well as on a phone. */
(function () {
  "use strict";

  var PIN_KEY = "jarvis-panel-pin";
  var REFRESH_MS = 5000;
  var pin = load(PIN_KEY);
  var timer = null;
  var busy = false;

  var NAMES = {
    light: "Свет", socket: "Розетка", ac: "Кондиционер", heating: "Отопление", ventilation: "Вентиляция",
    humidifier: "Увлажнитель", curtains: "Шторы", water_valve: "Кран воды", gas_valve: "Кран газа",
    security: "Охрана"
  };
  var ORDER = ["light", "curtains", "socket", "ventilation", "ac", "heating", "humidifier", "water_valve", "gas_valve"];

  function $(id) { return document.getElementById(id); }

  function load(key) {
    try { return window.localStorage.getItem(key) || ""; } catch (e) { return ""; }
  }

  function save(key, value) {
    try { window.localStorage.setItem(key, value); } catch (e) { /* private mode: asked again next time */ }
  }

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) { node.className = cls; }
    if (text !== undefined && text !== null) { node.appendChild(document.createTextNode(String(text))); }
    return node;
  }

  function api(method, path, body, done) {
    var xhr = new XMLHttpRequest();
    xhr.open(method, path, true);
    xhr.setRequestHeader("X-Pin", pin);
    if (body) { xhr.setRequestHeader("Content-Type", "application/json"); }
    xhr.onreadystatechange = function () {
      if (xhr.readyState !== 4) { return; }
      var data = null;
      try { data = JSON.parse(xhr.responseText); } catch (e) { data = null; }
      if (xhr.status === 401) { showLock("Неверный PIN."); return; }
      if (xhr.status === 429) { showLock((data && data.detail) || "Подожди минуту."); return; }
      if (xhr.status !== 200) {
        toast((data && data.detail) || ("Ошибка " + xhr.status), true);
        if (done) { done(null); }
        return;
      }
      if (done) { done(data); }
    };
    xhr.send(body ? JSON.stringify(body) : null);
  }

  var toastTimer = null;
  function toast(text, bad) {
    var box = $("toast");
    box.textContent = text;
    box.className = "toast" + (bad ? " bad" : "");
    box.hidden = false;
    if (toastTimer) { clearTimeout(toastTimer); }
    toastTimer = setTimeout(function () { box.hidden = true; }, 3500);
  }

  // --- the PIN ---

  function showLock(message) {
    stopRefresh();
    $("app").hidden = true;
    $("lock").hidden = false;
    $("lockError").textContent = message || "";
    $("pin").value = "";
  }

  $("lockForm").onsubmit = function (e) {
    e.preventDefault();
    pin = $("pin").value.replace(/^\s+|\s+$/g, "");
    api("GET", "/api/ping", null, function (data) {
      if (data && data.ok) {
        save(PIN_KEY, pin);
        $("pin").value = "";
        start();
      }
    });
  };

  // --- what a control says back ---

  function answer(result, retry) {
    if (!result) { return; }
    if (result.error) {
      if (/confirmed=true/.test(result.error) && retry) {
        if (window.confirm("Нужно подтверждение: " + result.error.split(" - ")[0] + ". Сделать?")) { retry(); }
        return;
      }
      toast(result.error, true);
      return;
    }
    if (result.does) { toast(result.ran + ": " + result.does); }
    refresh();
  }

  function control(room, device, action, extra) {
    var body = { room: room, device: device, action: action };
    for (var key in extra || {}) { if (extra.hasOwnProperty(key)) { body[key] = extra[key]; } }
    api("POST", "/api/control", body, function (result) {
      answer(result, function () {
        body.confirmed = true;
        api("POST", "/api/control", body, function (again) { answer(again); });
      });
    });
  }

  // --- the house ---

  function reading(room, key) {
    var r = room.readings[key];
    return r ? r.value : null;
  }

  function number(value) {
    var n = parseFloat(value);
    return isNaN(n) ? null : Math.round(n * 10) / 10;
  }

  function ru(n) { return String(n).replace(".", ","); }  // 0,6 кВт·ч

  function drawHouse(rooms) {
    var houseBox = $("houseCard");
    var list = $("rooms");
    houseBox.innerHTML = "";
    list.innerHTML = "";
    var house = null, dangers = [];
    for (var i = 0; i < rooms.length; i++) {
      var r = rooms[i];
      if (r.name === "Весь дом") { house = r; continue; }
      for (var d in r.dangers) { if (r.dangers.hasOwnProperty(d) && r.dangers[d]) { dangers.push(r.name); } }
      list.appendChild(drawRoom(r));
    }
    houseBox.appendChild(drawHouseCard(house, dangers));
    var stamp = new Date();
    $("status").textContent = "обновлено " + ("0" + stamp.getHours()).slice(-2) + ":" + ("0" + stamp.getMinutes()).slice(-2);
  }

  function drawHouseCard(house, dangers) {
    var card = el("div", "card house-card");
    if (dangers.length) {
      card.appendChild(el("div", "stat", null)).appendChild(el("b", "error", "Тревога: " + dangers.join(", ")));
    }
    if (house && house.house.power_now) {
      var stat = el("div", "stat");
      stat.appendChild(el("b", null, ru(number(house.house.power_now.value)) + " Вт"));
      stat.appendChild(el("span", null, "сейчас"));
      card.appendChild(stat);
    }
    if (house && house.house.electricity_today) {
      var today = el("div", "stat");
      today.appendChild(el("b", null, ru(number(house.house.electricity_today.value)) + " кВт·ч"));
      today.appendChild(el("span", null, "сегодня"));
      card.appendChild(today);
    }
    var guard = null;
    if (house) {
      for (var i = 0; i < house.devices.length; i++) { if (house.devices[i].type === "security") { guard = house.devices[i]; } }
    }
    if (guard) {
      var button = el("button", "toggle" + (guard.on ? " on" : ""), guard.on ? "Охрана включена" : "Охрана выключена");
      button.onclick = function () { control("весь дом", "security", guard.on ? "off" : "on"); };
      card.appendChild(button);
    }
    return card;
  }

  function drawRoom(room) {
    var card = el("div", "card");
    var head = el("div", "room-head");
    head.appendChild(el("div", "room-name", room.name));
    var parts = [];
    var t = number(reading(room, "temperature")), h = number(reading(room, "humidity")), c = number(reading(room, "carbon_dioxide"));
    if (t !== null) { parts.push(ru(t) + "°"); }
    if (h !== null) { parts.push(Math.round(h) + "%"); }
    if (c !== null) { parts.push(Math.round(c) + " ppm"); }
    head.appendChild(el("div", "readings", parts.join(" · ")));
    card.appendChild(head);

    var badges = el("div", "badges");
    if (reading(room, "window")) { badges.appendChild(el("span", "badge warn", "окно открыто")); }
    if (reading(room, "door")) { badges.appendChild(el("span", "badge warn", "дверь открыта")); }
    if (reading(room, "motion")) { badges.appendChild(el("span", "badge live", "движение")); }
    for (var d in room.dangers) {
      if (room.dangers.hasOwnProperty(d) && room.dangers[d]) { badges.appendChild(el("span", "badge warn", "тревога: " + d)); }
    }
    if (badges.childNodes.length) { card.appendChild(badges); }

    var devices = room.devices.slice().sort(function (a, b) { return ORDER.indexOf(a.type) - ORDER.indexOf(b.type); });
    for (var i = 0; i < devices.length; i++) { card.appendChild(drawDevice(room, devices[i])); }
    if (room.norms.temperature) { card.appendChild(drawNorm(room, "temperature", "Держать", "°", 0.5)); }
    if (room.norms.humidity_min) { card.appendChild(drawNorm(room, "humidity_min", "Влажность не ниже", "%", 5)); }
    return card;
  }

  function drawDevice(room, device) {
    var row = el("div", "row");
    var label = el("div", "label", NAMES[device.type] || device.type);
    var sub = "";
    if (device.type === "light" && device.on && device.brightness_pct) { sub = device.brightness_pct + "%"; }
    if ((device.type === "ac" || device.type === "heating") && device.on && device.target_temperature) {
      sub = "до " + ru(device.target_temperature) + "°" + (device.action === "cooling" || device.action === "heating" ? ", работает" : "");
    }
    if (device.type === "humidifier" && device.on && device.target_humidity) { sub = "держит " + device.target_humidity + "%"; }
    if (device.type === "curtains") {
      sub = !device.position ? "закрыты" : device.position >= 100 ? "открыты" : "открыты на " + device.position + "%";
    }
    if (sub) { label.appendChild(el("span", "sub", sub)); }
    row.appendChild(label);

    if (device.type === "curtains") {
      var group = el("div", "group");
      var buttons = [["Открыть", 100], ["½", 50], ["Закрыть", 0]];
      for (var i = 0; i < buttons.length; i++) {
        (function (title, position) {
          var b = el("button", null, title);
          b.onclick = function () { control(room.name, "curtains", position ? "on" : "off", position && position < 100 ? { position: position } : null); };
          group.appendChild(b);
        })(buttons[i][0], buttons[i][1]);
      }
      row.appendChild(group);
      return row;
    }

    var onWord = device.type.indexOf("valve") >= 0 ? (device.on ? "Открыт" : "Закрыт") : (device.on ? "Вкл" : "Выкл");
    var toggle = el("button", "toggle" + (device.on ? " on" : ""), onWord);
    toggle.onclick = function () { control(room.name, device.type, device.on ? "off" : "on"); };
    row.appendChild(toggle);

    if (device.type === "light") {
      var wrap = el("div", "row");
      var slider = el("input");
      slider.type = "range"; slider.min = 1; slider.max = 100; slider.step = 1;
      slider.value = device.on ? (device.brightness_pct || 100) : 50;
      slider.onchange = function () { control(room.name, "light", "on", { brightness_pct: parseInt(slider.value, 10) }); };
      slider.ontouchstart = slider.onmousedown = function () { busy = true; };
      slider.ontouchend = slider.onmouseup = function () { busy = false; };
      var box = el("div", "");
      box.appendChild(row);
      wrap.style.borderTop = "0";
      wrap.style.paddingTop = "0";
      wrap.appendChild(slider);
      box.appendChild(wrap);
      return box;
    }
    return row;
  }

  function drawNorm(room, kind, title, unit, step) {
    var norm = room.norms[kind];
    var row = el("div", "row");
    row.appendChild(el("div", "label", title));
    var stepper = el("div", "stepper");
    var minus = el("button", null, "−"), plus = el("button", null, "+");
    var value = el("div", "value", ru(norm.value) + unit);
    function set(delta) {
      var next = Math.round((norm.value + delta) * 10) / 10;
      if ((norm.min !== null && next < norm.min) || (norm.max !== null && next > norm.max)) { return; }
      var body = { room: room.name };
      body[kind] = next;
      norm.value = next;
      value.textContent = ru(next) + unit;
      api("POST", "/api/norm", body, function (result) { answer(result); });
    }
    minus.onclick = function () { set(-step); };
    plus.onclick = function () { set(step); };
    stepper.appendChild(minus); stepper.appendChild(value); stepper.appendChild(plus);
    row.appendChild(stepper);
    return row;
  }

  function refresh() {
    if (busy) { return; }
    api("GET", "/api/house", null, function (data) { if (data) { drawHouse(data.rooms); } });
  }

  function startRefresh() {
    stopRefresh();
    refresh();
    timer = setInterval(function () { if (!document.hidden && !$("tab-house").hidden) { refresh(); } }, REFRESH_MS);
  }

  function stopRefresh() { if (timer) { clearInterval(timer); timer = null; } }

  // --- scenarios and schedules ---

  function loadScenes() {
    api("GET", "/api/scenarios", null, function (data) {
      var box = $("scenes");
      box.innerHTML = "";
      var list = (data && data.scenarios) || [];
      for (var i = 0; i < list.length; i++) {
        (function (name) {
          var b = el("button", null, name);
          b.onclick = function () { api("POST", "/api/scenarios/run", { name: name }, function (result) { answer(result); }); };
          box.appendChild(b);
        })(list[i].name);
      }
      if (!list.length) { box.appendChild(el("p", "muted", "Сценариев нет.")); }
    });
    api("GET", "/api/schedules", null, function (data) {
      var box = $("schedules");
      box.innerHTML = "";
      var list = (data && data.schedules) || [];
      for (var i = 0; i < list.length; i++) { box.appendChild(drawSchedule(list[i])); }
      if (!list.length) { box.appendChild(el("p", "muted", "Расписаний нет.")); }
    });
  }

  function drawSchedule(item) {
    var card = el("div", "card schedule");
    card.appendChild(el("div", "label", item.name.charAt(0).toUpperCase() + item.name.slice(1)));
    if (item.time) {
      var time = el("input");
      time.type = "time"; time.value = item.time;
      time.onchange = function () {
        api("POST", "/api/schedules", { action: "set_time", name: item.name, time: time.value }, function (result) {
          if (result && !result.error) { toast("«" + item.name + "» теперь в " + time.value); } else { answer(result); }
        });
      };
      card.appendChild(time);
    }
    var toggle = el("button", "toggle" + (item.on ? " on" : ""), item.on ? "Вкл" : "Выкл");
    toggle.onclick = function () {
      api("POST", "/api/schedules", { action: item.on ? "disable" : "enable", name: item.name }, function (result) {
        if (result && !result.error) { loadScenes(); } else { answer(result); }
      });
    };
    card.appendChild(toggle);
    return card;
  }

  // --- chat ---

  function say(text, who) {
    var box = $("messages");
    box.appendChild(el("div", "msg " + who, text));
    window.scrollTo(0, document.body.scrollHeight);
  }

  $("chatForm").onsubmit = function (e) {
    e.preventDefault();
    var input = $("chatInput");
    var text = input.value.replace(/^\s+|\s+$/g, "");
    if (!text) { return; }
    input.value = "";
    say(text, "me");
    api("POST", "/api/chat", { message: text }, function (data) {
      if (data) { say(data.response, "jarvis"); }
    });
  };

  // --- tabs ---

  var tabs = document.querySelectorAll(".tabs button");
  for (var i = 0; i < tabs.length; i++) {
    tabs[i].onclick = function () {
      var name = this.getAttribute("data-tab");
      for (var j = 0; j < tabs.length; j++) { tabs[j].className = tabs[j] === this ? "active" : ""; }
      $("tab-house").hidden = name !== "house";
      $("tab-scenes").hidden = name !== "scenes";
      $("tab-chat").hidden = name !== "chat";
      if (name === "house") { refresh(); }
      if (name === "scenes") { loadScenes(); }
    };
  }

  function start() {
    $("lock").hidden = true;
    $("app").hidden = false;
    startRefresh();
  }

  if (pin) {
    api("GET", "/api/ping", null, function (data) { if (data && data.ok) { start(); } });
  } else {
    showLock();
  }
})();
