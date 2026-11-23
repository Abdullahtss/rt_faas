var TOKEN = "rtfaas-dev-token";
var SEQ_KEY = "rtfaas.last_seq";

function headers() {
  return {
    Authorization: "Bearer " + TOKEN,
    "Content-Type": "application/json",
  };
}

function apiGet(path) {
  return fetch(path, { headers: headers() }).then(function (res) {
    if (!res.ok) throw new Error(res.status + " " + res.statusText);
    return res.json();
  });
}

function apiPost(path, body) {
  return fetch(path, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify(body || {}),
  }).then(function (res) {
    if (!res.ok) throw new Error(res.status + " " + res.statusText);
    return res.json();
  });
}

function money(n, ccy) {
  return (ccy || "PKR") + " " + Number(n).toLocaleString(undefined, { maximumFractionDigits: 0 });
}

function when(ts) {
  var d = new Date(ts && ts.indexOf("Z") === -1 ? ts + "Z" : ts);
  return d.toLocaleTimeString();
}

function showBanner(text) {
  var el = document.getElementById("banner");
  if (!el) return;
  if (!text) {
    el.classList.add("hidden");
    el.textContent = "";
    return;
  }
  el.textContent = text;
  el.classList.remove("hidden");
}

function renderList(container, items, selectedId, onPick) {
  container.innerHTML = "";
  if (!items.length) {
    var empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "No rows yet.";
    container.appendChild(empty);
    return;
  }
  items.forEach(function (t) {
    var row = document.createElement("div");
    row.className = "row-item" + (selectedId === t.id ? " sel" : "");
    var score = document.createElement("span");
    score.className = "level " + t.level;
    // Show whichever side drove the verdict, so a red row never reads "0".
    var shown = t.score;
    if (typeof t.ml_score === "number" && t.ml_score > shown) shown = t.ml_score;
    score.textContent = shown;
    score.title = "Rules " + t.score + "/100" +
      (typeof t.ml_score === "number" ? " · model " + t.ml_score + "/100" : "");
    var mid = document.createElement("div");
    var strong = document.createElement("strong");
    strong.textContent = t.account_masked;
    if (t.ml_flag) {
      var chip = document.createElement("span");
      chip.className = "mlchip";
      chip.textContent = "ML";
      chip.title = "Model flagged this (" + t.ml_score + "/100)";
      strong.appendChild(chip);
    }
    var em = document.createElement("em");
    em.textContent = money(t.amount, t.currency) + " · " + (t.city || t.merchant);
    mid.appendChild(strong);
    mid.appendChild(em);
    var time = document.createElement("time");
    time.textContent = when(t.timestamp);
    row.appendChild(score);
    row.appendChild(mid);
    row.appendChild(time);
    if (t.scenario === "banksim-fraud") row.className += " file-fraud";
    row.addEventListener("click", function () { onPick(t); });
    container.appendChild(row);
  });
}

function upsert(list, item, cap) {
  cap = cap || 120;
  var next = [item].concat(list.filter(function (x) { return x.id !== item.id; }));
  return next.slice(0, cap);
}

function downloadCsv(level) {
  var url = "/api/v1/export.csv" + (level ? "?level=" + encodeURIComponent(level) : "");
  return fetch(url, { headers: headers() }).then(function (res) {
    return res.blob();
  }).then(function (blob) {
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "rtfaas-audit.csv";
    a.click();
    URL.revokeObjectURL(a.href);
  });
}
