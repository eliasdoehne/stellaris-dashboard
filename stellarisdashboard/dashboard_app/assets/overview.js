// Overview KPI tiles: hovering a sparkline moves a cursor and dot to the nearest sample and shows its
// date and value under it. Delegated on document so it survives hx-boost swaps.
(function () {
    "use strict";

    function points(spark) {
        if (!spark._points) {
            try { spark._points = JSON.parse(spark.dataset.points || "[]"); } catch (e) { spark._points = []; }
        }
        return spark._points;
    }

    function reset(spark) {
        var tile = spark.closest(".kpi-tile");
        var pts = points(spark);
        var end = pts[pts.length - 1];
        spark.classList.remove("is-hovered");
        if (tile) {
            tile.classList.remove("is-reading");
            tile.querySelector(".kpi-readout").textContent = "";
        }
        var dot = spark.querySelector(".kpi-spark-dot");
        if (dot && end) { dot.style.left = end[0] + "%"; dot.style.top = end[1] + "%"; }
    }

    document.addEventListener("pointermove", function (e) {
        var spark = e.target.closest && e.target.closest(".kpi-spark");
        document.querySelectorAll(".kpi-spark.is-hovered").forEach(function (s) { if (s !== spark) reset(s); });
        if (!spark) return;
        var pts = points(spark);
        if (!pts.length) return;
        var box = spark.getBoundingClientRect();
        var x = 100 * (e.clientX - box.left) / box.width;
        var best = pts[0];
        pts.forEach(function (p) { if (Math.abs(p[0] - x) < Math.abs(best[0] - x)) best = p; });
        spark.classList.add("is-hovered");
        spark.querySelector(".kpi-spark-cursor").style.left = best[0] + "%";
        var dot = spark.querySelector(".kpi-spark-dot");
        dot.style.left = best[0] + "%";
        dot.style.top = best[1] + "%";
        var tile = spark.closest(".kpi-tile");
        tile.classList.add("is-reading");
        tile.querySelector(".kpi-readout").textContent = best[2];
    });

    document.addEventListener("pointerleave", function () {
        document.querySelectorAll(".kpi-spark.is-hovered").forEach(reset);
    });
})();
