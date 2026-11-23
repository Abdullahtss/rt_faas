(function () {
  var live = [];
  var alerts = [];
  var queue = [];
  var latencies = [];
  var selected = null;
  var connected = false;
  var simRunning = false;
  var backoff = 1000;
  var ws = null;

  var liveList = document.getElementById("live-list");
  var alertList = document.getElementById("alert-list");
  var queueList = document.getElementById("queue-list");
  var caseBox = document.getElementById("case");
  var conn = document.getElementById("conn");
  var p95El = document.getElementById("p95");
  var feedCount = document.getElementById("feed-count");
  var alertCount = document.getElementById("alert-count");
  var btnStream = document.getElementById("btn-stream");

  function beep() {
    try {
      var ctx = new AudioContext();
      var osc = ctx.createOscillator();
      var gain = ctx.createGain();
      osc.frequency.value = 880;
      gain.gain.value = 0.05;
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start();
      osc.stop(ctx.currentTime + 0.18);
    } catch (e) { /* ignore */ }
  }

  function p95() {
    if (!latencies.length) return 0;
    var s = latencies.slice().sort(function (a, b) { return a - b; });
    return s[Math.min(s.length - 1, Math.floor(s.length * 0.95))];
  }

  function pick(item) {
    selected = item;
    draw();
  }

  function ingestItem(item, isAlert, replay) {
    live = upsert(live, item);
    if (item.processing_ms) {
      latencies = latencies.concat([item.processing_ms]).slice(-200);
    }
    if (isAlert || item.level === "High") {
      alerts = upsert(alerts, item, 80);
      if (typeof item.seq === "number") {
        var stored = Number(localStorage.getItem(SEQ_KEY) || "0");
        if (item.seq > stored) localStorage.setItem(SEQ_KEY, String(item.seq));
      }
      if (!replay) beep();
    }
    if ((item.level === "Medium" || item.level === "High") && !item.review) {
      queue = upsert(queue, item, 80);
    }
    if (item.review) {
      queue = queue.filter(function (x) { return x.id !== item.id; });
    }
    if (selected && selected.id === item.id) selected = item;
    draw();
  }

  function renderCase() {
    caseBox.innerHTML = "";
    var h = document.createElement("h2");
    h.textContent = "Case file";
    caseBox.appendChild(h);
    if (!selected) {
      var p = document.createElement("p");
      p.className = "muted";
      p.textContent = "Select a transaction to inspect reasons and take a decision.";
      caseBox.appendChild(p);
      return;
    }
    var t = selected;
    var dl = document.createElement("dl");
    function kv(k, v, html) {
      var dt = document.createElement("dt");
      dt.textContent = k;
      var dd = document.createElement("dd");
      if (html) dd.appendChild(v);
      else dd.textContent = v;
      dl.appendChild(dt);
      dl.appendChild(dd);
    }
    kv("Account", t.account_masked);
    kv("Amount", money(t.amount, t.currency));
    kv("Where", (t.city || "—") + " · " + t.merchant);
    kv("BankSim", t.scenario === "banksim-fraud" ? "marked fraud in the file" : "genuine in the file");
    // Verdict is whichever side was more severe; the two rows under it show
    // what each side said on its own.
    var verdict = document.createElement("span");
    verdict.className = "level " + t.level;
    verdict.textContent = t.level;
    kv("Verdict", verdict, true);

    var ruleLvl = t.rule_level || t.level;
    var lvl = document.createElement("span");
    lvl.className = "level " + ruleLvl;
    lvl.textContent = ruleLvl + " " + t.score + "/100";
    kv("Rules", lvl, true);

    if (typeof t.ml_score === "number") {
      var mlLvl = t.ml_level || "Low";
      var ml = document.createElement("span");
      ml.className = "level " + mlLvl;
      ml.textContent = mlLvl + " " + t.ml_score + "/100";
      kv("Model", ml, true);
    }
    if (typeof t.anomaly_pct === "number") {
      var anLvl = t.anomaly_level || "Low";
      var an = document.createElement("span");
      an.className = "level " + anLvl;
      an.textContent = anLvl;
      var anWrap = document.createElement("span");
      anWrap.appendChild(an);
      var anText = document.createElement("small");
      anText.className = "muted";
      anText.textContent = " rarer than " + t.anomaly_pct.toFixed(1) + "% of normal payments";
      anWrap.appendChild(anText);
      kv("Anomaly", anWrap, true);
    }
    kv("Latency", Number(t.processing_ms).toFixed(1) + " ms");
    kv("Review", t.review || "pending");
    caseBox.appendChild(dl);
    var rulesHead = document.createElement("p");
    rulesHead.className = "src-head";
    rulesHead.textContent = "Rule reasons";
    caseBox.appendChild(rulesHead);
    var ul = document.createElement("ul");
    ul.className = "reasons";
    (t.reasons || []).forEach(function (r) {
      var li = document.createElement("li");
      var b = document.createElement("b");
      b.textContent = r.rule + " +" + r.points;
      var span = document.createElement("span");
      span.textContent = r.detail;
      li.appendChild(b);
      li.appendChild(span);
      ul.appendChild(li);
    });
    caseBox.appendChild(ul);
    if ((t.ml_reasons || []).length) {
      var mlHead = document.createElement("p");
      mlHead.className = "src-head";
      mlHead.textContent = "Model reasons";
      caseBox.appendChild(mlHead);
      var mul = document.createElement("ul");
      mul.className = "reasons ml";
      t.ml_reasons.forEach(function (r) {
        var li = document.createElement("li");
        var b = document.createElement("b");
        var pts = Math.round(r.contribution * 100);
        b.textContent = r.feature + " +" + pts;
        var span = document.createElement("span");
        span.textContent = r.detail;
        var bar = document.createElement("div");
        bar.className = "bar";
        var fill = document.createElement("i");
        fill.style.width = Math.max(4, Math.min(100, pts)) + "%";
        bar.appendChild(fill);
        li.appendChild(b);
        li.appendChild(span);
        li.appendChild(bar);
        mul.appendChild(li);
      });
      caseBox.appendChild(mul);
    }
    if (!t.review && (t.level === "Medium" || t.level === "High")) {
      var notes = document.createElement("textarea");
      notes.placeholder = "Analyst notes";
      var actions = document.createElement("div");
      actions.className = "actions";
      var approve = document.createElement("button");
      approve.className = "ok";
      approve.type = "button";
      approve.textContent = "Approve";
      var block = document.createElement("button");
      block.className = "bad";
      block.type = "button";
      block.textContent = "Block";
      function decide(decision) {
        apiPost("/api/v1/transactions/" + t.id + "/review", {
          decision: decision,
          notes: notes.value,
          reviewer: "analyst",
        }).then(function (updated) {
          ingestItem(updated, false, true);
        }).catch(function (err) { showBanner(String(err)); });
      }
      approve.addEventListener("click", function () { decide("approved"); });
      block.addEventListener("click", function () { decide("blocked"); });
      actions.appendChild(approve);
      actions.appendChild(block);
      caseBox.appendChild(notes);
      caseBox.appendChild(actions);
    }
  }

  function draw() {
    conn.textContent = connected ? "Live" : "Reconnecting";
    conn.className = "pill " + (connected ? "ok" : "bad");
    p95El.textContent = latencies.length ? p95().toFixed(1) : "—";
    feedCount.textContent = String(live.length);
    alertCount.textContent = String(alerts.length);
    btnStream.textContent = simRunning ? "Stop BankSim" : "Play BankSim";
    renderList(liveList, live, selected && selected.id, pick);
    renderList(alertList, alerts, selected && selected.id, pick);
    renderList(queueList, queue, selected && selected.id, pick);
    renderCase();
  }

  function openWs() {
    var lastSeq = Number(localStorage.getItem(SEQ_KEY) || "0");
    var proto = location.protocol === "https:" ? "wss" : "ws";
    var url = proto + "://" + location.host + "/api/v1/ws/alerts?token=" +
      encodeURIComponent(TOKEN) + "&last_seq=" + lastSeq;
    ws = new WebSocket(url);
    var pingTimer;
    ws.onopen = function () {
      connected = true;
      backoff = 1000;
      draw();
      pingTimer = setInterval(function () {
        if (ws && ws.readyState === 1) ws.send("ping");
      }, 12000);
    };
    ws.onmessage = function (ev) {
      var msg;
      try { msg = JSON.parse(ev.data); } catch (e) { return; }
      if (msg.type === "transaction" && msg.data && !msg.replay) ingestItem(msg.data, false);
      if (msg.type === "alert" && msg.data) {
        if (msg.replay) alerts = upsert(alerts, msg.data, 20);
        else ingestItem(msg.data, true, false);
        draw();
      }
      if (msg.type === "review" && msg.data) ingestItem(msg.data, false, true);
    };
    ws.onclose = function () {
      connected = false;
      if (pingTimer) clearInterval(pingTimer);
      draw();
      setTimeout(openWs, backoff);
      backoff = Math.min(15000, backoff * 2);
    };
  }

  btnStream.addEventListener("click", function () {
    var req = simRunning
      ? apiPost("/api/v1/simulator/stop", {})
      : apiPost("/api/v1/simulator/start", { tps: 2, running: true });
    req.then(function (st) {
      simRunning = Boolean(st.running);
      draw();
    }).catch(function (err) { showBanner(String(err)); });
  });
  document.getElementById("btn-fraud").addEventListener("click", function () {
    apiPost("/api/v1/simulator/next-fraud", {})
      .then(function (item) { ingestItem(item, item.level === "High", true); })
      .catch(function (err) { showBanner(String(err)); });
  });

  apiGet("/api/v1/model").then(function (m) {
    var el = document.getElementById("model-name");
    if (!el) return;
    el.textContent = m.available ? m.model.replace(/_/g, " ") : "rules only";
    if (m.metrics && m.metrics.test && m.metrics.test.model) {
      var r = m.metrics.test.model;
      el.title = "Held-out test: precision " + r.precision + ", recall " + r.recall +
        ", PR-AUC " + r.pr_auc;
    }
  }).catch(function () { /* model panel is optional */ });

  Promise.all([
    apiGet("/api/v1/transactions"),
    apiGet("/api/v1/reviews/queue"),
    apiGet("/api/v1/simulator/status"),
  ]).then(function (parts) {
    live = parts[0];
    queue = parts[1];
    alerts = live.filter(function (t) { return t.level === "High"; });
    latencies = live.map(function (t) { return t.processing_ms; }).filter(Boolean);
    simRunning = Boolean(parts[2].running);
    draw();
  }).catch(function () {
    showBanner("Backend not reachable. Start the server on port 8000.");
  });

  openWs();
  draw();
})();
