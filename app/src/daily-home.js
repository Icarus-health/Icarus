// Kleine Interaktion fuer die sichtbare Alltagsoberflaeche. Die bestehende
// Chat-Logik bleibt alleinige Stelle fuer Nachrichten und Netzwerkanfragen.

const input = document.querySelector("#chat-input");
const form = document.querySelector("#chat-form");
const chatView = document.querySelector("#view-chat");
const messages = document.querySelector("#messages");

function updateConversationState() {
  if (!chatView || !messages) return;
  chatView.classList.toggle("has-conversation", messages.children.length > 0);
}

for (const starter of document.querySelectorAll(".starter")) {
  starter.addEventListener("click", () => {
    if (!input) return;
    input.value = starter.dataset.prompt ?? "";
    input.focus();
  });
}

form?.addEventListener("submit", () => {
  window.setTimeout(updateConversationState, 0);
});

new MutationObserver(updateConversationState).observe(messages, { childList: true });
updateConversationState();
