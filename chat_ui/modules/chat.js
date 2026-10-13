// Chat UI module for the Data Explorer frontend.
//
// Owns the entire chat tab + cross-app system messaging:
//   - Chat configuration probe (`/chat/status`) and empty-state vs.
//     active-chat container toggle (the "OpenAI / Ollama / Together"
//     example tabs that show when the agent isn't configured).
//   - Send / Enter-key / Clear chat wiring.
//   - User and agent message rendering, including the "Thinking…" dots
//     animation while the agent is responding. Agent replies are rendered
//     as sanitized Markdown (see `modules/markdown.js`); user/system/error
//     bubbles stay literal text.
//   - Chart embedding inside agent replies (renderChart dispatcher).
//     The 8 type-specific renderers (bar / scatter / line / pie /
//     histogram / boxplot / heatmap / grouped_bar) live in
//     `modules/chart-renderers.js` so future chart consumers can share
//     them — see that file's header for why explore-charts deliberately
//     keeps its own renderer set rather than reusing these.
//   - Result cards for tools whose output is hidden from the AI (the admin's
//     "Run, hide output from AI" mode): the user sees the tool's output in a
//     tagged card, the model only got a confirmation that it ran.
//   - The welcome message displayed on first load.
//
// Four exports:
//   - `initChat()` — call once from script.js's DOMContentLoaded
//     callback. Looks up the chat-related DOM refs, wires the send /
//     keypress / clear listeners, initializes the empty-state example
//     tabs, and posts the welcome message.
//   - `displayMessage(text, sender, charts)` — exported because every
//     module that needs to surface a system/error message in the chat
//     log calls it (dataset-load successes/failures, auth errors,
//     "please load a dataset first" prompts, etc.). Lazy-resolves
//     `chatBox` if called before `initChat` — same defensive pattern as
//     `modules/governance.js`'s `showToast` re-entry.
//   - `displayToolResult(result)` — renders one hidden-from-AI result card.
//     Used for live responses and when the chat history is reloaded.
//   - `checkChatStatus()` — exported because the tab-switching code in
//     script.js calls it when the user clicks the chat tab; the result
//     drives whether the empty-state panel or the active chat surface
//     is visible.
//
// DOM lookup is deferred until `initChat()` runs (rather than at module
// load) because some chat DOM ids are only present once the chat tab
// markup has been parsed; the `displayMessage` lazy-resolve handles the
// rare case where another module fires a system message before init.

import { state } from '../core/state.js';
import { apiUrl, fetchJson, getApiErrorMessage, getApiErrorPayload } from '../core/api.js';
import {
    renderBarChart,
    renderScatterChart,
    renderLineChart,
    renderPieChart,
    renderHistogram,
    renderBoxplot,
    renderHeatmap,
    renderGroupedBarChart,
} from './chart-renderers.js';
import { escapeHtml, renderMarkdown } from './markdown.js';
import { renderToolOutput } from './tool-output.js';

export const HIDDEN_FROM_AI_LABEL = 'Hidden from AI';
export const HIDDEN_FROM_AI_TOOLTIP =
    'The assistant ran this tool but cannot see its output. Only you can see this.';

// Static markup (no data interpolated), so assigning it to innerHTML is safe.
const EYE_SLASH_SVG = `
<svg class="tool-result-badge-icon" viewBox="0 0 24 24" width="14" height="14" fill="none"
     stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94"/>
    <path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19"/>
    <path d="M14.12 14.12a3 3 0 1 1-4.24-4.24"/>
    <line x1="1" y1="1" x2="23" y2="23"/>
</svg>`;

let chatBox = null;
let userInput = null;
let sendButton = null;
let clearChatButton = null;
let chatContainer = null;
let chatEmptyState = null;

