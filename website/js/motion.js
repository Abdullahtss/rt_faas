(function () {
  var stage = document.getElementById("stage");
  if (!stage) return;
  var world = stage.querySelector(".world");
  if (!world) return;

  stage.addEventListener("mousemove", function (e) {
    var box = stage.getBoundingClientRect();
    var x = (e.clientX - box.left) / box.width - 0.5;
    var y = (e.clientY - box.top) / box.height - 0.5;
    world.style.transform =
      "rotateX(" + (-y * 16).toFixed(2) + "deg) rotateY(" + (x * 22).toFixed(2) + "deg)";
  });

  stage.addEventListener("mouseleave", function () {
    world.style.transform = "";
  });
})();
