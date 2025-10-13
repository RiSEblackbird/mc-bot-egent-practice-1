# Minecraft 自律ボット（Python + gpt-5-mini + Mineflayer）

本プロジェクトは、Minecraft Java Edition 1.21.8 + Paper 上で動作する **日本語対応の LLM 自律ボット** です。  
Python 側が LLM（OpenAI **gpt-5-mini**）でチャット意図を解釈し、Node.js 側の Mineflayer ボットへ行動コマンドを送ります。

## 1. できること（初期）
- 農業：畑の整備/収穫/再植付け、パン作成
- 自動採掘：鉄/石炭/ダイヤのブランチマイニング等
- 探索：プレイヤー基準の周辺探索
- クラフト支援：素材収集→作業台でクラフト→受け渡し
- 自己防衛戦闘：敵対Mobの回避/迎撃
- 簡易建築：小屋/倉庫などの原始的建築
- プレイヤー随伴：「ついてきて」で追尾モード

## 2. Paper サーバーの起動（前提）
Windows 例：
```powershell
cd C:\mc\paper
java -Xms4G -Xmx4G -jar paper-1.21.8-60.jar --nogui
```

開発中は `server.properties` の `online-mode=false` を推奨。

## 3. セットアップ

### 3.1 Node（Mineflayer ボット）

Node 側のボット実装は TypeScript 化しており、`npm start` を実行すると自動的にビルドと起動を行います。

```bash
cd node-bot
npm install
npm start
# TypeScript ソースを確認したい場合は npm run build で dist/bot.js を生成
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

### 3.3 Docker Compose（Python + Node 同時ホットリロード）

開発時に Python エージェントと Node ボットの両方をホットリロードで動かしたい場合は、プロジェクトルートに追加した `docker-compose.yml` を利用できます。

```bash
cp env.example .env  # まだ .env が無い場合
docker compose up --build
```

* Node サービスは `npm run dev`（`tsx` を利用）で TypeScript ソースの変更を検知し、自動的に再起動します。
* Python サービスは `watchfiles` を用いて `.py` ファイルの変更を検知し、`agent.py` を再実行します。
* ホットリロード環境では依存ライブラリをコンテナ起動時に自動インストールするため、初回起動時は少し時間がかかります。
* Docker Compose は `host.docker.internal` をコンテナの hosts に追加しています。Windows / WSL / macOS から Paper サーバーを起動している場合でも、ボットがホスト OS 上の `25565` ポートへ接続できます。

## 4. .env

`env.example` を `.env` にコピーし、中身を設定します（Python側で読み込み）。

* `OPENAI_API_KEY`: OpenAI の API キー
* `OPENAI_BASE_URL`（任意）
* `OPENAI_MODEL`: 既定 `gpt-5-mini`
* `WS_URL`: Python→Node の WebSocket（既定 `ws://127.0.0.1:8765`）
* `MC_HOST` / `MC_PORT`: Paper サーバー（既定 `localhost:25565`、Docker 実行時は自動で `host.docker.internal` へフォールバック）
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

