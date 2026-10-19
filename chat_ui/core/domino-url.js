export function getDominoExternalOrigin(locationLike = window.location) {
    const fallbackOrigin = locationLike.origin;

    try {
        const externalUrl = new URL(locationLike.href);
        if (externalUrl.hostname.startsWith('app.')) {
            externalUrl.hostname = externalUrl.hostname.slice('app.'.length);
        }
        return externalUrl.origin;
    } catch {
        return fallbackOrigin;
    }
}

export function resolveDominoExternalUrl(path, locationLike = window.location) {
    return new URL(path, getDominoExternalOrigin(locationLike)).toString();
}