export async function checkChatStatus() {
    if (state.chatStatus.checked) {
        // Already checked, just update UI
        updateChatUI();
        return state.chatStatus.configured;
    }

    try {
        const data = await fetchJson(apiUrl('chat/status'));
        state.chatStatus.configured = data.configured;
        state.chatStatus.checked = true;
        state.chatStatus.details = data;
        console.log('Chat status:', data);
        updateChatUI();
        return data.configured;
    } catch (error) {
        console.error('Error checking chat status:', error);
        const message = await getApiErrorMessage(error, 'Unable to determine whether chat is configured.');
        displayMessage(`Error checking chat status: ${message}`, 'system');
        state.chatStatus.configured = false;
        state.chatStatus.checked = true;
        updateChatUI();
        return false;
    }
}

function updateChatUI() {
    if (state.chatStatus.configured) {
        if (chatContainer) chatContainer.style.display = 'flex';
        if (chatEmptyState) chatEmptyState.style.display = 'none';
    } else {
        if (chatContainer) chatContainer.style.display = 'none';
        if (chatEmptyState) chatEmptyState.style.display = 'flex';
    }
}

function initChatEmptyStateTabs() {
    const exampleTabs = document.querySelectorAll('.example-tab');
    const exampleContents = {
        'domino': document.getElementById('example-domino'),
        'openai': document.getElementById('example-openai'),
        'ollama': document.getElementById('example-ollama'),
        'together': document.getElementById('example-together')
    };

    exampleTabs.forEach(tab => {
        tab.addEventListener('click', () => {
            const example = tab.getAttribute('data-example');

            exampleTabs.forEach(t => t.classList.remove('active'));
            tab.classList.add('active');

            Object.keys(exampleContents).forEach(key => {
                if (exampleContents[key]) {
                    exampleContents[key].style.display = key === example ? 'block' : 'none';
                }
            });
        });
    });
}

function clearChat() {
    if (chatBox.children.length === 0) return;

    clearChatButton.disabled = true;

    fetchJson(apiUrl('chat/clear'), { method: 'POST' })
        .then(() => {
            chatBox.innerHTML = '';
            displayMessage('Chat history cleared. You can start a new conversation.', 'system');
        })
        .catch(async error => {
            console.error('Error clearing chat:', error);
            const message = await getApiErrorMessage(error, 'Please try again.');
            displayMessage(`Failed to clear chat history: ${message}`, 'system');
        })
        .finally(() => {
            clearChatButton.disabled = false;
        });
}

async function loadChatHistory() {
    try {
        const data = await fetchJson(apiUrl('chat/history'));
        const messages = Array.isArray(data.messages) ? data.messages : [];
        chatBox.innerHTML = '';

        if (messages.length === 0) {
            displayMessage('Welcome to Data Explorer! Please select and load a dataset to get started.', 'system');
            return;
        }

        messages.forEach(message => {
            if (message && message.sender === 'tool_result') {
                displayToolResult(message);
                return;
            }
            if (!message || !message.text || !message.sender) return;
            displayMessage(message.text, message.sender, message.charts || null);
        });
    } catch (error) {
        console.error('Error loading chat history:', error);
        chatBox.innerHTML = '';
        displayMessage('Welcome to Data Explorer! Please select and load a dataset to get started.', 'system');
    }
}

function sendMessage() {
    const messageText = userInput.value.trim();
    if (messageText === '') {
        return;
    }

    if (!state.currentDataset) {
        displayMessage('Please load a dataset first before asking questions.', 'system');
        return;
    }

    displayMessage(messageText, 'user');

    userInput.value = '';

    sendButton.disabled = true;
    userInput.disabled = true;

    const thinkingElement = showThinkingAnimation();

    fetchJson(apiUrl('chat'), {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ message: messageText }),
    })
    .then(data => {
        console.log('Received data from server:', data);
        // Hidden-from-AI results are shown first: the tools ran before the agent replied.
        (data.user_only_results || []).forEach(displayToolResult);
        if (data.notice) {
            displayMessage(data.notice, 'system');
        }
        if (data.response) {
            console.log('Charts received:', data.charts);
            displayMessage(data.response, 'agent', data.charts);
        }
    })
    .catch(async error => {
        console.error('Error:', error);
        const data = await getApiErrorPayload(error);
        if (data && data.error) {
            console.error('Agent error:', data.error);
            console.error('Error details:', data.error_detail);
            console.error('Error type:', data.error_type);

            const apiMessage = await getApiErrorMessage(error, data.error);
            let errorMsg = `Error: Could not get response from agent.\n\n${apiMessage}`;
            if (data.error_detail) {
                errorMsg += '\nDetails: ' + data.error_detail;
            }
            if (data.error_type) {
                errorMsg += '\nError Type: ' + data.error_type;
            }
            errorMsg += '\n\nPlease check the Domino app logs for more details.';

            displayMessage(errorMsg, 'error');
            return;
        }
        const message = await getApiErrorMessage(error, 'Make sure the Flask server is running.');
        displayMessage(`Error: Could not connect to the server. ${message}`, 'error');
    })
    .finally(() => {
        removeThinkingAnimation(thinkingElement);

        sendButton.disabled = false;
        userInput.disabled = false;
        userInput.focus();
    });
}

