// Shared modal/overlay open-close primitives.
//
// Every modal in the Data Explorer follows the same pattern: a fixed-position
// overlay element (`*-modal-overlay` id) and a `.visible` class on the overlay
// that drives both display and the fade transition (see `style.css`'s
// `.modal-overlay` and `.modal-overlay.visible` rules). Opening = add the
// class, closing = remove it; clicking the overlay backdrop (but not the
// inner modal content) dismisses.
//
// The helpers below are deliberately one-liners — they exist so feature
// modules don't reinvent the same three patterns and so a future "Escape to
// close" / focus-trap / scroll-lock change can land in one place. They make
// the open/close pattern *named*, not faster — there's no perf or behavior
// difference vs. the prior inline classList toggles.
//
// Per ground rule #2 (zero behavior change), this is a pure consolidation of
// existing code. The pattern was extracted for plan box 4.6 / P14b after
// surveying the four modal-owning modules: `modules/filters.js` (filter modal
// + expression modal), `modules/file-browser.js` (file browser modal),
// `modules/governance.js` (finding modal — the governance popover uses the
// same `.visible` class but is *not* a modal-overlay so it doesn't go through
// these helpers).
//
// Exports:
//   - `openModal(el)` — el.classList.add('visible'). The element MUST exist;
//     callers always look it up at module load and reuse the cached ref.
//   - `closeModal(el)` — el.classList.remove('visible').
//   - `attachOverlayDismiss(overlayEl, closeFn)` — wires the
//     "click-on-backdrop-but-not-inner-modal" dismissal pattern. The check
//     uses `e.target === overlayEl` (not `e.currentTarget` or `.contains()`)
//     because that's the exact pre-extraction shape from filters.js /
//     governance.js / file-browser.js (verbatim per ground rule #2). Never
//     return the listener removal handle — modal listeners live for the page
//     lifetime, same as before.

export function openModal(el) {
    el.classList.add('visible');
}

export function closeModal(el) {
    el.classList.remove('visible');
}

export function attachOverlayDismiss(overlayEl, closeFn) {
    overlayEl.addEventListener('click', (e) => {
        if (e.target === overlayEl) closeFn();
    });
}
