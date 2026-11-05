// Topbar search: live results from /history/<game>/search as you type.
// "/" (or Ctrl/Cmd+K) focuses it, arrows pick a hit, Enter opens it, Esc closes.
// Delegated on document and keyed on the .topsearch element, so it survives
// hx-boost body swaps (each swap brings a fresh element and fresh state).
(function () {
    "use strict";
    var DELAY_MS = 90;

    function state(root) {
        if (!root._search) {
            root._search = {
                root: root,
                input: root.querySelector(".topsearch-input"),
                results: root.querySelector(".topsearch-results"),
                timer: null, controller: null, seq: 0,
                kind: "", active: -1, shownFor: null,
            };
        }
        return root._search;
    }

    function hits(s) { return Array.prototype.slice.call(s.results.querySelectorAll(".search-hit")); }

    function setActive(s, i) {
        var list = hits(s);
        s.active = list.length ? Math.max(0, Math.min(i, list.length - 1)) : -1;
        list.forEach(function (a, j) { a.setAttribute("aria-selected", j === s.active ? "true" : "false"); });
        if (s.active >= 0) {
            s.input.setAttribute("aria-activedescendant", list[s.active].id);
            list[s.active].scrollIntoView({block: "nearest"});
        } else {
            s.input.removeAttribute("aria-activedescendant");
        }
    }

    function show(s, open) {
        s.results.hidden = !open;
        s.input.setAttribute("aria-expanded", open ? "true" : "false");
    }

    // Drops the pending search and any response still on its way.
    function cancel(s) {
        clearTimeout(s.timer);
        s.seq++;
        if (s.controller) s.controller.abort();
        s.root.classList.remove("is-loading");
    }

    function close(s) {
        cancel(s);
        show(s, false);
    }

    function fetchResults(s) {
        clearTimeout(s.timer);
        var q = s.input.value.trim();
        if (!q) { s.results.innerHTML = ""; s.shownFor = null; close(s); return Promise.resolve(); }
        var key = q + "\n" + s.kind;
        if (key === s.shownFor) { cancel(s); show(s, true); return Promise.resolve(); }
        if (s.controller) s.controller.abort();
        s.controller = window.AbortController ? new AbortController() : null;
        var seq = ++s.seq;
        var url = s.root.getAttribute("data-search-url") + "?q=" + encodeURIComponent(q) + (s.kind ? "&kind=" + s.kind : "");
        s.root.classList.add("is-loading");
        return fetch(url, {signal: s.controller && s.controller.signal, headers: {"Accept": "text/html"}})
            .then(function (r) { return r.text(); })
            .then(function (html) {
                if (seq !== s.seq) return;
                s.results.innerHTML = html;
                s.shownFor = key;
                hits(s).forEach(function (a, i) { a.id = "topsearch-hit-" + i; });
                if (s.root.getAttribute("data-search-boost") !== "false" && window.htmx) {
                    s.results.setAttribute("hx-boost", "true");
                    window.htmx.process(s.results);
                }
                setActive(s, 0);
                show(s, document.activeElement === s.input);
            })
            .catch(function () { /* aborted or offline: keep the previous results */ })
            .then(function () { if (seq === s.seq) s.root.classList.remove("is-loading"); });
    }

    function schedule(s) {
        clearTimeout(s.timer);
        s.timer = setTimeout(function () { fetchResults(s); }, DELAY_MS);
    }

    function openBox(root) {
        var s = state(root);
        root.classList.add("is-open");
        s.input.focus();
        s.input.select();
    }

    function closeBox(s) {
        close(s);
        s.root.classList.remove("is-open");
        s.input.blur();
    }

    function go(s, a, newTab) {
        if (!a) return;
        if (newTab) { window.open(a.href, "_blank"); return; }
        closeBox(s);
        a.click();  // a boosted link swaps the page via htmx, a plain one navigates
    }

    document.addEventListener("input", function (e) {
        if (!e.target.classList.contains("topsearch-input")) return;
        var s = state(e.target.closest(".topsearch"));
        s.kind = "";
        schedule(s);
    });

    document.addEventListener("focusin", function (e) {
        if (!e.target.classList.contains("topsearch-input")) return;
        var s = state(e.target.closest(".topsearch"));
        if (s.input.value.trim()) fetchResults(s);
        else if (!s.warmed) fetch(s.root.getAttribute("data-search-url") + "?warm=1").catch(function () {});
        s.warmed = true;
    });

    document.addEventListener("keydown", function (e) {
        var input = e.target.classList && e.target.classList.contains("topsearch-input") ? e.target : null;
        if (input) {
            var s = state(input.closest(".topsearch"));
            if (e.key === "ArrowDown" || e.key === "ArrowUp") {
                e.preventDefault();
                if (s.results.hidden) { fetchResults(s); return; }
                var n = hits(s).length;
                if (n) setActive(s, (s.active + (e.key === "ArrowDown" ? 1 : n - 1)) % n);
            } else if (e.key === "Enter") {
                e.preventDefault();
                var newTab = e.ctrlKey || e.metaKey;
                // typed faster than the delay: search now, then open the best hit
                var pending = s.shownFor !== s.input.value.trim() + "\n" + s.kind;
                var search = pending ? fetchResults(s) : Promise.resolve();
                var seq = s.seq;
                search.then(function () {
                    if (seq === s.seq) go(s, hits(s)[Math.max(s.active, 0)], newTab);  // unless closed meanwhile
                });
            } else if (e.key === "Escape") {
                e.preventDefault();
                if (!s.results.hidden) close(s);
                else closeBox(s);
            } else if (e.key === "Tab") {
                close(s);
            }
            return;
        }
        var typing = e.target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName);
        var root = document.querySelector(".topsearch");
        if (!root || typing || e.altKey) return;
        var slash = e.key === "/" && !e.ctrlKey && !e.metaKey;
        var cmdK = (e.key === "k" || e.key === "K") && (e.ctrlKey || e.metaKey);
        if (slash || cmdK) {
            e.preventDefault();
            openBox(root);
        }
    });

    document.addEventListener("mousedown", function (e) {
        var root = document.querySelector(".topsearch");
        if (!root) return;
        var s = state(root);
        if (e.target.closest(".search-more")) {
            e.preventDefault();  // keep the focus in the input
            s.kind = e.target.closest(".search-more").getAttribute("data-search-kind") || "";
            fetchResults(s);
            return;
        }
        if (!root.contains(e.target)) {
            close(s);
            if (!s.input.value.trim()) root.classList.remove("is-open");
        }
    });

    document.addEventListener("mousemove", function (e) {
        var a = e.target.closest && e.target.closest(".search-hit");
        if (!a) return;
        var s = state(a.closest(".topsearch"));
        var i = hits(s).indexOf(a);
        if (i !== s.active) setActive(s, i);
    });

    document.addEventListener("click", function (e) {
        var opener = e.target.closest("[data-search-open]");
        if (opener) { openBox(opener.closest(".topsearch")); return; }
        var closer = e.target.closest("[data-search-close]");
        if (closer) { closeBox(state(closer.closest(".topsearch"))); return; }
        var hit = e.target.closest(".search-hit");
        if (hit && !e.ctrlKey && !e.metaKey && !e.shiftKey) {
            var s = state(hit.closest(".topsearch"));
            close(s);
            s.root.classList.remove("is-open");
        }
    });
})();
