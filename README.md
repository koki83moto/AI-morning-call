# ☎️ AI モーニングコール

指定した時刻に AI が電話をかけてきて起こしてくれる、個人利用向けの Web アプリです。
電話に出て「起きた？」に答えると、AI が返答を聞き取って**本当に起きたか判定**し、
まだ眠そうなら 5 分後に自動で再架電します。

- 電話基盤: Telnyx Programmable Voice (TeXML) / Conversation Relay
- AI: Anthropic Claude API（既定 `claude-haiku-4-5`）
- バックエンド: Python 3.11+ / FastAPI
- DB: SQLite / スケジューラ: APScheduler（Firebase 不要）
- macOS / Xcode 不要。Windows だけで開発・運用できます。

> **なぜ Telnyx なのか**
> 当初は Twilio で実装していましたが、本人確認（Compliance Profile）が通らず電話番号を
> 購入できなかったため Telnyx へ移行しました。Telnyx の Conversation Relay は Twilio の
> ConversationRelay とほぼ同一仕様なので、会話処理のコードはそのまま流用できています。

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
| v0 | Telnyx から自分の携帯に発信できることの確認 | 手順のみ（コード不要） |
| v1 | 指定日時に自動でモーニングコール（固定メッセージ読み上げ） | ✅ 実装済み |
| v2 | AI が返答を聞き取って起床判定 → 二度寝なら再架電 | ✅ 実装済み |
| v3 | チャット欄に「明日7時に起こして」と書くだけで予約 | ✅ 実装済み |

### 通話の流れ（v2）

```
予定時刻 ──> APScheduler が発火
              │
              ▼
      Telnyx TeXML Calls API で発信
              │
              ▼
   Telnyx が /telnyx/voice/{id} を叩く ──> Conversation Relay の TeXML を返す
              │
              ▼
   Telnyx が WebSocket /conversation-relay に接続
              │
   AI「おはようございます。起きていますか？」
              │
        あなたの返答（Telnyx が文字起こし）
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
│   ├── auth.py            # APIキー認証・Telnyx 署名検証
│   ├── scheduler.py       # APScheduler・発信ジョブ・再架電
│   ├── telnyx_client.py   # 発信と TeXML 生成
│   ├── conversation.py    # Conversation Relay の WebSocket ハンドラ
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

#### ① Telnyx アカウントを作る

https://telnyx.com から登録します。番号購入前にクレジットカードの登録が必要な場合があります
（画面上部に `PRETRIAL` と出ているうちは機能制限がかかります）。

#### ② 電話番号を買う

**Numbers → Search & Buy Numbers**

- **Country**: United States
- **Features**: `Voice` にチェック
- **Number type**: Local

US の Local 番号は月額無料のものが多く、そこから日本の携帯にも発信できます。
カート画面の *Connection or Application* / *Messaging Profile* / *Billing Group* は
空のままで購入して構いません（あとから割り当てます）。

#### ③ API キーを発行する

**Mission Control Portal → API Keys → Create API Key**

発行された `KEY...` を控えます。これが `TELNYX_API_KEY` です。

#### ④ 残高を入れる ⚠️ 忘れがち

**Billing** で入金するかクレジットカードの自動チャージを設定してください。
残高 $0 のままだと発信が失敗します。テストなら $5〜10 で十分です。

> `TELNYX_ACCOUNT_SID` は**空のままで構いません**。Account ID はポータルに表示されない
> ことがあるため、未設定なら `account_sid` の要らない発信エンドポイントに自動で切り替わります。

#### ⑤ TeXML Application を作る ⚠️ ここが肝

**Voice → TeXML Applications → Create TeXML Application**

- 名前は何でも構いません（例: `morning-call`）
- **Voice URL** は空でも動きます。発信のたびにアプリ側から `Url` を渡すためです
- 作成後に表示される **ID** が `TELNYX_APPLICATION_SID` です

作成したら **② で買った番号にこの Application を割り当ててください**
（Numbers → My Numbers → 番号を開いて Connection/Application を選択）。

#### ⑥ 日本への発信を許可する ⚠️ ここでハマりがち

**Voice → Outbound Voice Profiles**

プロファイルを作成（または既存のものを開き）、通話先として **Japan** を許可してください。
Telnyx は既定で国際発信を絞っているため、これを忘れると発信が失敗します。
作成したプロファイルは TeXML Application に紐づけます。

#### ⑦ Anthropic API キーを取得する

https://console.anthropic.com/settings/keys で発行し、控えてください。
（`sk-ant-` で始まる文字列。使用量に応じた課金が発生します）

#### ⑧ ngrok を用意する（ローカル開発用）

Telnyx の Webhook がローカル PC に届くようにするために必要です。

1. `winget install Ngrok.Ngrok`
2. https://dashboard.ngrok.com/get-started/your-authtoken でトークンを取得
3. 設定：

```bash
ngrok config add-authtoken ここにトークン
```

---

### プロジェクトのセットアップ

```bash
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
TELNYX_API_KEY=KEYxxxxxxxxxxxxxxxx      # ③
TELNYX_APPLICATION_SID=xxxxxxxxxxxx     # ⑤
TELNYX_PHONE_NUMBER=+1XXXXXXXXXX        # ② で買った番号
TELNYX_ACCOUNT_SID=                     # 空のままでOK
ANTHROPIC_API_KEY=sk-ant-xxxxxxxx       # ⑦
APP_BASE_URL=                           # 次の手順で ngrok の URL を入れる
API_KEY=                                # 上で生成した文字列
```

> `.env` は `.gitignore` 済みなので、Git にコミットされません。

---

## 起動

**ターミナル 1 — ngrok**

```bash
ngrok http 8000
```

表示された `https://xxxx-xx-xx.ngrok-free.dev` を `.env` の `APP_BASE_URL` に貼ってください（末尾のスラッシュは不要）。

