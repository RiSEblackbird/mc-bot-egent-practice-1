# Minecraft 自律ボット（Python + gpt-5-mini + Mineflayer）

本プロジェクトは、Minecraft Java Edition 1.21.1 + Paper 上で動作する **日本語対応の LLM 自律ボット** です。
Python 側が LLM（OpenAI **gpt-5-mini**）でチャット意図を解釈し、Node.js 側の Mineflayer ボットへ行動コマンドを送ります。

## 1. できること（初期）
- 農業：畑の整備/収穫/再植付け、パン作成
- 自動採掘：鉄/石炭/ダイヤのブランチマイニング等
- 探索：プレイヤー基準の周辺探索
- クラフト支援：素材収集→作業台でクラフト→受け渡し
- 自己防衛戦闘：敵対Mobの回避/迎撃
- 簡易建築：小屋/倉庫などの原始的建築
- プレイヤー随伴：「ついてきて」で追尾モード

## バージョン方針

本プロジェクトは既定で **Minecraft Java / Paper 1.21.1** を使用します。Paper サーバーの JAR は `paper-1.21.1-*.jar` を取得して `paper.jar` にリネームし、`C:\mc\paper\paper.jar` など任意の配置先で保守してください。Bot 側の既定プロトコルも `MC_VERSION=1.21.1` を参照するため、サーバーとクライアントの齟齬を防ぎます。将来的に別バージョンを試す場合は `.env` の `MC_VERSION` や `paper.jar` を差し替えるだけで移行できます。

### クライアントとの互換性

原則として Minecraft クライアントも 1.21.1 を利用してください。1.21.1 以外のクライアントから接続する必要がある場合は、Paper サーバーに ViaVersion / ViaBackwards などの互換プラグインを導入して調整します（完全互換ではない点に留意）。

## 2. Paper サーバーの起動（前提）
Windows 例：
```powershell
cd C:\mc\paper
java -Xms4G -Xmx4G -jar .\paper.jar --nogui
```

```powershell
cd C:\mc\paper-1.21.1
java -Xms4G -Xmx4G -jar .\paper-1.21.1.jar --nogui
```

開発中は `server.properties` の `online-mode=false` を推奨。

## 3. セットアップ

### 3.1 Node（Mineflayer ボット）

Node 側のボット実装は TypeScript 化しており、`npm start` を実行すると自動的にビルドと起動を行います。
なお Mineflayer v4.33 系は Node.js 22 以降を要求するため、開発環境の Node バージョンが古い場合は `nvm` などでのアップデートを強く推奨します。
本リポジトリ直下の `.nvmrc` は 22 系を指しているので、`nvm use` でバージョンを切り替えられます。

```bash
cd node-bot
npm install
npm start
# TypeScript ソースを確認したい場合は npm run build で dist/bot.js を生成
# ユニットテストは vitest で実行可能
npm test
```

### 3.2 Python（LLM エージェント）

```bash
cd python
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r ../requirements.txt
cp ../env.example ../.env
# .env を編集（OpenAIキーや接続設定）
python agent.py
```

Python エージェントは `AGENT_WS_HOST` / `AGENT_WS_PORT` で指定したポートに WebSocket サーバーを公開します。
Mineflayer 側（Node.js）がチャットを受信すると、自動的にこのサーバーへ `type=chat` の JSON を送信し、
Python 側で LLM プランニングとアクション実行が行われます。`DEFAULT_MOVE_TARGET` を変更すると、
「移動」系のステップで座標が指定されなかった場合のフォールバック座標を調整できます。

### 3.3 Docker Compose（Python + Node 同時ホットリロード）

開発時に Python エージェントと Node ボットの両方をホットリロードで動かしたい場合は、プロジェクトルートに追加した `docker-compose.yml` を利用できます。

```bash
cp env.example .env  # まだ .env が無い場合
docker compose up --build
```

* Node サービスは `npm run dev`（`tsx` を利用）で TypeScript ソースの変更を検知し、自動的に再起動します。
* Python サービスは `watchfiles` を用いて `.py` ファイルの変更を検知し、`agent.py` を再実行します。なお CLI の仕様上、
  `watchfiles -- ...` に渡すコマンドは `"python agent.py"` のように 1 引数へクォートしておかないと、Python が
  対話モードで起動してポートをリッスンしない（Node からの接続が `ECONNREFUSED` になる）点に注意してください。
