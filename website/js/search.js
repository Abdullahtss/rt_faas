(function () {
  var form = document.getElementById("search-form");
  var results = document.getElementById("results");

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var params = new URLSearchParams();
    var account = document.getElementById("account").value.trim();
    var level = document.getElementById("level").value;
    var from = document.getElementById("from").value;
    var to = document.getElementById("to").value;
    if (account) params.set("account_id", account);
    if (level) params.set("level", level);
    if (from) params.set("date_from", new Date(from).toISOString());
    if (to) params.set("date_to", new Date(to).toISOString());
    apiGet("/api/v1/transactions?" + params.toString())
      .then(function (rows) {
        showBanner("");
        renderList(results, rows, null, function () {});
      })
      .catch(function (err) { showBanner(String(err)); });
  });

  document.getElementById("btn-csv").addEventListener("click", function () {
    downloadCsv(document.getElementById("level").value || undefined)
      .catch(function (err) { showBanner(String(err)); });
  });
})();
