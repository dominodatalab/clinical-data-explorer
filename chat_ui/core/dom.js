// Shared DOM helpers for the Data Explorer frontend.
//
// Owns small utilities that touch the DOM but aren't owned by any single
// feature module:
//   - escapeHtml(str) — the single canonical HTML-escape (deduplicated in
//     plan §7.3 / commit 6d5591f). Uses the textContent → innerHTML
//     round-trip so the browser does the escaping. Identical to the
//     original script.js definition; do NOT "optimize" without verifying
//     every call site (~21 of them) still renders identically.
//   - showToast(message) — appends a transient `.toast` div to <body> for
//     ~3 seconds. Used by `modules/governance.js` (finding-created success)
//     AND by script.js's `copyPermalink()` (link-copied confirmation), so
//     it lives in core/dom.js to avoid one of those depending on the other.

export function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

export function showToast(message) {
    const toast = document.createElement('div');
    toast.className = 'toast';
    toast.textContent = message;
    document.body.appendChild(toast);

    setTimeout(() => {
        toast.remove();
    }, 3000);
}
