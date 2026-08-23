# AI-morning-call

指定した時刻に AI が電話をかけて起こしてくれる個人向けモーニングコール PoC。電話に出て返答すると AI が起床判定し、眠そうなら自動で再架電します。

## 技術スタック

| 層 | 技術 |
|---|---|
| **バックエンド** | Python 3.11+ · FastAPI · SQLAlchemy |
| **フロントエンド** | HTML/CSS/JavaScript (vanilla) |
| **DB** | SQLite 3 |
| **スケジューラ** | APScheduler |
| **電話基盤** | Telnyx Programmable Voice (TeXML) / Conversation Relay |
| **AI** | Anthropic Claude API (既定: claude-haiku-4-5) |

**キー依存**:
- `fastapi` / `uvicorn` — REST API サーバ
- `sqlalchemy` — ORM
- `apscheduler` — 時刻ベースジョブスケジューラ
- `httpx` — Telnyx REST API 呼び出し（専用 SDK は使わない）
- `anthropic` — Claude API クライアント
- `pydantic` — リクエスト検証
- `python-dotenv` — 環境変数読み込み
- `PyNaCl` — Telnyx Webhook の Ed25519 署名検証（既定は無効なので任意）

## ディレクトリ構成

```
backend/
  ├─ main.py            # FastAPI app · ルーティング定義
  ├─ config.py          # 環境変数の読み込み・設定値
  ├─ db.py              # SQLite セッション管理
  ├─ models.py          # SQLAlchemy モデル (Call, ConversationLog, Setting)
  ├─ schemas.py         # Pydantic リクエストスキーマ
  ├─ auth.py            # APIキー認証 · Telnyx Ed25519 署名検証
  ├─ scheduler.py       # APScheduler · 発信ジョブ · 再架電ロジック
  ├─ telnyx_client.py   # Telnyx TeXML API 呼び出し · TeXML 生成
  ├─ conversation.py    # Conversation Relay WebSocket ハンドラ
  ├─ llm_client.py      # Claude API 呼び出し
  └─ requirements.txt

frontend/
  ├─ index.html         # チャット UI
  ├─ app.js
  └─ style.css

.env.example           # 環境変数テンプレート
README.md              # 詳細なセットアップ・トラブルシューティング
```

## 起動・開発コマンド

### 初期セットアップ

```bash
# Python 仮想環境の作成
py -m venv .venv

# 依存関係のインストール
.venv/Scripts/python.exe -m pip install -r backend/requirements.txt

# .env ファイルの設定（.env.example をコピーして値を埋める）
cp .env.example .env

# API キーの生成
.venv/Scripts/python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
```

### ローカル開発 (ターミナル 2 個必要)

**ターミナル 1 — ngrok トンネル**
```bash
ngrok http 8000
# 表示された https://xxxx-xx.ngrok-free.app を .env の APP_BASE_URL に設定
```

**ターミナル 2 — FastAPI サーバ**
```bash
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

> PowerShell では相対パスの実行ファイルに `.\` が必要（`.venv/...` だと CommandNotFoundException）。

サーバ起動確認：
```bash
curl http://localhost:8000/health
# "missing_env": [] なら OK
```

UI: http://localhost:8000/  
API ドキュメント: http://localhost:8000/docs

### 動作確認（API 呼び出し）

```bash
# API キーを環境変数に設定
$env:API_KEY = (Select-String -Path .env -Pattern '^API_KEY=' | % { $_.Line -split '=' | select -Last 1 })

# v1: 発信を予約
curl -X POST http://localhost:8000/calls/schedule `
  -H "Content-Type: application/json" `
  -H "X-API-Key: $env:API_KEY" `
  -d '{"phone_number":"+819012345678","call_at":"2026-08-15T07:00:00+09:00","use_conversation":false}'

# 予約一覧
curl -H "X-API-Key: $env:API_KEY" http://localhost:8000/calls

