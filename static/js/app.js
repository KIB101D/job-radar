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

  function fitSticky(side) {
    const top = Math.min(GAP, window.innerHeight - side.offsetHeight - GAP);
    side.style.top = `${top}px`;
  }

  function watchSticky() {
    const side = document.querySelector(".workspace-side");
    if (!side) return;
    const fit = () => fitSticky(side);
    if ("ResizeObserver" in window) new ResizeObserver(fit).observe(side);
    window.addEventListener("resize", fit);
    fit();
  }

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

    if (event.target.closest("[data-toast-close]")) {
      event.target.closest("[data-toast]")?.remove();
    }
  });

  document.addEventListener("change", (event) => {
    const group = event.target.closest?.(".fgroup");
    if (group) summarize(group);
  });

  document.addEventListener("DOMContentLoaded", () => {
    enhance(document);
    watchSticky();
    // Content swapped in by HTMX gets the same treatment.
    document.body.addEventListener("htmx:load", (event) => enhance(event.target));
  });
})();