function showThinkingAnimation() {
    const thinkingElement = document.createElement('div');
    thinkingElement.classList.add('message', 'agent-message', 'thinking-message');
    thinkingElement.innerHTML = `
        <div class="thinking-animation">
            <span class="thinking-text">Thinking</span>
            <div class="thinking-dots">
                <span class="dot"></span>
                <span class="dot"></span>
                <span class="dot"></span>
            </div>
        </div>
    `;
    chatBox.appendChild(thinkingElement);
    chatBox.scrollTop = chatBox.scrollHeight;
    return thinkingElement;
}

function removeThinkingAnimation(thinkingElement) {
    if (thinkingElement && thinkingElement.parentNode) {
        thinkingElement.parentNode.removeChild(thinkingElement);
    }
}

function renderChartError(containerId, message) {
    const container = document.getElementById(containerId);
    if (!container) return;

    const errorElement = document.createElement('p');
    errorElement.classList.add('error');
    errorElement.textContent = message;
    container.replaceChildren(errorElement);
}

export function displayMessage(text, sender, charts = null) {
    // Lazy-resolve chatBox so callers that fire before initChat()
    // (e.g. very-early dataset-load error paths) still work.
    if (!chatBox) {
        chatBox = document.getElementById('chat-box');
        if (!chatBox) return;
    }

    const messageElement = document.createElement('div');
    messageElement.classList.add('message', `${sender}-message`);
    messageElement.dataset.testid = 'chat-message';
    messageElement.dataset.sender = sender;

    if (sender === 'agent') {
        messageElement.classList.add('markdown-body');
        messageElement.innerHTML = renderMarkdown(text);
    } else {
        // User, system, and error messages are literal text: a user typing
        // `**x**` or `<b>` must see exactly that.
        messageElement.innerHTML = escapeHtml(text).replace(/\n/g, '<br>');
    }
    chatBox.appendChild(messageElement);

    if (charts && charts.length > 0) {
        charts.forEach((chartSpec, index) => {
            const chartContainer = document.createElement('div');
            chartContainer.classList.add('chart-container');
            chartContainer.id = `chart-${Date.now()}-${index}`;
            chatBox.appendChild(chartContainer);

            renderChart(chartContainer.id, chartSpec);
        });
    }

    chatBox.scrollTop = chatBox.scrollHeight;
}

function textElement(tag, className, text) {
    const el = document.createElement(tag);
    if (className) el.className = className;
    el.textContent = text;
    return el;
}