# 詳細・会話ログ確認
curl -H "X-API-Key: $env:API_KEY" http://localhost:8000/calls/1
```

## 実装進捗

| version | 機能 | 状態 |
|---|---|---|
| v0 | Telnyx から携帯への発信確認 | 手順のみ |
| v1 | 指定時刻に自動発信（固定メッセージ読み上げ） | ✅ **実機確認済み** |
| v2 | AI と会話して起床判定 → 再架電 | ✅ **実機確認済み** |
| v3 | チャット UI から自然文で予約 | ✅ **実機確認済み** |

Telnyx 移行後の実機確認 (2026-08-20) — v0〜v3 すべて動作:
- v1: 日本語で固定メッセージを読み上げ、`status=completed`
- v2: 「起きた」→ Deepgram nova-3 が認識 → Claude が `awake` 判定 → 終話・再架電なし
- v3: チャットから予約 → Claude がメッセージを抽出（例「起きてください」）→ 発信・会話まで成功

日本語 STT の癖: 「起きました」が「開きました」と誤認識されることがあるが、
Claude が `unclear` を返して聞き直すループで正しく復帰する（実機で確認済み）。

Git コミット:
- `b558f4e` — Initial commit
- `61410cb` — context reset前の情報を保存

### 電話基盤の移行経緯（重要）

当初は Twilio 実装だったが、**Twilio の本人確認 (Compliance Profile) が通らず番号を購入できなかった**ため
Telnyx へ移行した。Telnyx の Conversation Relay は Twilio の ConversationRelay と
**verb 名も WebSocket メッセージ形式もほぼ同一**なので、`conversation.py` はほぼ無改修で流用している。

- 乗り換え候補として SignalWire も検討したが、**SignalWire には ConversationRelay 相当の機能がない**
  （AI エージェントは SignalWire 側の LLM が動く方式で、Claude を使うには生音声を受けて
  STT/TTS を自前実装する必要がある）ため不採用。
- Twilio に戻す場合は git 履歴の `twilio_client.py` を参照すること。

## 開発時の必須ルール

### 1. `.env` をコミットしない
`.gitignore` に既に設定済み。ローカル設定値を含むため絶対にコミットしないこと。

### 2. 発信失敗時に無限リトライしない設計
- `scheduler.py` の `_fire()` 内で呼び出し失敗は `CallStatus.FAILED` で終了
- 自動リトライなし · DB に エラーメッセージを記録
- 手動で再試行する場合のみ API 経由

### 3. スケジューラは Firebase ではなく APScheduler を使用
- 常時起動サーバで メモリ内スケジューラを運用
- 起動時に DB から「未来の予約」のみ復元
- 過去の予約は `MISSED` にして発信しない（課金暴走防止）
- 複数ワーカー対応不要（シングルプロセス運用）

### 4. 個人利用 PoC のため過剰な認証機構は追加しない
- 管理 API: 固定 APIキー (`X-API-Key` ヘッダ) **または** ログインで発行される
  セッション Cookie。共有シークレットは `API_KEY` 1 つだけ
- セッション Cookie は `API_KEY` を鍵にした HMAC 署名文字列（サーバ側は状態を持たない）。
  `API_KEY` を変更すれば発行済み Cookie は全て無効になる
- `HttpOnly` + `SameSite=Lax`。`Secure` は**リクエストのスキーム**で判定する
  （`APP_BASE_URL` で判定すると ngrok の https を設定したまま localhost 開発したときに
  Cookie が返らなくなる）
- Telnyx Webhook: 通話ごとのトークン検証（これ以上の強化は不要）
- Ed25519 署名検証は任意。TeXML Webhook に署名が付くか未確認のため既定は無効
  (`TELNYX_VALIDATE_SIGNATURE=false`)
- OAuth / ユーザー認証は実装しない

### 5. その他重要な仕様
- 日時は全て **UTC naive datetime** で DB に保存
- Conversation Relay は JavaScript ではなく Python WebSocket ハンドラで実装
- 無音判定は `SILENCE_TIMEOUT_SECONDS × MAX_SILENCE_STRIKES` で動作
- 再架電の上限は `MAX_RETRIES` 環境変数でコントロール（既定 3 回）
- **日本語の文字起こしは Deepgram の `nova-3` を使う**。`flux` は英語専用なので指定しないこと
- **`<Say>` は `voice` を指定しないと英語音声で読まれる**。`language` 属性が効くのは
  `voice="alice"` のときだけで、既定の `man`/`woman` は常に en-US。
  `TELNYX_VOICE` に ja-JP の音声 ID（既定 `AWS.Polly.Tomoko-Neural`）を必ず入れること。
  音声一覧: `GET https://api.telnyx.com/v2/text-to-speech/voices`（3900 件超・要フィルタ）