> ⚠️ 無料版 ngrok は**再起動のたびに URL が変わります**。変わったら `.env` を更新してサーバを再起動してください。

**ターミナル 2 — アプリ**

```bash
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

> PowerShell では相対パスの実行ファイルに `.\` が必要です（`.venv/...` と書くと
> `CommandNotFoundException` になります）。Git Bash なら `.venv/Scripts/python.exe` でも動きます。

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

コードを書かずに Telnyx 側の設定だけ確認します。

Telnyx コンソールの **Voice → TeXML Applications** で作ったアプリの Voice URL に、
`<Say>` だけを返す静的な TeXML を置いた URL を設定し、
その番号に自分の携帯からかけて日本語が読み上げられれば OK です。

```xml
<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say language="ja-JP">おはようございます。テスト通話です。</Say>
</Response>
```

ここまで動けば、Telnyx 側の設定（番号・Application・Outbound Voice Profile）は問題ありません。

### v1: 指定時刻に発信する

2 分後に電話がかかってくるよう予約します。`phone_number` は**あなたの携帯番号**（E.164 形式）です。

```bash
curl -X POST http://localhost:8000/calls/schedule \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{"phone_number":"+819012345678","call_at":"2026-08-21T07:00:00+09:00","message":"おはよう、起きる時間だよ","use_conversation":false}'
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

`use_conversation` を省略（既定 `true`）すれば Conversation Relay が使われます。

