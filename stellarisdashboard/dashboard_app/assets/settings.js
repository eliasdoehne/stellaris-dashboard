// Settings page (templates/_settings_content.html): unsaved-changes state, per-setting reset,
// "modified" markers, dependent settings and jumping to a setting from a link. The page works
// without this script as a plain form. Loaded on every page and delegated on document, because
// hx-boost swaps only the body; init() is safe to run again on the same content.
(function () {
    "use strict";

    function form() { return document.getElementById("settings-form"); }

    function rows(f) { return Array.prototype.slice.call(f.querySelectorAll(".setting[data-setting]")); }

    function control(row) { return document.getElementById(row.getAttribute("data-setting")); }

    function rowOf(key) { return document.getElementById("setting-" + key); }

    function valueOf(input) {
        if (input.type === "checkbox") return input.checked ? "true" : "false";
        return input.value;
    }

    function setValue(input, text) {
        if (input.type === "checkbox") input.checked = text === "true";
        else input.value = text;
    }

    // a blank field means "use the default", as on the server
    function effective(row, text) {
        var kind = row.getAttribute("data-kind");
        if (kind !== "bool" && kind !== "choice" && text.trim() === "") return row.getAttribute("data-default");
        return text;
    }

    function same(row, a, b) {
        var kind = row.getAttribute("data-kind");
        a = effective(row, a);
        b = effective(row, b);
        if (kind === "int" || kind === "float") {
            if (a.trim() !== "" && b.trim() !== "" && !isNaN(Number(a)) && !isNaN(Number(b))) return Number(a) === Number(b);
        }
        if (kind === "bool" || kind === "choice") return a === b;
        return a.trim() === b.trim();
    }

    function isSet(row) {
        var input = control(row);
        return input.type === "checkbox" ? input.checked : input.value.trim() !== "";
    }

    function refreshRow(row) {
        var input = control(row);
        if (!input) return;
        var isDefault = same(row, valueOf(input), row.getAttribute("data-default"));
        row.classList.toggle("is-modified", !isDefault);
        var badge = row.querySelector("[data-modified-badge]");
        if (badge) badge.hidden = isDefault;
        var reset = row.querySelector("[data-reset]");
        if (reset) reset.hidden = isDefault;
    }

    function setDescribed(input, id, on) {
        var ids = (input.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean);
        var has = ids.indexOf(id) !== -1;
        if (on && !has) ids.push(id);
        if (!on && has) ids.splice(ids.indexOf(id), 1);
        input.setAttribute("aria-describedby", ids.join(" "));
    }

    // A setting that only matters when another one is set is dimmed and locked while it is not.
    // It is never `disabled`: disabled fields are not submitted, and a missing checkbox means false.
    function refreshDependency(row) {
        var parent = rowOf(row.getAttribute("data-depends-on"));
        if (!parent) return;
        var inactive = !isSet(parent);
        var input = control(row);
        row.classList.toggle("is-inactive", inactive);
        var note = row.querySelector(".setting-note");
        if (note) {
            note.hidden = !inactive;
            setDescribed(input, note.id, inactive);
        }
        if (inactive) input.setAttribute("aria-disabled", "true");
        else input.removeAttribute("aria-disabled");
        if (input.type !== "checkbox") input.readOnly = inactive;
        var reset = row.querySelector("[data-reset]");
        if (reset && inactive) reset.hidden = true;
    }

    function isDirty(f) {
        return rows(f).some(function (row) {
            var input = control(row);
            return input && !same(row, valueOf(input), row.getAttribute("data-saved"));
        });
    }

    function refreshDirty(f) {
        var dirty = isDirty(f);
        f.classList.toggle("is-dirty", dirty);
        var hint = f.querySelector("[data-dirty-hint]");
        if (hint) hint.hidden = !dirty;
        f.querySelectorAll("[data-save], [data-discard]").forEach(function (button) {
            if (dirty) button.removeAttribute("aria-disabled");
            else button.setAttribute("aria-disabled", "true");
        });
    }

    function refreshAll(f) {
        rows(f).forEach(refreshRow);
        f.querySelectorAll(".setting[data-depends-on]").forEach(refreshDependency);
        refreshDirty(f);
    }

    function revealRow(row) {
        var details = row.closest("details");
        if (details && !details.open) details.open = true;
    }

    // Links like /settings/#setting-save_file_path (from the games page) highlight that setting.
    // After an hx-boost navigation :target does not apply, so it is marked with a class as well.
    function showTarget() {
        var id = decodeURIComponent(window.location.hash.slice(1));
        document.querySelectorAll(".setting.is-targeted").forEach(function (r) { r.classList.remove("is-targeted"); });
        if (!id) return;
        var el = document.getElementById(id);
        var row = el && el.closest(".setting");
        if (!row || !form() || !form().contains(row)) return;
        revealRow(row);
        row.classList.add("is-targeted");
        row.scrollIntoView({block: "center"});
    }

    function focusFirstError(f) {
        var bad = f.querySelector("[aria-invalid=true]");
        if (!bad) return false;
        revealRow(bad.closest(".setting"));
        bad.focus();
        return true;
    }

    var refocus = false;

    function init() {
        var f = form();
        if (!f || f.hasAttribute("data-ready")) return;
        f.setAttribute("data-ready", "");
        refreshAll(f);
        if (!focusFirstError(f)) {
            if (refocus) f.querySelector("[data-save]").focus();
            else showTarget();
        }
        refocus = false;
    }

    document.addEventListener("input", function (e) {
        var f = form();
        if (!f || !f.contains(e.target)) return;
        var row = e.target.closest(".setting");
        if (row) refreshRow(row);
        f.querySelectorAll(".setting[data-depends-on]").forEach(refreshDependency);
        refreshDirty(f);
    });
    document.addEventListener("change", function (e) {
        var f = form();
        if (f && f.contains(e.target)) refreshDirty(f);
    });

    document.addEventListener("click", function (e) {
        var f = form();
        if (!f) return;
        var locked = e.target.closest("input[type=checkbox][aria-disabled=true]");
        if (locked && f.contains(locked)) {
            e.preventDefault();
            return;
        }
        var save = e.target.closest("[data-save], [data-discard]");
        if (save && save.getAttribute("aria-disabled") === "true") {
            e.preventDefault();
            return;
        }
        var reset = e.target.closest("[data-reset]");
        if (reset && f.contains(reset)) {
            var row = reset.closest(".setting");
            var input = control(row);
            setValue(input, row.getAttribute("data-default"));
            input.dispatchEvent(new Event("input", {bubbles: true}));
            input.dispatchEvent(new Event("change", {bubbles: true}));
            input.focus();
            return;
        }
        var link = e.target.closest("#settings-errors a[href^='#']");
        if (link) {
            var target = document.getElementById(link.getAttribute("href").slice(1));
            if (target) {
                e.preventDefault();
                revealRow(target.closest(".setting"));
                target.focus();
            }
        }
    });

    // "Discard changes": back to the saved values (a native reset would restore a rejected value
    // that the page shows for fixing).
    document.addEventListener("reset", function (e) {
        var f = form();
        if (e.target !== f) return;
        e.preventDefault();
        rows(f).forEach(function (row) {
            var input = control(row);
            if (!input || same(row, valueOf(input), row.getAttribute("data-saved"))) return;
            setValue(input, row.getAttribute("data-saved"));
            // lets htmx check a folder again
            input.dispatchEvent(new Event("change", {bubbles: true}));
        });
        refreshAll(f);
    });

    document.addEventListener("htmx:beforeSwap", function (e) {
        // after saving in place, keep the keyboard focus on the page instead of losing it
        var target = e.detail.target;
        if (target && target.id === "settings-content") {
            refocus = target.contains(document.activeElement) || document.activeElement === document.body;
        }
    });

    window.addEventListener("hashchange", showTarget);
    document.addEventListener("htmx:load", init);
    document.addEventListener("DOMContentLoaded", init);
})();
