/*
 * Small progressive enhancements. Everything works without JS:
 * keyword fields stay plain comma-separated inputs and forms submit normally.
 */
(() => {
  "use strict";

  const browserTimezone = (() => {
    try {
      return Intl.DateTimeFormat().resolvedOptions().timeZone || "";
    } catch {
      return "";
    }
  })();

  const closeIcon =
    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
    'stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>';

  // --- Keyword chips -----------------------------------------------------------------
  // <div data-chips><input type="text" name="include_keywords" value="python, django"></div>
  // becomes removable chips + an entry field; the original input turns hidden and
  // keeps the comma-separated value the server expects.

  function splitKeywords(value) {
    return value
      .split(/[,;\n]+/)
      .map((word) => word.trim().replace(/\s+/g, " "))
      .filter(Boolean);
  }

  function initChips(root) {
    const source = root.querySelector("input");
    if (!source || root.dataset.chipsReady) return;
    root.dataset.chipsReady = "1";

    const list = document.createElement("div");
    list.className = "chips-list";
    const entry = document.createElement("input");
    entry.type = "text";
    entry.id = source.id;
    entry.placeholder = source.placeholder;
    entry.autocomplete = "off";
    entry.className = "chips-entry";
    entry.setAttribute("enterkeyhint", "done");
    source.removeAttribute("id");
    source.type = "hidden";
    root.append(list, entry);

    let words = splitKeywords(source.value);
    let pendingTimer;
    // On a radar every change is saved at once, so a half-typed word must not be:
    // it is saved on Enter, comma or when the field loses focus. On /filters/ the
    // change only previews matches, so a pause in typing is enough.
    const autosave = Boolean(root.closest("form[data-autosave]"));

    // The hidden value always includes text still being typed, so the preview and
    // a submit see it, but it only becomes a chip on Enter or comma. (Turning it
    // into a chip on blur shifted the layout under the user's next click.)
    const sync = () => {
      source.value = words.concat(splitKeywords(entry.value)).join(", ");
    };
    const commit = () => {
      sync();
      source.dispatchEvent(new Event("change", { bubbles: true }));
    };

    const render = () => {
      list.replaceChildren(
        ...words.map((word, index) => {
          const chip = document.createElement("span");
          chip.className = "kw-chip";
          chip.textContent = word;
          const remove = document.createElement("button");
          remove.type = "button";
          remove.setAttribute("aria-label", `Прибрати ${word}`);
          remove.innerHTML = closeIcon;
          remove.addEventListener("click", () => {
            words.splice(index, 1);
            render();
            commit();
            entry.focus();
          });
          chip.append(remove);
          return chip;
        }),
      );
    };

    const addFromEntry = () => {
      const fresh = splitKeywords(entry.value).filter(
        (word) => !words.some((known) => known.toLowerCase() === word.toLowerCase()),
      );
      entry.value = "";
      if (!fresh.length) return;
      words = words.concat(fresh);
      render();
      commit();
    };

    entry.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === ",") {
        event.preventDefault();
        addFromEntry();
      } else if (event.key === "Backspace" && !entry.value && words.length) {
        words.pop();
        render();
        commit();
      }
    });
    // The entry has no name, so its own change event must not trigger a preview.
    entry.addEventListener("change", (event) => event.stopPropagation());
    entry.addEventListener("input", () => {
      if (autosave) return sync();
      clearTimeout(pendingTimer);
      pendingTimer = setTimeout(commit, 500);
    });
    entry.addEventListener("blur", () => {
      // Saved as typed; it becomes a chip on the next render. Making the chip now
      // would shift the layout under the click that caused the blur.
      if (autosave && entry.value.trim()) commit();
    });
    entry.addEventListener("paste", () => setTimeout(() => /[,;\n]/.test(entry.value) && addFromEntry()));

    root.chipsReset = () => {
      words = [];
      entry.value = "";
      render();
      source.value = "";
    };
    root.closest("form")?.addEventListener("submit", sync);
    render();
  }

  // --- Reset filters --------------------------------------------------------------------

  function resetFilters(form) {
    form.querySelectorAll("[data-chips]").forEach((root) => root.chipsReset?.());
    form
      .querySelectorAll('input[type="checkbox"]')
      .forEach((box) => (box.checked = box.hasAttribute("data-default-checked")));
    form.querySelectorAll("select[data-default]").forEach((select) => (select.value = select.dataset.default));
    form.querySelectorAll(".fgroup").forEach(summarize);
    form.dispatchEvent(new Event("change", { bubbles: true }));
  }

  // --- Folded filter groups ---------------------------------------------------------------
  // <details class="fgroup"> shows the current choice next to its name ("Рівень  Junior").

  function summarize(group) {
    const value = group.querySelector(".fgroup-value");
    if (!value) return;
    const select = group.querySelector("select");
    let text = "";
    if (select) {
      if (select.value !== (select.dataset.default ?? "")) text = select.selectedOptions[0]?.textContent || "";
    } else {
      text = [...group.querySelectorAll("input[type=checkbox]:checked")]
        .map((box) => box.nextElementSibling?.textContent.trim())
        .filter(Boolean)
        .join(", ");
    }
    value.textContent = text || value.dataset.empty || "";
    value.classList.toggle("is-set", Boolean(text));
  }

  // --- Filters column that follows the page ------------------------------------------------
  // Sticky by its top while it fits the window; when taller (a group unfolded), it
  // scrolls with the page and then sticks by its bottom, so no inner scrollbar.

  const GAP = 20;

  const phone = window.matchMedia("(max-width: 959px)");

  function fitSticky(side) {
    // On a phone the column is the filters sheet, placed by CSS.
    if (phone.matches) return side.style.removeProperty("top");
    const top = Math.min(GAP, window.innerHeight - side.offsetHeight - GAP);
    side.style.top = `${top}px`;
  }

  // --- Filters sheet (phones) ---------------------------------------------------------------
  // Below 960px the filters column is a sheet over the matches (CSS). The floating button
  // opens it, "Показати N збігів", the backdrop, Esc or a swipe down close it. Changes in
  // it save as usual, so the list behind is already up to date when it closes.

  const sheet = () => document.querySelector("[data-sheet]");

  function ukPlural(n, one, few, many) {
    const tens = n % 100;
    const units = n % 10;
    if (units === 1 && tens !== 11) return one;
    if (units >= 2 && units <= 4 && (tens < 12 || tens > 14)) return few;
    return many;
  }

  function setSheet(open) {
    const panel = sheet();
    if (!panel) return;
    const opener = document.querySelector("[data-sheet-open]");
    open = open && phone.matches;
    panel.classList.toggle("is-open", open);
    panel.style.removeProperty("--drag");
    document.documentElement.classList.toggle("sheet-is-open", open);
    opener?.setAttribute("aria-expanded", String(open));
    if (open) {
      panel.scrollTop = 0;
      panel.focus({ preventScroll: true });
    } else if (panel.contains(document.activeElement)) {
      opener?.focus({ preventScroll: true });
    }
  }

  function sheetMode() {
    const panel = sheet();
    if (!panel) return;
    if (phone.matches) {
      panel.setAttribute("role", "dialog");
      panel.setAttribute("aria-modal", "true");
      panel.setAttribute("aria-label", "Фільтри");
      panel.tabIndex = -1;
    } else {
      ["role", "aria-modal", "aria-label", "tabindex"].forEach((name) => panel.removeAttribute(name));
      setSheet(false);
    }
  }

  // Badge on the button: keywords plus every folded group that has a choice.
  function refreshSheet() {
    const panel = sheet();
    if (!panel) return;
    let set = panel.querySelectorAll(".fgroup-value.is-set").length;
    panel.querySelectorAll("[data-chips] input[name]").forEach((input) => (set += splitKeywords(input.value).length));
    const badge = document.querySelector("[data-filter-count]");
    if (badge) {
      badge.textContent = set;
      badge.hidden = !set;
    }
    const total = Number.parseInt(document.getElementById("match-count")?.textContent || "", 10);
    const label = panel.querySelector("[data-sheet-count]");
    if (label && !Number.isNaN(total)) label.textContent = `Показати ${total} ${ukPlural(total, "збіг", "збіги", "збігів")}`;
  }

  // Swipe down from the top of the sheet (when it isn't scrolled) closes it.
  function watchSwipe() {
    const panel = sheet();
    if (!panel) return;
    let startY = null;
    let dy = 0;
    panel.addEventListener(
      "touchstart",
      (event) => {
        const fromTop = event.touches[0].clientY - panel.getBoundingClientRect().top;
        startY = panel.scrollTop <= 0 && fromTop < 72 ? event.touches[0].clientY : null;
        dy = 0;
      },
      { passive: true },
    );
    panel.addEventListener(
      "touchmove",
      (event) => {
        if (startY === null) return;
        dy = Math.max(0, event.touches[0].clientY - startY);
        panel.classList.add("is-dragging");
        panel.style.setProperty("--drag", `${dy}px`);
      },
      { passive: true },
    );
    panel.addEventListener("touchend", () => {
      if (startY === null) return;
      panel.classList.remove("is-dragging");
      startY = null;
      if (dy > 90) setSheet(false);
      else panel.style.removeProperty("--drag");
    });
  }

  function watchSticky() {
    const side = document.querySelector(".workspace-side");
    if (!side) return;
    const fit = () => fitSticky(side);
    if ("ResizeObserver" in window) new ResizeObserver(fit).observe(side);
    window.addEventListener("resize", fit);
    fit();
  }

  // --- Live refresh guards ----------------------------------------------------------------
  // The filters and bot cards re-read the database every few seconds. They skip a
  // round only while you are using them right now: this window has focus and the
  // focused element is a field with text being typed, or an open-able select.
  // (Focus merely left on a checkbox you clicked earlier doesn't count: otherwise a
  // card stopped updating after any click, until the tab was switched.)

  function isTyping(selector) {
    if (!document.hasFocus()) return false;
    const field = document.activeElement;
    if (!field || !field.closest(selector)) return false;
    if (field.matches("select")) return true;
    return field.matches('input[type="text"], input:not([type]), textarea') && field.value.trim() !== "";
  }

  window.JobRadar = { isTyping };

  // Folded filter groups stay unfolded when the card is redrawn with new data.
  let openGroups = [];
  document.addEventListener("htmx:beforeSwap", (event) => {
    if (event.detail.target?.id !== "filters-box") return;
    openGroups = [...document.querySelectorAll("#filters-box .fgroup[open] .fgroup-label")].map(
      (label) => label.textContent,
    );
  });
  document.addEventListener("htmx:afterSettle", (event) => {
    if (!openGroups.length || !document.getElementById("filters-box")?.contains(event.target)) return;
    document.querySelectorAll("#filters-box .fgroup").forEach((group) => {
      if (openGroups.includes(group.querySelector(".fgroup-label")?.textContent)) group.open = true;
    });
    openGroups = [];
  });

  // --- Copy to clipboard ------------------------------------------------------------------

  async function copy(button) {
    const label = button.querySelector("[data-copy-label]") || button;
    const original = label.textContent;
    try {
      await navigator.clipboard.writeText(button.dataset.copy);
      label.textContent = button.dataset.copiedText || "Скопійовано";
    } catch {
      label.textContent = "Не вдалося скопіювати";
    }
    setTimeout(() => (label.textContent = original), 2000);
  }

  // --- Wiring -----------------------------------------------------------------------------

  function enhance(scope) {
    scope.querySelectorAll("[data-chips]").forEach(initChips);
    scope.querySelectorAll(".fgroup").forEach(summarize);
    if (browserTimezone) {
      scope.querySelectorAll("input[data-timezone]").forEach((input) => (input.value = browserTimezone));
    }
  }

  document.addEventListener("click", (event) => {
    const reset = event.target.closest("[data-reset-filters]");
    if (reset) return resetFilters(reset.closest("form"));

    const copyButton = event.target.closest("[data-copy]");
    if (copyButton) return copy(copyButton);

    if (event.target.closest("[data-sheet-open]")) return setSheet(true);
    if (event.target.closest("[data-sheet-close]")) return setSheet(false);

    if (event.target.closest("[data-toast-close]")) {
      event.target.closest("[data-toast]")?.remove();
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && document.documentElement.classList.contains("sheet-is-open")) setSheet(false);
  });

  document.addEventListener("change", (event) => {
    const group = event.target.closest?.(".fgroup");
    if (group) summarize(group);
    refreshSheet();
  });

  document.addEventListener("DOMContentLoaded", () => {
    enhance(document);
    watchSticky();
    sheetMode();
    refreshSheet();
    watchSwipe();
    phone.addEventListener("change", sheetMode);
    // Content swapped in by HTMX gets the same treatment.
    document.body.addEventListener("htmx:load", (event) => enhance(event.target));
    // New counts and redrawn filters (a save, the bot, a poll) update the badge and label.
    document.body.addEventListener("htmx:afterSettle", refreshSheet);
  });
})();