```bash
curl -X POST http://localhost:8000/calls/schedule \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{"phone_number":"+819012345678","call_at":"2026-08-21T07:00:00+09:00","message":"おはよう、起きる時間だよ"}'
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
| `POST` | `/telnyx/voice/{id}` | Telnyx 用 Webhook（TeXML を返す） |
| `POST` | `/telnyx/status/{id}` | Telnyx 用ステータスコールバック |
| `WS` | `/conversation-relay` | Conversation Relay 接続先 |

Telnyx 向けエンドポイントは APIキーではなく、**通話ごとのトークン**で保護しています
（任意で Ed25519 署名検証も有効化できます）。

### `status` の意味

| status | 意味 |
|---|---|
| `scheduled` | 予約済み・発信待ち |
| `dialing` | Telnyx に発信を依頼した |
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
| `TELNYX_LANGUAGE` | `ja-JP` | Conversation Relay の言語 |
| `TELNYX_TRANSCRIPTION_PROVIDER` | `deepgram` | 文字起こしの提供元 |
| `TELNYX_SPEECH_MODEL` | `nova-3` | 文字起こしモデル。**`flux` は英語専用なので日本語では使わない** |
| `TELNYX_TTS_PROVIDER` / `TELNYX_VOICE` | 空 | 読み上げ音声の指定（空なら Telnyx の既定） |
| `TELNYX_VALIDATE_SIGNATURE` | `false` | Ed25519 署名検証。有効にするなら `TELNYX_PUBLIC_KEY` も設定 |

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
| 発信失敗時の非リトライ | Telnyx エラー時はログと DB に記録して終了。次回起動時も再試行しない |
| 起動時の期限切れ処理 | サーバ停止中に過ぎた予約は `missed` にして発信しない |
| 会話ターン数の上限 | `MAX_TURNS`（既定 8）で打ち切り |
| 認証 | 管理 API は固定 APIキー、Telnyx Webhook は通話別トークン |

費用の内訳（最新は Telnyx / Anthropic の料金ページを確認してください）:

- Telnyx の US Local 番号: 月額無料のものが多い
- 日本の携帯への発信: 分単位で課金（ここが主なコスト）
- Conversation Relay の STT / TTS: 別途課金
- Claude Haiku 4.5: 1 通話の判定で $0.001 未満

**Telnyx コンソールで使用量アラートを設定しておくことを強くおすすめします。**

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
| 発信が国際発信で拒否される | **Voice → Outbound Voice Profiles** で Japan が許可されているか確認 |
| `HTTP 401` / `403` が返る | `TELNYX_API_KEY` が正しいか、期限切れでないか確認 |
| `HTTP 404` が返る | `TELNYX_ACCOUNT_SID` を設定しているなら値が誤っている可能性。**空にすれば別方式で発信します** |
| 残高不足で発信できない | Billing で入金（残高 $0 だと発信できません） |
| `HTTP 422` が返る | `TELNYX_APPLICATION_SID` が TeXML Application の ID か、`TELNYX_PHONE_NUMBER` が購入済みの E.164 番号か確認 |
| 電話は鳴るが無音／すぐ切れる | `APP_BASE_URL` が現在の ngrok URL と一致しているか確認。ngrok は再起動で URL が変わります |
| AI が話さない／会話にならない | ngrok の Web インスペクタ（http://127.0.0.1:4040）で `/telnyx/voice/...` のレスポンスを確認。`wss://` の URL が正しいか見る |
| 日本語が聞き取られない | `TELNYX_SPEECH_MODEL` が `flux` になっていないか確認（英語専用）。`nova-3` を使う |
| 冒頭に英語のアナウンスが入る | **トライアルアカウントの仕様**です（"This is an automated call generated on the Telnyx platform..."）。消すにはアカウント Level 2 が必要 |
| 発信が `D60` / code 10010 で 403 | 発信先が **Verified Numbers** に未登録。Telnyx コンソールで自分の番号を認証してください |
| **メッセージ全体が英語の音声で読まれる** | `TELNYX_VOICE` が空。`<Say>` は `voice` 未指定だと英語になります（`language` は `voice="alice"` のときしか効きません）。`AWS.Polly.Tomoko-Neural` などを設定 |
| 日本語の発音を変えたい | `TELNYX_VOICE` を別の ja-JP 音声に変更（`AWS.Polly.Takumi-Neural`／`Azure.ja-JP-NanamiNeural` など） |
| 起床判定がおかしい | `ANTHROPIC_MODEL=claude-sonnet-5` に変更して再起動 |
| 予約が消えた | `morning_call.db` が消えていないか確認。デプロイ先では永続ディスクが必要です |
| サーバ再起動後に予約が動かない | 予定時刻を過ぎたものは `missed` になります（仕様）。未来の予約は自動で復元されます |

### ログの見方

サーバのログに、発信・判定・再架電がすべて出力されます。

```
INFO  backend.scheduler: 発信ジョブを登録しました call_id=1 run_date=2026-08-20T22:00:00+00:00
INFO  backend.telnyx_client: 発信しました call_id=1 sid=xxxxxxxx
INFO  backend.conversation: ConversationRelay 接続 call_id=1
INFO  backend.conversation: 判定 call_id=1 decision=snooze end_call=True
INFO  backend.conversation: 再架電を予約しました call_id=1 -> 2
```

---

## 今後の拡張案（今回のスコープ外）

- モバイルアプリ化（Flutter 等、macOS 非依存の技術）
- マルチユーザー対応
- 定期予約（毎朝 7 時など）
- 天気やニュースを読み上げる
