"use strict";

const $ = (id) => document.getElementById(id);

const apiKeyInput = $("api-key");
const phoneInput = $("phone-number");
const settingsStatus = $("settings-status");
const chatLog = $("chat-log");
const chatForm = $("chat-form");
const chatInput = $("chat-input");
const callsList = $("calls-list");

const STORAGE_KEY = "morning-call:apiKey";

function getApiKey() {
  return apiKeyInput.value.trim();
}

async function api(path, options = {}) {
  const key = getApiKey();
  if (!key) {
    throw new Error("API キーを入力してください。");
  }
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": key,
      ...(options.headers || {}),
    },
  });

  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const body = await response.json();
      if (body.detail) {
        detail =
          typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
      }
    } catch (_) {
      /* JSON でないレスポンスはそのまま */
    }
    throw new Error(detail);
  }

  if (response.status === 204) return null;
  return response.json();
}

function setStatus(text, kind = "") {
  settingsStatus.textContent = text;
  settingsStatus.className = `status ${kind}`;
}

function addBubble(text, kind) {
  const div = document.createElement("div");
  div.className = `bubble ${kind}`;
  div.textContent = text;
  chatLog.appendChild(div);
  chatLog.scrollTop = chatLog.scrollHeight;
}

function formatDateTime(iso) {
  const d = new Date(iso);
  return d.toLocaleString("ja-JP", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

const STATUS_LABELS = {
  scheduled: "予約済み",
  dialing: "発信中",
  in_progress: "通話中",
  completed: "完了",
  no_answer: "応答なし",
  failed: "失敗",
  missed: "未実行",
  canceled: "キャンセル",
};

function renderCalls(calls) {
  callsList.innerHTML = "";
  if (!calls.length) {
    const p = document.createElement("p");
    p.className = "empty";
    p.textContent = "まだ予約はありません。";
    callsList.appendChild(p);
    return;
  }

  for (const call of calls) {
    const item = document.createElement("div");
    item.className = "call-item";

    const left = document.createElement("div");
    const when = document.createElement("div");
    when.className = "when";
    when.textContent = formatDateTime(call.call_at);
    const msg = document.createElement("div");
    msg.className = "msg";
    msg.textContent = call.message;
    left.appendChild(when);
    left.appendChild(msg);

    const right = document.createElement("div");
    const badge = document.createElement("span");
    badge.className = `badge ${call.status}`;
    badge.textContent = STATUS_LABELS[call.status] || call.status;
    right.appendChild(badge);

    if (call.status === "scheduled") {
      const cancel = document.createElement("button");
      cancel.type = "button";
      cancel.className = "ghost";
      cancel.textContent = "取消";
      cancel.style.marginLeft = "0.5rem";
      cancel.addEventListener("click", async () => {
        try {
          await api(`/calls/${call.id}`, { method: "DELETE" });
          await loadCalls();
        } catch (err) {
          addBubble(String(err.message), "error");
        }
      });
      right.appendChild(cancel);
    }

    item.appendChild(left);
    item.appendChild(right);
    callsList.appendChild(item);
  }
}

async function loadCalls() {
  try {
    const data = await api("/calls?limit=30");
    renderCalls(data.calls);
  } catch (err) {
    callsList.innerHTML = "";
    const p = document.createElement("p");
    p.className = "empty";
    p.textContent = `読み込めませんでした: ${err.message}`;
    callsList.appendChild(p);
  }
}

async function loadPhone() {
  try {
    const data = await api("/settings/phone");
    if (data.phone_number) {
      phoneInput.value = data.phone_number;
    }
  } catch (_) {
    /* 未認証などは無視 */
  }
}

$("save-settings").addEventListener("click", async () => {
  const key = getApiKey();
  if (!key) {
    setStatus("API キーを入力してください。", "err");
    return;
  }
  localStorage.setItem(STORAGE_KEY, key);

  const phone = phoneInput.value.trim();
  if (!phone) {
    setStatus("API キーを保存しました。", "ok");
    await loadCalls();
    return;
  }

  try {
    await api("/settings/phone", {
      method: "PUT",
      body: JSON.stringify({ phone_number: phone }),
    });
    setStatus("保存しました。", "ok");
    await loadCalls();
  } catch (err) {
    setStatus(err.message, "err");
  }
});

$("refresh-calls").addEventListener("click", loadCalls);

chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = chatInput.value.trim();
  if (!text) return;

  addBubble(text, "user");
  chatInput.value = "";

  try {
    const data = await api("/chat/schedule", {
      method: "POST",
      body: JSON.stringify({ text }),
    });
    addBubble(data.reply, "bot");
    if (data.scheduled) {
      await loadCalls();
    }
  } catch (err) {
    addBubble(err.message, "error");
  }
});

// 初期化
const savedKey = localStorage.getItem(STORAGE_KEY);
if (savedKey) {
  apiKeyInput.value = savedKey;
  loadPhone();
  loadCalls();
} else {
  renderCalls([]);
}
