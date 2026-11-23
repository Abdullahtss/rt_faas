(function () {
  var SVGNS = "http://www.w3.org/2000/svg";
  var curve = [];
  var liveThreshold = null;
  var sliderTouched = false;
  var polling = null;

  function $(id) { return document.getElementById(id); }
  function pct(x) { return x === null || x === undefined ? "—" : (100 * x).toFixed(1) + "%"; }
  function num(x, d) { return x === null || x === undefined ? "—" : Number(x).toFixed(d === undefined ? 3 : d); }

  function kpis(el, items) {
    el.innerHTML = "";
    items.forEach(function (it) {
      var d = document.createElement("div");
      var s = document.createElement("small");
      s.textContent = it[0];
      var b = document.createElement("b");
      b.textContent = it[1];
      d.appendChild(s);
      d.appendChild(b);
      if (it[2]) d.title = it[2];
      el.appendChild(d);
    });
  }

  function svgEl(name, attrs, text) {
    var e = document.createElementNS(SVGNS, name);
    Object.keys(attrs).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function hbars(el, rows, max, cls) {
    el.innerHTML = "";
    if (!rows.length) {
      var p = document.createElement("p");
      p.className = "muted";
      p.textContent = "Nothing yet.";
      el.appendChild(p);
      return;
    }
    rows.forEach(function (r) {
      var row = document.createElement("div");
      row.className = "hbar";
      var name = document.createElement("span");
      name.textContent = r[0];
      name.title = r[0];
      var track = document.createElement("span");
      track.className = "track";
      var fill = document.createElement("i");
      if (r[3] || cls) fill.className = r[3] || cls;
      fill.style.width = (max ? Math.max(2, 100 * r[1] / max) : 0) + "%";
      track.appendChild(fill);
      var val = document.createElement("b");
      val.textContent = r[2] !== undefined ? r[2] : r[1];
      row.appendChild(name);
      row.appendChild(track);
      row.appendChild(val);
      el.appendChild(row);
    });
  }

  // ---- threshold curve ------------------------------------------------
  function drawCurve(idx) {
    var svg = $("curve");
    svg.innerHTML = "";
    if (!curve.length) return;
    var W = 640, H = 170, L = 34, R = 10, T = 8, B = 24;
    var x = function (t) { return L + (W - L - R) * t; };
    var y = function (v) { return T + (H - T - B) * (1 - v); };
    [0, 0.25, 0.5, 0.75, 1].forEach(function (v) {
      svg.appendChild(svgEl("line", { x1: L, x2: W - R, y1: y(v), y2: y(v), stroke: "#e6dac7", "stroke-width": 1 }));
      svg.appendChild(svgEl("text", { x: L - 6, y: y(v) + 3, "text-anchor": "end" }, Math.round(v * 100) + "%"));
    });
    [0, 0.25, 0.5, 0.75, 1].forEach(function (t) {
      svg.appendChild(svgEl("text", { x: x(t), y: H - 6, "text-anchor": "middle" }, t.toFixed(2)));
    });
    function line(key, color) {
      var d = curve.map(function (c, i) {
        return (i ? "L" : "M") + x(c.threshold).toFixed(1) + " " + y(c[key]).toFixed(1);
      }).join(" ");
      svg.appendChild(svgEl("path", { d: d, fill: "none", stroke: color, "stroke-width": 2.2 }));
    }
    line("precision", "#c4512c");
    line("recall", "#3f4d8a");
    var c = curve[idx];
    svg.appendChild(svgEl("line", { x1: x(c.threshold), x2: x(c.threshold), y1: T, y2: H - B, stroke: "#241c16", "stroke-width": 1.5, "stroke-dasharray": "4 3" }));
    svg.appendChild(svgEl("circle", { cx: x(c.threshold), cy: y(c.precision), r: 4, fill: "#c4512c" }));
    svg.appendChild(svgEl("circle", { cx: x(c.threshold), cy: y(c.recall), r: 4, fill: "#3f4d8a" }));
  }

  function showSlider() {
    var idx = Number($("thr").value);
    var c = curve[idx];
    if (!c) return;
    $("thr-out").textContent = c.threshold.toFixed(3);
    kpis($("thr-kpis"), [
      ["Precision", pct(c.precision), "Share of flagged test payments that really were fraud"],
      ["Recall", pct(c.recall), "Share of all test fraud that gets flagged"],
      ["Alerts / 1,000", num(c.alerts_per_1000, 1), "How many analysts would have to look at"],
      ["Live threshold", liveThreshold === null ? "—" : liveThreshold.toFixed(3)],
    ]);
    drawCurve(idx);
  }

  function nearestIndex(t) {
    var best = 0;
    curve.forEach(function (c, i) {
      if (Math.abs(c.threshold - t) < Math.abs(curve[best].threshold - t)) best = i;
    });
    return best;
  }

  // ---- traffic --------------------------------------------------------
  function drawTraffic(perMin) {
    var svg = $("traffic");
    svg.innerHTML = "";
    var W = 640, H = 170, L = 30, R = 6, T = 8, B = 22;
    var max = Math.max(4, Math.max.apply(null, perMin.map(function (m) { return m.total; })));
    var step = (W - L - R) / perMin.length;
    var y = function (v) { return T + (H - T - B) * (1 - v / max); };
    [0, 0.5, 1].forEach(function (f) {
      var v = Math.round(max * f);
      svg.appendChild(svgEl("line", { x1: L, x2: W - R, y1: y(v), y2: y(v), stroke: "#e6dac7", "stroke-width": 1 }));
      svg.appendChild(svgEl("text", { x: L - 5, y: y(v) + 3, "text-anchor": "end" }, String(v)));
    });
    perMin.forEach(function (m, i) {
      var bx = L + i * step + 1, bw = Math.max(2, step - 3);
      var title = m.t + ": " + m.total + " payments, " + m.medium + " medium, " + m.high + " high";
      var g = svgEl("g", {});
      g.appendChild(svgEl("title", {}, title));
      g.appendChild(svgEl("rect", { x: bx, y: y(m.total), width: bw, height: Math.max(0, y(0) - y(m.total)), fill: "#d9cdb8", rx: 2 }));
      if (m.medium) g.appendChild(svgEl("rect", { x: bx, y: y(m.medium + m.high), width: bw, height: y(0) - y(m.medium), fill: "#e3b15a", rx: 2 }));
      if (m.high) g.appendChild(svgEl("rect", { x: bx, y: y(m.high), width: bw, height: y(0) - y(m.high), fill: "#c4512c", rx: 2 }));
      svg.appendChild(g);
      if (i % 5 === 4) svg.appendChild(svgEl("text", { x: bx + bw / 2, y: H - 6, "text-anchor": "middle" }, m.t));
    });
  }

  // ---- tables ---------------------------------------------------------
  function table(el, head, rows) {
    el.innerHTML = "";
    var thead = document.createElement("thead");
    var tr = document.createElement("tr");
    head.forEach(function (h) { var th = document.createElement("th"); th.textContent = h; tr.appendChild(th); });
    thead.appendChild(tr);
    el.appendChild(thead);
    var tbody = document.createElement("tbody");
    rows.forEach(function (r) {
      var row = document.createElement("tr");
      r.forEach(function (cell) {
        var td = document.createElement("td");
        if (cell instanceof Node) td.appendChild(cell);
        else td.textContent = cell;
        row.appendChild(td);
      });
      tbody.appendChild(row);
    });
    el.appendChild(tbody);
  }

  function tag(text, cls) {
    var s = document.createElement("span");
    s.className = "tag " + cls;
    s.textContent = text;
    return s;
  }

  // ---- loaders --------------------------------------------------------
  function loadModel() {
    return apiGet("/api/v1/model").then(function (m) {
      liveThreshold = m.threshold;
      $("m-version").textContent = m.available ? (m.model.replace(/_/g, " ") + (m.version ? " v" + m.version : "")) : "rules only";
      $("m-threshold").textContent = m.threshold === null ? "—" :
        m.threshold.toFixed(4) + (m.threshold_overridden ? " (manual)" : "");
      var mt = m.metrics;
      if (!mt) { $("model-note").textContent = "No metrics file. Run python train_model.py."; return; }
      var t = mt.test.model;
      kpis($("model-kpis"), [
        ["PR-AUC", num(t.pr_auc)],
        ["Precision", pct(t.precision)],
        ["Recall", pct(t.recall)],
        ["F1", num(t.f1)],
      ]);
      $("model-note").textContent = "Trained " + mt.trained_at + " on " + mt.rows.toLocaleString() +
        " payments (" + mt.fraud_rows.toLocaleString() + " fraud). Test days " + mt.split.test_days.join("–") +
        ", never used for tuning. Analyst decisions in this version: " + ((mt.feedback && mt.feedback.used) || 0) + ".";
      var an = mt.test.anomaly;
      if (an) {
        kpis($("an-kpis"), [
          ["PR-AUC", num(an.pr_auc)],
          ["Precision", pct(an.precision)],
          ["Recall", pct(an.recall)],
          ["Alerts / 1,000", num(an.alerts_per_1000, 1)],
          ["Fraud only it caught", String(an.fraud_missed_by_model_but_caught || 0), "Test fraud the supervised model missed but the anomaly detector flagged"],
        ]);
      } else {
        kpis($("an-kpis"), [["Status", "not trained"]]);
      }
      if (mt.curve && mt.curve.length) {
        curve = mt.curve;
        $("thr").max = String(curve.length - 1);
        if (!sliderTouched) $("thr").value = String(nearestIndex(liveThreshold || mt.threshold));
        showSlider();
      }
    });
  }

  function loadMonitor() {
    return apiGet("/api/v1/monitor").then(function (d) {
      $("m-updated").textContent = d.generated_at + " UTC";
      drawTraffic(d.per_minute);
      var lastMin = d.per_minute.slice(-5).reduce(function (a, m) { return a + m.total; }, 0);
      kpis($("traffic-kpis"), [
        ["Payments stored", d.total_transactions.toLocaleString()],
        ["Last 5 min", String(lastMin)],
        ["High (last " + d.window_rows + ")", String(d.levels.High)],
        ["Medium", String(d.levels.Medium)],
        ["p95 latency", d.latency_ms.p95 === null ? "—" : d.latency_ms.p95 + " ms"],
      ]);
      var s = d.sources;
      var srcRows = [
        ["Model only", s["model only"] || 0, undefined, "ml"],
        ["Rules only", s["rules only"] || 0],
        ["Both", s["both"] || 0, undefined, "ok"],
        ["Anomaly detector flagged", s["anomaly flagged"] || 0, undefined, "ml"],
      ];
      hbars($("sources"), srcRows, Math.max.apply(null, srcRows.map(function (r) { return r[1]; }).concat([1])));
      var cats = d.high_by_category.map(function (c) { return [c.category, c.count]; });
      hbars($("cats"), cats, cats.length ? cats[0][1] : 1);

      var fb = d.feedback;
      var conf = fb.confirmed;
      hbars($("confirmed"), [
        ["Model alerts", conf.model.precision || 0, conf.model.reviewed ? pct(conf.model.precision) + " of " + conf.model.reviewed : "—", "ml"],
        ["Rule alerts", conf.rules.precision || 0, conf.rules.reviewed ? pct(conf.rules.precision) + " of " + conf.rules.reviewed : "—"],
        ["Anomaly alerts", conf.anomaly.precision || 0, conf.anomaly.reviewed ? pct(conf.anomaly.precision) + " of " + conf.anomaly.reviewed : "—", "ml"],
      ], 1);
      Array.prototype.forEach.call($("confirmed").querySelectorAll(".hbar"), function (r) {
        r.style.gridTemplateColumns = "minmax(0, 9rem) minmax(0, 1fr) 6.5rem";
      });

      if (!d.drift.length) {
        table($("drift"), ["Input", "PSI", "Status"], [["No live payments with stored model inputs yet. Press Play BankSim on the console.", "", ""]]);
      } else {
        table($("drift"), ["Input", "PSI", "Status", "Live rows"], d.drift.map(function (r) {
          return [r.feature, r.psi.toFixed(3), tag(r.status, r.status), String(r.rows)];
        }));
      }
    });
  }

  function loadHistory() {
    return apiGet("/api/v1/model/history").then(function (rows) {
      if (!rows.length) {
        table($("history"), ["Version"], [["No versions recorded yet."]]);
        return;
      }
      table($("history"), ["Version", "Trained", "Source", "Feedback rows", "PR-AUC", "Precision", "Recall", "Status"],
        rows.slice().reverse().map(function (r) {
          return ["v" + r.version, r.trained_at, r.source, String(r.feedback_used), num(r.pr_auc), pct(r.precision), pct(r.recall),
            tag(r.promoted ? "promoted" : "rejected", r.promoted ? "stable" : "drifted")];
        }));
      var notes = rows.slice().reverse().map(function (r) { return "v" + r.version + ": " + r.note; });
      $("history").title = notes.join("\n");
    });
  }

  function renderRetrain(st) {
    var fb = st.feedback || {};
    kpis($("fb-kpis"), [
      ["Reviewed", String(fb.reviewed || 0)],
      ["Blocked", String(fb.blocked || 0)],
      ["Approved", String(fb.approved || 0)],
      ["Usable for training", String(fb.used || 0), "Decisions with stored model inputs, outside the validation and test days"],
    ]);
    $("retrain-stage").textContent = st.state === "idle" ? "idle" : st.state + (st.stage ? " · " + st.stage : "");
    $("btn-retrain").disabled = st.state === "running";
    var log = $("retrain-log");
    if (st.log && st.log.length) {
      log.classList.remove("hidden");
      log.textContent = st.log.join("\n") + (st.error ? "\nERROR: " + st.error : "");
      log.scrollTop = log.scrollHeight;
    }
    var box = $("retrain-result");
    box.innerHTML = "";
    if (st.result && st.result.new) {
      var o = st.result.old || {}, n = st.result.new;
      var t = document.createElement("table");
      t.className = "tbl-mon";
      box.appendChild(t);
      table(t, ["", "Before" + (o.version ? " (v" + o.version + ")" : ""), "After (v" + n.version + ")"], [
        ["PR-AUC", num(o.pr_auc), num(n.pr_auc)],
        ["Precision", pct(o.precision), pct(n.precision)],
        ["Recall", pct(o.recall), pct(n.recall)],
        ["F1", num(o.f1), num(n.f1)],
        ["Decision", "", st.result.entry.promoted ? "promoted to serving" : "rejected, kept the old model"],
      ]);
    }
    if (st.state === "running" && !polling) {
      polling = setInterval(loadRetrain, 2000);
    }
    if (st.state !== "running" && polling) {
      clearInterval(polling);
      polling = null;
      loadModel();
      loadHistory();
    }
  }

  function loadRetrain() {
    return apiGet("/api/v1/model/retrain").then(renderRetrain);
  }

  $("thr").addEventListener("input", function () { sliderTouched = true; showSlider(); });
  $("thr-apply").addEventListener("click", function () {
    var c = curve[Number($("thr").value)];
    if (!c) return;
    apiPost("/api/v1/model/threshold", { threshold: c.threshold })
      .then(function () { sliderTouched = false; return loadModel(); })
      .catch(function (err) { showBanner(String(err)); });
  });
  $("thr-reset").addEventListener("click", function () {
    apiPost("/api/v1/model/threshold", { threshold: null })
      .then(function () { sliderTouched = false; return loadModel(); })
      .catch(function (err) { showBanner(String(err)); });
  });
  $("btn-retrain").addEventListener("click", function () {
    $("btn-retrain").disabled = true;
    apiPost("/api/v1/model/retrain", {})
      .then(function () { return loadRetrain(); })
      .catch(function (err) { showBanner(String(err)); $("btn-retrain").disabled = false; });
  });

  function refresh() {
    return Promise.all([loadMonitor(), loadRetrain()]).then(function () { showBanner(""); })
      .catch(function () { showBanner("Backend not reachable. Start the server on port 8000."); });
  }

  loadModel().catch(function () {});
  loadHistory().catch(function () {});
  refresh();
  setInterval(refresh, 5000);
})();
