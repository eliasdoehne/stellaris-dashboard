// Event ledger: when htmx swaps a year in place, "Show fewer" keeps the year's footer under the
// pointer (so collapsing doesn't strand the reader further down), and keyboard focus stays in
// the year when the clicked link is gone. Delegated on document so it survives hx-boost swaps.
(function () {
    "use strict";
    var pending = null;

    // htmx:beforeRequest fires on the clicked link, htmx:beforeSwap on the section it replaces
    document.addEventListener("htmx:beforeRequest", function (e) {
        var link = e.detail.elt;
        if (link.closest(".ledger-year-foot")) pending = {link: link};
    });

    document.addEventListener("htmx:beforeSwap", function (e) {
        var target = e.detail.target;
        if (!pending || !pending.link.isConnected || !target.contains(pending.link)) return;
        var link = pending.link;
        pending.id = target.id;
        pending.top = link.hasAttribute("data-keep-in-view") ? link.getBoundingClientRect().top : null;
        pending.hadFocus = document.activeElement === link;
    });

    document.addEventListener("htmx:afterSwap", function () {
        if (!pending || !pending.id) return;
        var p = pending;
        pending = null;
        var section = document.getElementById(p.id);
        if (!section) return;
        var foot = section.querySelector(".ledger-year-foot") || section.lastElementChild;
        if (p.top !== null && foot) window.scrollBy(0, foot.getBoundingClientRect().top - p.top);
        // htmx refocuses a link with the same id; otherwise land on the year's other link
        if (p.hadFocus && (!document.activeElement || document.activeElement === document.body)) {
            var next = section.querySelector(".ledger-year-foot a");
            if (next) next.focus({preventScroll: true});
        }
    });
})();
