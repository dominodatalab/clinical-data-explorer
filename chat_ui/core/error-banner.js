export function showErrorBanner(message) {
    const banner = document.getElementById('error-banner');
    const text = document.getElementById('error-banner-text');
    if (!banner || !text) return;

    text.textContent = message || 'Sorry, something went wrong.';
    banner.classList.add('visible');
    banner.onclick = hideErrorBanner;
}

export function hideErrorBanner() {
    const banner = document.getElementById('error-banner');
    if (banner) {
        banner.classList.remove('visible');
    }
}
