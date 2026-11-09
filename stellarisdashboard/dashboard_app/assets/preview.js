// Entity previews: resting the pointer (or keyboard focus) on a link to an empire's, leader's, war's,
// system's or planet's ledger page shows a card with its key facts (entity_preview.py).
// Delegated on document so it survives hx-boost swaps; the card is re-created when a swap drops it.
(function () {
    "use strict";
    var KINDS = ["country", "leader", "war", "system", "planet"];
    var LINKS = "a.textlink, a.titlelink, a.search-hit";
    var SHOW_DELAY_MS = 350;
    var HIDE_DELAY_MS = 250;
    var CACHE_MS = 60000;
    var GAP = 8;

    // touch screens have no hover: a tap follows the link as before
    if (!window.matchMedia || !window.fetch || !window.matchMedia("(any-hover: hover)").matches) return;

    var cache = {};  // preview URL -> {at, promise}
    var card = null;
    var anchor = null;  // the link the card is (or is about to be) shown for
    var point = null;  // where the pointer entered it, to pick the line of a wrapped link
    var showTimer = null;
    var hideTimer = null;
    var seq = 0;

    function previewUrl(link) {
        if (!link || !link.matches(LINKS) || link.closest(".preview-card")) return null;
        var url;
        try { url = new URL(link.getAttribute("href"), window.location.href); } catch (e) { return null; }
        if (url.origin !== window.location.origin || !/^\/history\/[^\/]+\/?$/.test(url.pathname)) return null;
        for (var i = 0; i < KINDS.length; i++) {
            var id = url.searchParams.get(KINDS[i]);
            if (id && /^-?\d+$/.test(id)) return url.pathname.replace(/\/$/, "") + "/preview/" + KINDS[i] + "/" + id;
        }
        return null;
    }

    function load(url) {
        var hit = cache[url];
        if (hit && Date.now() - hit.at < CACHE_MS) return hit.promise;
        var promise = fetch(url, {headers: {"Accept": "text/html"}}).then(function (r) {
            if (!r.ok) throw new Error(r.status);
            return r.text();
        });
        cache[url] = {at: Date.now(), promise: promise};
        promise.catch(function () { delete cache[url]; });
        return promise;
    }

    function getCard() {
        if (!card || !card.isConnected) {
            card = document.createElement("div");
            card.className = "preview-card";
            card.id = "entity-preview";
            card.hidden = true;
            card.addEventListener("pointerenter", function () { clearTimeout(hideTimer); });
            card.addEventListener("pointerleave", function (e) {
                if (!anchor || !anchor.contains(e.relatedTarget)) scheduleHide();
            });
            document.body.appendChild(card);
        }
        return card;
    }

    function scheduleShow(link, x, y) {
        clearTimeout(hideTimer);
        clearTimeout(showTimer);
        if (link === anchor && card && !card.hidden) return;
        anchor = link;
        point = x === undefined ? null : {x: x, y: y};
        var url = previewUrl(link);
        var mySeq = ++seq;
        showTimer = setTimeout(function () {
            load(url).then(function (html) {
                if (mySeq !== seq || !link.isConnected) return;
                var c = getCard();
                c.innerHTML = html;
                c.hidden = false;
                place(c, link);
                link.setAttribute("aria-describedby", c.id);
            }).catch(function () { /* hidden, deleted or offline: no preview */ });
        }, SHOW_DELAY_MS);
    }

    function scheduleHide() {
        clearTimeout(showTimer);
        clearTimeout(hideTimer);
        hideTimer = setTimeout(hide, HIDE_DELAY_MS);
    }

    function hide() {
        clearTimeout(showTimer);
        clearTimeout(hideTimer);
        seq++;
        if (anchor) anchor.removeAttribute("aria-describedby");
        anchor = null;
        if (card) card.hidden = true;
    }

    // The line box of the link under the pointer: a link can wrap over two lines.
    function linkBox(link) {
        var rects = link.getClientRects();
        if (point) {
            for (var i = 0; i < rects.length; i++) {
                var r = rects[i];
                if (point.y >= r.top - 1 && point.y <= r.bottom + 1) return r;
            }
        }
        return rects[0] || link.getBoundingClientRect();
    }

    function place(c, link) {
        var vw = document.documentElement.clientWidth;
        var vh = window.innerHeight;
        c.style.maxHeight = "";
        var w = c.offsetWidth;
        var h = c.offsetHeight;
        var left, top;
        var results = link.closest(".topsearch-results");
        var side = results && results.getBoundingClientRect();
        if (side && side.left - GAP - w >= GAP) {
            // beside the search results, so the list stays readable
            left = side.left - GAP - w;
            top = Math.min(link.getBoundingClientRect().top, vh - h - GAP);
        } else {
            // below or above the link, never over it: in the roomier side, scrolling if it doesn't fit
            var box = linkBox(link);
            left = Math.min(Math.max(GAP, box.left), vw - w - GAP);
            var below = vh - box.bottom - 2 * GAP;
            var above = box.top - 2 * GAP;
            if (h > below && h > above) {
                h = Math.max(below, above);
                c.style.maxHeight = h + "px";
            }
            top = h <= below || below >= above ? box.bottom + GAP : box.top - GAP - h;
        }
        c.style.left = Math.max(GAP, left) + "px";
        c.style.top = Math.max(GAP, top) + "px";
    }

    document.addEventListener("pointerover", function (e) {
        if (e.pointerType && e.pointerType !== "mouse" && e.pointerType !== "pen") return;
        var link = e.target.closest && e.target.closest("a");
        if (!link || link === anchor || !previewUrl(link)) return;
        scheduleShow(link, e.clientX, e.clientY);
    });

    document.addEventListener("pointerout", function (e) {
        if (!anchor || !anchor.contains(e.target) || anchor.contains(e.relatedTarget)) return;
        if (card && card.contains(e.relatedTarget)) return;
        scheduleHide();
    });

    document.addEventListener("focusin", function (e) {
        var link = e.target;
        if (link.matches && link.matches(":focus-visible") && previewUrl(link)) scheduleShow(link);
    });

    document.addEventListener("focusout", function (e) {
        if (e.target === anchor && !(card && card.contains(e.relatedTarget))) scheduleHide();
    });

    document.addEventListener("keydown", function (e) {
        if (e.key === "Escape" && card && !card.hidden) hide();
    });

    // following a link, scrolling or a page swap leaves the card stale
    document.addEventListener("click", hide, true);
    window.addEventListener("scroll", function (e) {
        if (!card || card.hidden || card.contains(e.target)) return;
        hide();
    }, {capture: true, passive: true});
    window.addEventListener("resize", hide);
    document.addEventListener("htmx:beforeSwap", hide);
})();
