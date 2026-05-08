// Chat UI module for the Data Explorer frontend.
//
// Owns the entire chat tab + cross-app system messaging:
//   - Chat configuration probe (`/chat/status`) and empty-state vs.
//     active-chat container toggle (the "OpenAI / Ollama / Together"
//     example tabs that show when the agent isn't configured).
//   - Send / Enter-key / Clear chat wiring.
//   - User and agent message rendering, including the "Thinking…" dots
//     animation while the agent is responding.
//   - Chart embedding inside agent replies (renderChart dispatcher).
//     The 8 type-specific renderers (bar / scatter / line / pie /
//     histogram / boxplot / heatmap / grouped_bar) live in
//     `modules/chart-renderers.js` so future chart consumers can share
//     them — see that file's header for why explore-charts deliberately
//     keeps its own renderer set rather than reusing these.
//   - The welcome message displayed on first load.
//
// Three exports:
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
import { apiUrl, fetchJson } from '../core/api.js';
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
        .catch(error => {
            console.error('Error clearing chat:', error);
            displayMessage('Failed to clear chat history. Please try again.', 'system');
        })
        .finally(() => {
            clearChatButton.disabled = false;
        });
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
        if (data.response) {
            console.log('Charts received:', data.charts);
            displayMessage(data.response, 'agent', data.charts);
        } else if (data.error) {
            console.error('Agent error:', data.error);
            console.error('Error details:', data.error_detail);
            console.error('Error type:', data.error_type);

            let errorMsg = 'Error: Could not get response from agent.\n\n';
            if (data.error_detail) {
                errorMsg += 'Details: ' + data.error_detail;
            }
            if (data.error_type) {
                errorMsg += '\nError Type: ' + data.error_type;
            }
            errorMsg += '\n\nPlease check the Domino app logs for more details.';

            displayMessage(errorMsg, 'agent');
        }
    })
    .catch((error) => {
        console.error('Error:', error);
        displayMessage('Error: Could not connect to the server. Make sure the Flask server is running.', 'agent');
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

function appendTextWithLineBreaks(element, text) {
    const lines = String(text ?? '').split(/\r?\n/);
    lines.forEach((line, index) => {
        if (index > 0) {
            element.appendChild(document.createElement('br'));
        }
        element.appendChild(document.createTextNode(line));
    });
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
    appendTextWithLineBreaks(messageElement, text);
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

function renderChart(containerId, chartSpec) {
    console.log('Rendering chart with spec:', chartSpec);

    if (!chartSpec || typeof chartSpec !== 'object') {
        console.error('Chart spec is invalid:', chartSpec);
        renderChartError(containerId, 'Invalid chart: malformed specification');
        return;
    }

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

    displayMessage('Welcome to Data Explorer! Please select and load a dataset to get started.', 'system');
}
