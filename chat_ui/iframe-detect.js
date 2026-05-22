// Detect whether the app is being rendered inside an iframe and tag <html>
// accordingly. Loaded synchronously in <head> BEFORE the stylesheet so the
// hide rule is applied during the first paint (no header flash).
//
// Domino apps run framed when the project's "deep linking" feature is OFF —
// in that case Domino's own top nav bar is already visible to the user, and
// our local .header would just duplicate it. The `in-iframe` class drives
// `html.in-iframe .header { display: none; }` in styles/layout.css.
//
// `window.self !== window.top` is safe across origins (the identity check
// itself doesn't trip the Same-Origin policy). The try/catch is belt-and-
// suspenders for older browsers / unusual sandbox configs that throw on
// even touching `window.top`.
(function () {
    try {
        if (window.self !== window.top) {
            document.documentElement.classList.add('in-iframe');
        }
    } catch (e) {
        document.documentElement.classList.add('in-iframe');
    }
})();