* ホットリロード環境では依存ライブラリをコンテナ起動時に自動インストールするため、初回起動時は少し時間がかかります。
* Docker Compose は `host.docker.internal` をコンテナの hosts に追加しています。Windows / WSL / macOS から Paper サーバーを起動している場合でも、ボットがホスト OS 上の `25565` ポートへ接続できます。
* Node.js サービス用コンテナは `node:22` を採用し、最新の Mineflayer 系ライブラリが要求するエンジン条件を満たして `minecraft-protocol` の PartialReadError（`entity_equipment` の VarInt 解析失敗）を防止します。
* Python エージェントが `AGENT_WS_PORT` をリッスンし、Node 側が `AGENT_WS_URL` で指定した経路からチャットを転送します。Docker Compose では既定で `ws://python-agent:9000` に接続します。

#### 3.3.1 1.21.x の PartialReadError 追加対策

- Paper / Vanilla 1.21.4 以降では ItemStack (Slot 型) に optional NBT が 2 セクション追加され、旧定義のままでは `entity_equipment` パケットで 2 バイトの読み残しが発生します。
- `node-bot/runtime/slotPatch.ts` で `customPackets` 用の Slot 定義を動的に生成し、1.21 ～ 1.21.x 系の亜種をまとめて上書きすることで `PartialReadError: Unexpected buffer end while reading VarInt` を解消しています。minecraft-data のバージョン一覧から自動検出しているため、新しい 1.21.x がリリースされても追従漏れを起こしません。
- 1.21.3 以前ではこれらのフィールドが送られないため、option タイプの 0 バイトだけが届き互換性が維持されます。
- `.env` で `MC_VERSION=1.21.1` のように **minecraft-data が認識するプロトコルラベル** を指定すると、Mineflayer がサーバーと同じ定義で通信を開始するため、`PartialReadError` の再発リスクを減らせます。未設定時は既定で 1.21.1 を採用し、未知の値が入力された場合は対応可能なバージョンへ自動フォールバックします。

## 4. .env

`env.example` を `.env` にコピーし、中身を設定します（Python側で読み込み）。

* `OPENAI_API_KEY`: OpenAI の API キー
* `OPENAI_BASE_URL`（任意。例: `https://api.openai.com/v1` のようにスキーム付きで指定。スキームを省いた場合は `http://` が自動補完され、実行時ログへ警告が出力されます）
* `OPENAI_MODEL`: 既定 `gpt-5-mini`
* `OPENAI_TEMPERATURE`: 0.0～2.0 の範囲で温度を調整。**温度固定モデル（例: gpt-5-mini）では無視され、ログに警告が出力されます。**
* `WS_URL`: Python→Node の WebSocket（既定 `ws://node-bot:8765`。Docker Compose ではサービス名解決で疎通）
* `WS_HOST` / `WS_PORT`: Node 側 WebSocket サーバーのバインド先（既定 `0.0.0.0:8765`）
* `AGENT_WS_HOST` / `AGENT_WS_PORT`: Python エージェントが Node からのチャットを受け付ける WebSocket サーバーのバインド先（既定 `0.0.0.0:9000`）
* `AGENT_WS_URL`: Node 側が Python へ接続するための URL。Docker Compose では `ws://python-agent:9000` を既定とし、ローカル実行時は `ws://127.0.0.1:9000` などへ変更します。
* `DEFAULT_MOVE_TARGET`: LLM プラン内で座標が省略された移動ステップ用のフォールバック座標（例 `0,64,0`）
* `MC_HOST` / `MC_PORT`: Paper サーバー（既定 `localhost:25565`、Docker 実行時は自動で `host.docker.internal` へフォールバック）
* `MC_VERSION`: Mineflayer が利用する Minecraft プロトコルのバージョン。Paper 1.21.1 を想定した既定値 `1.21.1` を含め、minecraft-data が対応するラベルを指定してください。
* `MC_RECONNECT_DELAY_MS`: 接続失敗時に Mineflayer ボットが再接続を試みるまでの待機時間（ミリ秒、既定 `5000`）
* `BOT_USERNAME`: ボットの表示名（例 `HelperBot`）
* `AUTH_MODE`: `offline`（開発時推奨）/ `microsoft`

## 5. 使い方（ゲーム内）

プレイヤーがチャットで日本語の自然文を送ります。例：