function formatBytes(bytes) {
    if (!Number.isFinite(bytes)) return '';
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// Render one result of a tool whose output is hidden from the AI. `result` is an
// entry of `user_only_results` / a `tool_result` history item:
// { tool_call_id, tool_name, title, args, content, truncated, size_bytes }
// or, for a failed call, { ..., is_error: true, error }.
// All values are inserted as text; nothing from the tool is parsed as HTML.
export function displayToolResult(result) {
    if (!chatBox) {
        chatBox = document.getElementById('chat-box');
        if (!chatBox) return;
    }
    if (!result) return;

    const card = document.createElement('div');
    card.classList.add('message', 'tool-result-message');
    card.dataset.testid = 'tool-result-card';
    card.dataset.toolName = result.tool_name || '';
    if (result.is_error) card.classList.add('tool-result-error');

    const header = document.createElement('div');
    header.className = 'tool-result-header';
    header.appendChild(textElement('span', 'tool-result-title', result.title || result.tool_name || 'Tool result'));

    const badge = document.createElement('span');
    badge.className = 'tool-result-badge';
    badge.dataset.testid = 'tool-result-badge';
    badge.setAttribute('role', 'note');
    badge.setAttribute('aria-label', HIDDEN_FROM_AI_TOOLTIP);
    badge.title = HIDDEN_FROM_AI_TOOLTIP;
    badge.tabIndex = 0;
    badge.innerHTML = EYE_SLASH_SVG;
    badge.appendChild(textElement('span', 'tool-result-badge-label', HIDDEN_FROM_AI_LABEL));
    header.appendChild(badge);
    card.appendChild(header);

    const args = result.args && Object.keys(result.args).length > 0 ? result.args : null;
    if (args) {
        const details = document.createElement('details');
        details.className = 'tool-result-args';
        details.appendChild(textElement('summary', '', 'Arguments'));
        details.appendChild(textElement('pre', '', JSON.stringify(args, null, 2)));
        card.appendChild(details);
    }

    const body = document.createElement('div');
    body.className = 'tool-result-body';
    if (result.is_error) {
        body.appendChild(textElement('div', 'tool-result-failed', 'Tool failed'));
        body.appendChild(textElement('pre', 'tool-result-error-text', result.error || 'Unknown error'));
    } else {
        const content = result.truncated && result.content && 'preview' in result.content
            ? result.content.preview
            : result.content;
        body.appendChild(renderToolOutput(content));
        if (result.truncated) {
            const size = formatBytes(result.size_bytes);
            body.appendChild(textElement(
                'div',
                'tool-output-note',
                `This output was too large to keep in full${size ? ` (${size})` : ''}. Showing the first part.`,
            ));
        }
    }
    card.appendChild(body);

    chatBox.appendChild(card);
    chatBox.scrollTop = chatBox.scrollHeight;
}

function renderChart(containerId, chartSpec) {
    console.log('Rendering chart with spec:', chartSpec);

    const { type, title, data } = chartSpec;

    if (!type) {
        console.error('Chart type is missing:', chartSpec);
        renderChartError(containerId, 'Invalid chart: missing type');
        return;
    }

    if (!data) {
        console.error('Chart data is missing:', chartSpec);
        renderChartError(containerId, 'Invalid chart: missing data');
        return;
    }

    try {
        switch (type) {
            case 'bar':
                renderBarChart(containerId, title, data);
                break;
            case 'scatter':
                renderScatterChart(containerId, title, data);
                break;
            case 'line':
                renderLineChart(containerId, title, data);
                break;
            case 'pie':
                renderPieChart(containerId, title, data);
                break;
            case 'histogram':
                renderHistogram(containerId, title, data);
                break;
            case 'boxplot':
                renderBoxplot(containerId, title, data);
                break;
            case 'heatmap':
                renderHeatmap(containerId, title, data);
                break;
            case 'grouped_bar':
                renderGroupedBarChart(containerId, title, data);
                break;
            default:
                console.error('Unknown chart type:', type);
                renderChartError(containerId, 'Unknown chart type: ' + String(type));
        }
    } catch (error) {
        console.error('Error rendering chart:', error);
        console.error('Chart spec was:', chartSpec);
        renderChartError(containerId, 'Error rendering chart: ' + String(error.message));
    }
}


export function initChat() {
    chatBox = document.getElementById('chat-box');
    userInput = document.getElementById('user-input');
    sendButton = document.getElementById('send-button');
    clearChatButton = document.getElementById('clear-chat-button');
    chatContainer = document.getElementById('chat-container');
    chatEmptyState = document.getElementById('chat-empty-state');

    initChatEmptyStateTabs();

    sendButton.addEventListener('click', sendMessage);
    userInput.addEventListener('keypress', (event) => {
        if (event.key === 'Enter') {
            sendMessage();
        }
    });
    clearChatButton.addEventListener('click', clearChat);

    loadChatHistory();
}
