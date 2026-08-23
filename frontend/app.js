"use strict";

const $ = (id) => document.getElementById(id);

const phoneInput = $("phone-number");
const settingsStatus = $("settings-status");
const loginStatus = $("login-status");
const chatLog = $("chat-log");
const chatForm = $("chat-form");
const chatInput = $("chat-input");
const callsList = $("calls-list");

const loginPanel = $("login-panel");
const settingsPanel = $("settings-panel");
const chatPanel = $("chat-panel");
const callsPanel = $("calls-panel");

async function api(path, options = {}) {
  // 認証はログインで発行される HttpOnly Cookie が担う（同一オリジンなので自動送信）。
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });

  if (response.status === 401) {
    showLoggedOut();
    throw new Error("ログインしてください。");
  }

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

function showLoggedIn() {
  loginPanel.hidden = true;
  settingsPanel.hidden = false;
  chatPanel.hidden = false;
  callsPanel.hidden = false;
}

function showLoggedOut() {
  loginPanel.hidden = false;
  settingsPanel.hidden = true;
  chatPanel.hidden = true;
  callsPanel.hidden = true;
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

$("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const password = $("password").value;
  if (!password) return;

  loginStatus.textContent = "確認中…";
  loginStatus.className = "status";
  try {
    const response = await fetch("/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${response.status}`);
    }
    $("password").value = "";
    loginStatus.textContent = "";
    showLoggedIn();
    await loadPhone();
    await loadCalls();
  } catch (err) {
    loginStatus.textContent = err.message;
    loginStatus.className = "status err";
  }
});

$("logout").addEventListener("click", async () => {
  await fetch("/auth/logout", { method: "POST" });
  phoneInput.value = "";
  chatLog.innerHTML = "";
  showLoggedOut();
});

$("save-settings").addEventListener("click", async () => {
  const phone = phoneInput.value.trim();
  if (!phone) {
    setStatus("電話番号を入力してください。", "err");
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

// 初期化: Cookie が有効なら入力なしでそのまま使える
(async () => {
  // 旧バージョンが localStorage に置いていた API キーは不要になったので消す
  localStorage.removeItem("morning-call:apiKey");

  try {
    const { authenticated } = await (await fetch("/auth/status")).json();
    if (authenticated) {
      showLoggedIn();
      await loadPhone();
      await loadCalls();
      return;
    }
  } catch (_) {
    /* サーバに繋がらない場合はログイン画面を出す */
  }
  showLoggedOut();
})();
