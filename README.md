# ☎️ AI モーニングコール

指定した時刻に AI が電話をかけてきて起こしてくれる、個人利用向けの Web アプリです。
電話に出て「起きた？」に答えると、AI が返答を聞き取って**本当に起きたか判定**し、
まだ眠そうなら 5 分後に自動で再架電します。

- 電話基盤: Twilio Programmable Voice / ConversationRelay
- AI: Anthropic Claude API（既定 `claude-haiku-4-5`）
- バックエンド: Python 3.11+ / FastAPI
- DB: SQLite / スケジューラ: APScheduler（Firebase 不要・クレカ登録不要）
- macOS / Xcode 不要。Windows だけで開発・運用できます。

---

## 目次

1. [できること](#できること)
2. [構成](#構成)
3. [セットアップ](#セットアップ)（← まずここ。あなたの作業があります）
4. [起動](#起動)
5. [動作確認: v0 → v1 → v2 → v3](#動作確認)
6. [API リファレンス](#api-リファレンス)
7. [設定できる項目](#設定できる項目)
8. [料金と安全装置](#料金と安全装置)
9. [本番デプロイ](#本番デプロイ)
10. [トラブルシューティング](#トラブルシューティング)

---

## できること

| バージョン | 内容 | 状態 |
|---|---|---|
| v0 | Twilio から自分の携帯に発信できることの確認 | 手順のみ（コード不要） |
| v1 | 指定日時に自動でモーニングコール（固定メッセージ読み上げ） | ✅ 実装済み |
| v2 | AI が返答を聞き取って起床判定 → 二度寝なら再架電 | ✅ 実装済み |
| v3 | チャット欄に「明日7時に起こして」と書くだけで予約 | ✅ 実装済み |

### 通話の流れ（v2）

```
予定時刻 ──> APScheduler が発火
              │
              ▼
         Twilio Voice API で発信
              │
              ▼
   Twilio が /twilio/voice/{id} を叩く ──> ConversationRelay の TwiML を返す
              │
              ▼
   Twilio が WebSocket /conversation-relay に接続
              │
   AI「おはようございます。起きていますか？」
              │
        あなたの返答（Twilio が文字起こし）
              │
              ▼
        Claude が判定
         ├─ 起きた   ──> お礼を言って終話（status: completed）
         ├─ 眠そう   ──> 「5分後にまたかけるね」と言って終話 + 再架電を予約
         └─ 無音15秒×2 ──> 応答なし扱い + 再架電を予約
```

---

## 構成

```
AI-morning-call/
├── backend/
│   ├── main.py            # FastAPI エントリポイント・全ルーティング
│   ├── config.py          # 環境変数の読み込み
│   ├── db.py              # SQLite 接続
│   ├── models.py          # DB モデル（Call / ConversationLog / Setting）
│   ├── schemas.py         # リクエスト検証
│   ├── auth.py            # APIキー認証・Twilio 署名検証
│   ├── scheduler.py       # APScheduler・発信ジョブ・再架電
│   ├── twilio_client.py   # 発信と TwiML 生成
│   ├── conversation.py    # ConversationRelay の WebSocket ハンドラ
│   ├── llm_client.py      # Claude API 呼び出し
│   └── requirements.txt
├── frontend/              # v3 のチャット UI（素の HTML/CSS/JS）
│   ├── index.html
│   ├── app.js
│   └── style.css
├── .env.example
└── README.md
```

---

## セットアップ

### あなたにお願いする作業（所要 30 分ほど）

#### ① Twilio アカウントを作る

1. https://www.twilio.com/try-twilio から登録
2. コンソール右下の **Account SID** と **Auth Token** を控える

#### ② 電話番号を買う

Twilio コンソール → **Phone Numbers → Manage → Buy a number**

- **Voice** の Capability が付いた番号を選ぶ
- 日本の番号（+81）は本人確認書類の提出が必要で時間がかかります。
  **アメリカの番号（+1, 月 $1〜）でも日本の携帯にかけられる**ので、まずはこちらが手軽です。
- 買った番号を控える（`+1XXXXXXXXXX` の形式）

#### ③ 日本への発信を許可する ⚠️ ここでハマりがち

Twilio は既定で国際発信をブロックしています。

**Voice → Settings → Geo Permissions** を開き、**Japan** にチェックを入れて保存してください。
（これを忘れると発信時に `Error 21215` が出ます）

#### ④ 自分の携帯番号を認証する（トライアルアカウントの場合）

無料トライアル中は、事前に登録した番号にしか発信できません。

**Phone Numbers → Manage → Verified Caller IDs** から自分の携帯番号を追加し、
届いた認証コードを入力してください。

#### ⑤ Anthropic API キーを取得する

https://console.anthropic.com/settings/keys で発行し、控えてください。
（`sk-ant-` で始まる文字列。使用量に応じた課金が発生します）

#### ⑥ ngrok を用意する（ローカル開発用）

Twilio の Webhook がローカル PC に届くようにするために必要です。

1. https://ngrok.com/download からダウンロード、または `winget install ngrok`
2. https://dashboard.ngrok.com/get-started/your-authtoken でトークンを取得
3. 設定：

```bash
ngrok config add-authtoken ここにトークン
```

---

### プロジェクトのセットアップ

```bash
cd C:/Users/kouki/AI-morning-call
py -m venv .venv
.venv/Scripts/python.exe -m pip install -r backend/requirements.txt
```

`.env.example` を `.env` にコピーします。

```bash
cp .env.example .env
```

API キー（このアプリを守るためのパスワードのようなもの）を生成します。

```bash
.venv/Scripts/python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
```

`.env` を開いて、控えておいた値を埋めてください。

```ini
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_PHONE_NUMBER=+1XXXXXXXXXX          # ② で買った番号
ANTHROPIC_API_KEY=sk-ant-xxxxxxxx          # ⑤ のキー
APP_BASE_URL=                              # 次の手順で ngrok の URL を入れる
API_KEY=                                   # 上で生成した文字列
```

> `.env` は `.gitignore` 済みなので、Git にコミットされません。

---

## 起動

**ターミナル 1 — ngrok**

```bash
ngrok http 8000
```

表示された `https://xxxx-xx-xx.ngrok-free.app` を `.env` の `APP_BASE_URL` に貼ってください（末尾のスラッシュは不要）。

> ⚠️ 無料版 ngrok は**再起動のたびに URL が変わります**。変わったら `.env` を更新してサーバを再起動してください。

**ターミナル 2 — アプリ**

```bash
.venv/Scripts/python.exe -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

起動できたか確認：

```bash
curl http://localhost:8000/health
```

`"missing_env": []` になっていれば設定完了です。

- チャット UI: http://localhost:8000/
- Swagger UI: http://localhost:8000/docs

---

## 動作確認

以下の例では API キーを環境変数に入れておくと楽です。

```bash
export API_KEY=$(grep '^API_KEY=' .env | cut -d= -f2)
```

> ⚠️ **Windows で日本語を含む curl を叩くときの注意**
> Git Bash / PowerShell では `-d '{"message":"おはよう"}'` のように日本語を直接書くと
> 文字コードが壊れて `There was an error parsing the body` になります。
> 日本語を含む場合は、UTF-8 のファイルに書いて `--data-binary @body.json` で渡すか、
> ブラウザの Swagger UI（http://localhost:8000/docs）から実行してください。
> （PowerShell では `curl` ではなく `curl.exe` を使ってください）

### v0: まず 1 回、手で電話をかけてみる

コードを書かずに Twilio 側の設定だけ確認します。

1. Twilio コンソール → **Voice → TwiML → TwiML Bins** → **Create new TwiML Bin**
2. 中身にこれを貼って保存：

```xml
<Response>
  <Say language="ja-JP">おはようございます。テスト通話です。</Say>
</Response>
```

3. **Phone Numbers → Manage → Active numbers** で買った番号を開き、
   *A call comes in* に作った TwiML Bin を割り当てて保存
4. その Twilio 番号に自分の携帯からかけて、日本語が読み上げられれば OK

ここまで動けば、Twilio 側の設定（番号・Geo Permissions）は問題ありません。

### v1: 指定時刻に発信する

2 分後に電話がかかってくるよう予約します。`phone_number` は**あなたの携帯番号**（E.164 形式）です。

```bash
curl -X POST http://localhost:8000/calls/schedule \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{"phone_number":"+819012345678","call_at":"2026-08-13T07:00:00+09:00","message":"おはよう、起きる時間だよ","use_conversation":false}'
```

`use_conversation: false` にすると、AI 会話なしでメッセージを読み上げるだけの v1 動作になります。

予約の確認：

```bash
curl -H "X-API-Key: $API_KEY" http://localhost:8000/calls
```

キャンセル：

```bash
curl -X DELETE -H "X-API-Key: $API_KEY" http://localhost:8000/calls/1
```

### v2: AI と会話して起床判定

`use_conversation` を省略（既定 `true`）すれば ConversationRelay が使われます。

```bash
curl -X POST http://localhost:8000/calls/schedule \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{"phone_number":"+819012345678","call_at":"2026-08-13T07:00:00+09:00","message":"おはよう、起きる時間だよ"}'
```

電話に出ると AI が話しかけてきます。

- 「はい、起きました」→ お礼を言って終話
- 「うーん、あと 5 分…」→「じゃあ 5 分後にまたかけるね」と言って終話し、5 分後に再架電
- 何も言わずに 15 秒 × 2 回 → 応答なし扱いで再架電

通話後、会話ログと判定結果を確認できます：

```bash
curl -H "X-API-Key: $API_KEY" http://localhost:8000/calls/1
```

```json
{
  "id": 1,
  "status": "completed",
  "logs": [
    { "role": "user", "content": "はい、起きました", "decision": null },
    { "role": "assistant", "content": "よかったです。いってらっしゃい。", "decision": "awake" }
  ]
}
```

### v3: チャットで予約する

ブラウザで http://localhost:8000/ を開きます。

1. **API キー**に `.env` の `API_KEY` を入力
2. **電話番号**に自分の携帯番号（`+819012345678`）を入力して「保存」
3. チャット欄に自然文で入力

```
明日の朝7時に優しく起こして
```

Claude が日時とメッセージを構造化抽出し、そのまま予約されます。以降は電話番号の入力は不要です。

API から直接叩く場合：

```bash
curl -X POST http://localhost:8000/chat/schedule \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{"text":"明日の朝7時に優しく起こして"}'
```

---

## API リファレンス

管理系のエンドポイントはすべて `X-API-Key` ヘッダが必要です。

| Method | Path | 説明 |
|---|---|---|
| `GET` | `/health` | 死活確認・設定漏れの確認 |
| `POST` | `/calls/schedule` | 発信を予約 |
| `GET` | `/calls` | 予約・実行済み一覧（`?limit=` `?status=`） |
| `GET` | `/calls/{id}` | 詳細 + 会話ログ |
| `DELETE` | `/calls/{id}` | 予約をキャンセル |
| `POST` | `/chat/schedule` | 自然文から予約（v3） |
| `GET` / `PUT` | `/settings/phone` | 電話番号の保存・取得 |
| `POST` | `/twilio/voice/{id}` | Twilio 用 Webhook（TwiML を返す） |
| `POST` | `/twilio/status/{id}` | Twilio 用ステータスコールバック |
| `WS` | `/conversation-relay` | ConversationRelay 接続先 |

Twilio 向けエンドポイントは APIキーではなく、**通話ごとのトークン + Twilio 署名検証**で保護しています。

### `status` の意味

| status | 意味 |
|---|---|
| `scheduled` | 予約済み・発信待ち |
| `dialing` | Twilio に発信を依頼した |
| `in_progress` | 通話中 |
| `completed` | 正常終了（起床確認 or 再架電予約済み） |
| `no_answer` | 応答なし・無音 |
| `failed` | 発信エラー（自動リトライしません） |
| `missed` | サーバ停止中に予定時刻を過ぎた |
| `canceled` | 手動キャンセル |

---

## 設定できる項目

`.env` で調整できます（詳細は `.env.example` のコメント）。

| 変数 | 既定 | 説明 |
|---|---|---|
| `ANTHROPIC_MODEL` | `claude-haiku-4-5` | 判定精度が足りなければ `claude-sonnet-5` に変更 |
| `SNOOZE_MINUTES` | `5` | 再架電までの分数 |
| `MAX_RETRIES` | `3` | 再架電の上限回数（課金の暴走防止） |
| `SILENCE_TIMEOUT_SECONDS` | `15` | 無音とみなす秒数 |
| `MAX_SILENCE_STRIKES` | `2` | 無音が何回続いたら諦めるか |
| `MAX_TURNS` | `8` | 1 通話あたりの最大やり取り回数 |
| `TIMEZONE` | `Asia/Tokyo` | 日時解釈のタイムゾーン |
| `TWILIO_TTS_PROVIDER` / `TWILIO_VOICE` | 空 | 音声の指定（空なら Twilio の既定） |
| `TWILIO_VALIDATE_SIGNATURE` | `true` | Twilio 署名検証。切り分け時のみ `false` に |

### モデルを切り替える

```ini
ANTHROPIC_MODEL=claude-sonnet-5
```

を `.env` に書いてサーバを再起動するだけです。コード変更は不要です。

---

## 料金と安全装置

このアプリは**実際にお金がかかります**。以下の安全装置を入れてあります。

| 装置 | 内容 |
|---|---|
| 再架電の上限 | `MAX_RETRIES`（既定 3 回）を超えたら打ち切り |
| 発信失敗時の非リトライ | Twilio エラー時はログと DB に記録して終了。次回起動時も再試行しない |
| 起動時の期限切れ処理 | サーバ停止中に過ぎた予約は `missed` にして発信しない |
| 会話ターン数の上限 | `MAX_TURNS`（既定 8）で打ち切り |
| 認証 | 管理 API は固定 APIキー、Twilio Webhook は署名 + 通話別トークン |

おおよその費用感（2026 年時点の目安。最新は各社の料金ページを確認してください）:

- Twilio 電話番号: 月 $1〜
- 日本の携帯への発信: 1 分あたり $0.1 前後
- ConversationRelay: 1 分あたり別途課金
- Claude Haiku 4.5: 1 通話の判定で $0.001 未満

**Twilio コンソールで使用量アラートを設定しておくことを強くおすすめします。**

会話ログはローカルの SQLite（`morning_call.db`）にのみ保存され、外部には送信しません。
（起床判定のためにユーザーの発話テキストは Anthropic API へ送られます）

---

## 本番デプロイ

ローカル + ngrok は PC を起動しっぱなしにする必要があるので、常用するならホスティングを検討してください。

### 注意点

- **常時起動が必須**です。スリープする無料プランでは予定時刻にジョブが動きません。
  - Render 無料プランなど: 外部から定期 ping を打つか、有料の常時起動プランにする
  - Railway / Google Cloud Run（min-instances=1）: 常時起動できる構成にする
- `APP_BASE_URL` を本番の HTTPS URL に変更してください
- SQLite を使うので、**永続ディスク**をマウントできるサービスを選んでください
  （コンテナが再作成されると予約が消えます）

### 起動コマンド例

```bash
uvicorn backend.main:app --host 0.0.0.0 --port $PORT
```

> ワーカーは 1 プロセス（既定）のままにしてください。APScheduler をプロセス内に持つ構成のため、
> 複数ワーカーにすると同じ予約が重複して発信されます。

---

## トラブルシューティング

| 症状 | 原因と対処 |
|---|---|
| 電話がかかってこない | `GET /health` の `missing_env` が空か、`scheduled_jobs` が 1 以上かを確認 |
| `Error 21215` (Geo Permission) | Twilio の **Voice → Settings → Geo Permissions** で Japan を許可 |
| `Error 21219` / 発信が拒否される | トライアル中は **Verified Caller IDs** に発信先番号の登録が必要 |
| `Error 21212` | `TWILIO_PHONE_NUMBER` が E.164 形式（`+1...`）か、購入済みの番号か確認 |
| 電話は鳴るが無音／すぐ切れる | `APP_BASE_URL` が現在の ngrok URL と一致しているか確認。ngrok は再起動で URL が変わります |
| `Twilio 署名の検証に失敗しました` | 同上（URL 不一致）。切り分け中は `TWILIO_VALIDATE_SIGNATURE=false` で回避可 |
| AI が話さない／会話にならない | ngrok の Web インスペクタ（http://127.0.0.1:4040）で `/twilio/voice/...` のレスポンスを確認。`wss://` の URL が正しいか見る |
| 日本語が変な発音になる | `.env` で `TWILIO_TTS_PROVIDER=Google` と `TWILIO_VOICE=ja-JP-Neural2-B` を試す |
| 起床判定がおかしい | `ANTHROPIC_MODEL=claude-sonnet-5` に変更して再起動 |
| 予約が消えた | `morning_call.db` が消えていないか確認。デプロイ先では永続ディスクが必要です |
| サーバ再起動後に予約が動かない | 予定時刻を過ぎたものは `missed` になります（仕様）。未来の予約は自動で復元されます |

### ログの見方

サーバのログに、発信・判定・再架電がすべて出力されます。

```
INFO  backend.scheduler: 発信ジョブを登録しました call_id=1 run_date=2026-08-12T22:00:00+00:00
INFO  backend.twilio_client: 発信しました call_id=1 sid=CAxxxxxxxx
INFO  backend.conversation: ConversationRelay 接続 call_id=1
INFO  backend.conversation: 判定 call_id=1 decision=snooze end_call=True
INFO  backend.conversation: 再架電を予約しました call_id=1 -> 2
```

---

## 今後の拡張案（今回のスコープ外）

- モバイルアプリ化（Flutter 等、macOS 非依存の技術）
- マルチユーザー対応（このタイミングで Firestore 移行を検討）
- 定期予約（毎朝 7 時など）
- 天気やニュースを読み上げる