- **`speechModel` は `<ConversationRelay>` のトップレベル属性ではない**。
  `<Language code="..." speechModel="..."/>` 子要素の中に置く。
  トップレベルに書くと設定が効かず **音声がまったく鳴らない**（挨拶すら聞こえない）。
- **`<Say>` と ConversationRelay は音声の系統が別**。`<Say>` は `AWS.Polly.*` で鳴るが、
  ConversationRelay は Telnyx ホスト音声（`Telnyx.*`、`hosted: true`）が確実。
  そのため `TELNYX_VOICE` と `TELNYX_RELAY_VOICE` を分けている。

### 6. Telnyx トライアルアカウントの制限（仕様であり不具合ではない）
- **通話の冒頭に英語アナウンスが自動挿入される**
  （"This is an automated call generated on the Telnyx platform..."）。
  消すにはアカウント Level 2（会社情報 + Telnyx 審査）が必要。個人利用なら放置でよい
- 発信先は **Verified Numbers に登録済みの番号のみ**（未登録だと `D60` / code 10010 で 403）
- 同時発信 2 本まで / 1 通話最大 10 分

### 7. パフォーマンス・安全装置
- 1 通話の会話ターン上限: `MAX_TURNS` (既定 8)
- 発信後の最大待機: `misfire_grace_time=300` (5 分)
- Telnyx: 日本への国際発信は Outbound Voice Profile で許可が必要

## 必須環境変数

`.env` に以下を設定:

```ini
TELNYX_API_KEY=             # Mission Control Portal → API Keys
TELNYX_APPLICATION_SID=     # Voice → TeXML Applications で作成した ID
TELNYX_ACCOUNT_SID=         # 任意（後述）
TELNYX_PHONE_NUMBER=        # 購入した E.164 番号 (+1XXXXXXXXXX など)
ANTHROPIC_API_KEY=          # Claude API キー (sk-ant-...)
APP_BASE_URL=               # ngrok URL または本番 HTTPS URL
API_KEY=                    # 管理 API キー
TIMEZONE=Asia/Tokyo         # タイムゾーン
```

Telnyx コンソール側の必須設定:
- **TeXML Application** を作成し、購入した番号に割り当てる
- **Outbound Voice Profile** で **Japan** への発信を許可する（未許可だと発信エラー）
- **残高が $0 だと発信できない**ので Billing で入金しておく

### 発信 API のエンドポイント（ハマりどころ）

Telnyx の TeXML 発信 API は 2 系統あり、ボディは同じ:

| パス | account_sid | 備考 |
|---|---|---|
| `POST /v2/texml/Accounts/{account_sid}/Calls` | 必要 | 新しい形式。`ApplicationSid` をボディに入れる |
| `POST /v2/texml/calls/{application_id}` | 不要 | 旧形式だが有効 |

**Account ID は Mission Control Portal に表示されないことがある**ため、
`telnyx_client._calls_request()` は `TELNYX_ACCOUNT_SID` が空なら自動で後者を使う。
`TELNYX_ACCOUNT_SID` は必須環境変数に含めないこと。

詳細は `.env.example` を参照。

## 本番デプロイ時の注意

- **常時起動が必須** (スリープすると予定時刻にジョブが動きません)
- SQLite の永続ディスクマウントが必須 (コンテナ再起動で予約が消えます)
- `APP_BASE_URL` を本番 HTTPS URL に更新
- ワーカー数は **1 プロセス** のまま (複数ワーカーで重複発信のリスク)
- Telnyx コンソールで使用量アラート設定推奨

起動コマンド例:
```bash
uvicorn backend.main:app --host 0.0.0.0 --port $PORT
```

詳細は README.md の「本番デプロイ」セクションを参照。
