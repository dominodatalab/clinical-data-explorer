// "Chat tool settings" link for admins.
//
// The settings page lives at `<app root>/admin/chat-tools` and isn't reachable through
// Domino's vanity URL, so people can't be expected to guess its address. This asks the
// server whether the current user may open it and, only if so, reveals a link built
// from the same base URL as every other API call (so it works inside the Domino iframe).
// Any failure leaves the link hidden: it is a convenience, never a gate.

import { apiUrl, fetchJson } from '../core/api.js';

export async function initAdminLink() {
    const link = document.getElementById('chat-tool-settings-link');
    if (!link) return;
    try {
        const { is_admin: isAdmin } = await fetchJson(apiUrl('admin/api/chat-tools/me'));
        if (isAdmin) {
            link.href = apiUrl('admin/chat-tools');
            link.hidden = false;
        }
    } catch (error) {
        console.debug('Chat tool settings link not shown:', error);
    }
}