* 「パンが無い」 → 小麦収穫→パン作成→手渡し or チェスト格納
* 「鉄が足りない」 → ツール確認→採掘計画→ブランチマイニング
* 「ついてきて」 → 追尾モード
* 「ここに小屋を建てて」 → 建材確認→不足なら収集→建築

ボットは進捗を日本語で逐次報告します。

### 5.1 デバッグログでチャット処理を追跡する

Paper サーバーでチャットを送信した際に「何も起こらない」ケースでも、Docker/コンソール上のログを確認すれば、
次の観点でフローを把握できます。

1. **Node（Mineflayer）**: `node-bot` の標準出力に `[Chat] <player> メッセージ` が記録され、
   直後に `[ChatBridge] ...` ログが続き、Python エージェントへチャットを転送した結果を確認できます。
   WebSocket 経由のコマンド受信時は `id=...` 付きの詳細ログが表示され、どの負荷元からどのコマンドが届き、
   どのレスポンスを返したか追跡できます。
2. **Python エージェント**: `python-agent-1` のログに `WS send/recv`、`queue chat`、`moveTo` などの INFO ログが出力され、
   LLM からの計画生成と Mineflayer への指示送出の成否を即座に確認できます。2025 年 10 月時点では、
   `command[001]` のようなコマンド ID 付きでチャット/移動コマンドの送信・応答・処理時間が逐次表示されるほか、
   プランニング結果の各ステップ（`plan_step index=1/3 ...`）がログへ詳細記録されるため、
   「どのステップをどの条件で実行／スキップしたか」を追跡可能です。Mineflayer 側のログと突き合わせれば、
   問題発生時の原因を秒単位で切り分けられます。

これらのログを突き合わせることで、「チャットが受信されたか」「Python 側がコマンドを送ったか」「Mineflayer が応答したか」を
時系列で把握でき、問題の切り分けが容易になります。

### 5.2 OpenAI 設定で温度を変更したい場合

一部の OpenAI モデル（特に gpt-5-mini 系）は API 側で温度が固定されており、`temperature` パラメータを送信するとリクエストが拒否されます。
本リポジトリでは `OPENAI_TEMPERATURE` を設定しても、そのモデルが温度変更不可であれば自動的に送信を抑止し、警告ログを出力します。

- **温度変更が許可されているモデルに切り替える**: `OPENAI_MODEL` を温度可変モデルに変更すると、`OPENAI_TEMPERATURE` の値が 0.0～2.0 の範囲で反映されます。
- **無効な温度指定をした場合**: 数値以外や範囲外の値を設定すると、INFO/ WARNING ログにフォールバックの旨が表示され、既定値 `0.3` が自動的に利用されます。
- **デバッグ方法**: `python/planner.py` のログに `OPENAI_TEMPERATURE` を無視した理由や、フォールバックが発生した詳細が記録されるため、再発防止に活用してください。

温度変更に失敗する場合は、まずログに出力される警告メッセージを確認し、`OPENAI_MODEL` と `OPENAI_TEMPERATURE` の組み合わせがサポート対象か見直してください。

## 6. 参考理論（READMEにURLを**必ず**記載）

本プロジェクトは以下の理論/手法を採用します。**各論文のURLを本節に列挙してください。**

* Voyager: [https://arxiv.org/abs/2305.16291](https://arxiv.org/abs/2305.16291)
* ReAct: [https://arxiv.org/abs/2210.03629](https://arxiv.org/abs/2210.03629)
* Reflexion: [https://arxiv.org/abs/2303.11366](https://arxiv.org/abs/2303.11366)
* VPT: [https://arxiv.org/abs/2206.04615](https://arxiv.org/abs/2206.04615)

* MineDojo: [https://arxiv.org/abs/2206.08853](https://arxiv.org/abs/2206.08853)

## 7. アーキテクチャ概要

```
[Player Chat (日本語)]
        │
        ▼
   Python(LLM) ──WS(JSON)──▶ Node(Mineflayer) ──▶ Paper Server
     ├─ planner.py（gpt-5-mini でタスク分解）
     ├─ actions.py（高レベル→低レベルコマンド）
     └─ memory.py（座標/在庫/履歴）
```

## 8. 注意

* 本 README/コードは**プレーンテキストのみ**で完結（PDF/Word 不要）。
* 実運用前に安全策（溶岩/落下/爆発回避）や失敗時のリカバリを拡充してください。

