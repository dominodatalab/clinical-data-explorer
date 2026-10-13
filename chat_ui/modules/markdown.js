// Markdown rendering for agent chat replies.
//
// Two exports:
//   - `escapeHtml(text)` — literal-text escaping for user/system/error
//     bubbles, so a user typing `**hi**` or `<b>` sees exactly that.
//   - `renderMarkdown(text)` — parses agent replies as GitHub-flavored
//     Markdown (via the vendored `marked`) and sanitizes the result (via
//     the vendored `DOMPurify`) before it is ever assigned to `innerHTML`.
//     Falls back to escaped-and-`<br>`'d text if either vendored library
//     failed to load, so a missing script degrades gracefully instead of
//     blanking the chat.
//
// `marked` and `DOMPurify` are loaded as classic (non-module) scripts in
// `index.html`, ahead of the module graph — same assumption the chart
// renderers already make about `Highcharts` being on `window`.

const ALLOWED_TAGS = [
    'p', 'br', 'strong', 'em', 'del', 'code', 'pre',
    'ul', 'ol', 'li',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'blockquote', 'a',
    'table', 'thead', 'tbody', 'tr', 'th', 'td',
    'hr', 'span',
];

const ALLOWED_ATTR = ['href', 'title', 'colspan', 'rowspan', 'start'];

let linkHookRegistered = false;
let warnedMissingLibraries = false;

function registerLinkHardeningHook() {
    if (linkHookRegistered || !window.DOMPurify) return;
    linkHookRegistered = true;

    window.DOMPurify.addHook('afterSanitizeAttributes', (node) => {
        if (node.tagName !== 'A' || !node.hasAttribute('href')) return;

        const href = node.getAttribute('href') || '';
        if (!/^(https?:|mailto:)/i.test(href)) {
            node.removeAttribute('href');
            return;
        }

        node.setAttribute('target', '_blank');
        node.setAttribute('rel', 'noopener noreferrer');
    });
}

export function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = String(text ?? '');
    return div.innerHTML;
}

function normalize(text) {
    return String(text ?? '')
        .replace(/\r\n/g, '\n')
        .replace(/\n{3,}/g, '\n\n')
        .trim();
}

// Raw HTML in a reply is shown as literal text rather than parsed. LLMs often
// write placeholders like `<column_name>` outside backticks, which DOMPurify
// would otherwise strip silently. DOMPurify remains the safety net regardless.
function buildRenderer() {
    const renderer = new window.marked.Renderer();
    renderer.html = (token) => escapeHtml(typeof token === 'string' ? token : token.text);
    return renderer;
}

export function renderMarkdown(text) {
    if (!window.marked || !window.DOMPurify) {
        if (!warnedMissingLibraries) {
            warnedMissingLibraries = true;
            console.warn('Markdown libraries unavailable; falling back to plain text rendering.');
        }
        return escapeHtml(text).replace(/\n/g, '<br>');
    }

    registerLinkHardeningHook();

    const src = normalize(text);
    const html = window.marked.parse(src, { gfm: true, breaks: true, renderer: buildRenderer() });

    return window.DOMPurify.sanitize(html, {
        ALLOWED_TAGS,
        ALLOWED_ATTR,
        ALLOW_DATA_ATTR: false,
    });
}
