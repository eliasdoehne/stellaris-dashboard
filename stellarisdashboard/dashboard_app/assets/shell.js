// App shell behaviour: rail collapse, mobile drawer, in-place graph tab switching
// on the Dash page, and keyboard shortcuts. Delegated on document so it keeps
// working after hx-boost swaps the body.
(function () {
    "use strict";
    var root = document.documentElement;
    var COLLAPSE_KEY = "sd.railCollapsed";

    try {
        if (localStorage.getItem(COLLAPSE_KEY) === "1") root.classList.add("rail-collapsed");
    } catch (e) { /* storage unavailable: default to expanded */ }

    function toggleCollapse() {
        var collapsed = root.classList.toggle("rail-collapsed");
        try { localStorage.setItem(COLLAPSE_KEY, collapsed ? "1" : "0"); } catch (e) { /* ignore */ }
        // charts and the galaxy canvas size themselves on window resize
        window.dispatchEvent(new Event("resize"));
    }

    function setDrawer(open) {
        root.classList.toggle("drawer-open", open);
    }

    function onDashPage() {
        return window.location.pathname.indexOf("/timeline/") === 0;
    }

    function currentGraphTab() {
        var tab = new URLSearchParams(window.location.search).get("tab");
        var first = document.querySelector("[data-graph-tab]");
        return tab || (first && first.getAttribute("data-graph-tab"));
    }

    function syncGraphTab() {
        var tab = currentGraphTab();
        document.querySelectorAll("[data-graph-tab]").forEach(function (a) {
            var active = a.getAttribute("data-graph-tab") === tab;
            a.classList.toggle("is-active", active);
            if (active && a.classList.contains("subnav-link")) a.scrollIntoView({block: "nearest", inline: "nearest"});
        });
        var crumb = document.querySelector("[data-graph-crumb]");
        if (crumb && tab) crumb.textContent = tab;
    }

    // dcc.Location picks up pushState navigation through this private event.
    function dashNavigate(href) {
        window.history.pushState({}, "", href);
        window.dispatchEvent(new CustomEvent("_dashprivate_pushstate"));
        syncGraphTab();
        window.scrollTo(0, 0);
    }

    document.addEventListener("click", function (e) {
        var toggle = e.target.closest("[data-shell-toggle]");
        if (toggle) {
            if (toggle.getAttribute("data-shell-toggle") === "collapse") toggleCollapse();
            else setDrawer(!root.classList.contains("drawer-open"));
            return;
        }
        var link = e.target.closest("a[data-graph-tab]");
        if (link && onDashPage() && !e.ctrlKey && !e.metaKey && !e.shiftKey && e.button === 0) {
            var target = new URL(link.href, window.location.href);
            var here = new URLSearchParams(window.location.search);
            if (target.searchParams.get("game_name") === here.get("game_name")) {
                e.preventDefault();
                setDrawer(false);
                dashNavigate(target.pathname + target.search);
                return;
            }
        }
        if (e.target.closest(".rail a")) setDrawer(false);
    });

    window.addEventListener("popstate", function () {
        if (onDashPage()) syncGraphTab();
    });
    document.addEventListener("DOMContentLoaded", function () {
        if (onDashPage()) syncGraphTab();
    });

    function isTyping(el) {
        return el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName));
    }

    document.addEventListener("keydown", function (e) {
        if (e.ctrlKey || e.metaKey || e.altKey || isTyping(e.target)) return;
        if (e.key === "Escape") { setDrawer(false); return; }
        if (e.key === "\\") { toggleCollapse(); return; }
        if (e.key === "[" || e.key === "]") {
            var tabs = Array.prototype.slice.call(document.querySelectorAll(".rail a[data-graph-tab]"));
            if (!onDashPage() || !tabs.length) return;
            var i = tabs.findIndex(function (a) { return a.classList.contains("is-active"); });
            var next = tabs[(i + (e.key === "]" ? 1 : tabs.length - 1)) % tabs.length];
            dashNavigate(new URL(next.href).pathname + new URL(next.href).search);
            return;
        }
        var section = document.querySelector('.rail a[data-shortcut="' + e.key.toLowerCase() + '"]');
        if (section && !section.classList.contains("is-active")) window.location.href = section.href;
    });
})();
