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
- 装備持ち替え：ツール名の指示に従って適切な手へ装備

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

Mineflayer 起動時の環境変数は `node-bot/runtime/config.ts` へ集約しており、Docker 実行時の `MC_HOST` 補正や `MC_VERSION` のフォールバック、`MOVE_GOAL_TOLERANCE` の上下限チェックを一括で行います。
設定変更のテストは `node-bot/tests/config.test.ts` を実行すると安全に回帰確認できます。

`bot.ts` には `gatherStatus` WebSocket コマンドを実装しており、Python エージェントが現在位置・インベントリ・体力/満腹度と掘削許可のスナップショットを即座に取得できます。Mineflayer の `canDig` 設定やゲームモードから地下採掘の可否を判定し、所持ツルハシのエンチャント情報と併せて JSON で返すため、チャット経由で逐一質問せずとも自律的な計画を立てられます。

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
「移動」系のステップで座標が指定されなかった場合のフォールバック座標を調整できます。なお Python エージェント
は、直前に検出した座標付きステップを記憶し、直後に続く「移動」「向かう」などの抽象ステップでは同じ目的地を
再利用するため、計画途中で座標の記述が省略されても同じ地点へ向かい続けます。
設定値の読み込みは `python/config.py` に統合しており、ポート番号やデフォルト座標のバリデーションを一括で処理します。
ユニットテスト `tests/test_agent_config.py` で挙動を確認できるため、環境変数を追加した場合も回帰チェックが容易です。
さらにチャット指示は **検出報告タスク**（座標・所持品などの報告指示）と **行動系タスク**（移動・採掘・建築など）に
分類され、Mineflayer で実行可能なアクションと未実装のタスクを丁寧に切り分けます。未対応カテゴリは Python 側で
待機リストとして整理し、「農作業」「建築作業」などのカテゴリ名を添えてプレイヤーに状況を説明するため、余計な
テンプレート文が差し込まれることなく自然な応答フローを維持できます。
建築カテゴリについては `docs/building_state_machine.md` に LangGraph ノードが従うべき
フェーズ定義・遷移条件・ロールバック指針を整理しており、長期ジョブを中断しても
安全に再開できるようチェックポイント設計の前提を共有しています。
2025 年 2 月時点では `python/agent_orchestrator.py` に LangGraph ベースのステートマシンを導入し、採掘・建築・防衛の
モジュールをノード単位で独立させました。これにより再計画時の分岐が視覚化され、`mine` → `equip` のような連鎖的な
処理もグラフ上で明示されます。同様に `python/planner.py` の LLM 呼び出しも LangGraph の条件分岐ノードに置き換え、
失敗時は優先度を `high` へ自動昇格、成功時は `normal` へ戻す優先度マネージャーと同期しています。新しいシナリオテスト
`tests/test_langgraph_scenarios.py` では障害検知・並列進行・優先度遷移を網羅し、グラフ内で再計画が完結することを検証できます。

Mineflayer から `ok=false` が返った場合は、障壁内容を `compose_barrier_notification` で LLM に共有し、
プレイヤーへチャット通知したのち自動的に再計画を依頼します。失敗ステップと残りの計画案をまとめて LLM に渡すため、
例えば「採掘が拒否された→ツール装備プランを立て直す」といった自律的なリカバリーが可能です。障壁発生時も既存の
検出レポートや backlog 通知が重複しないよう制御しており、チャットが過剰に騒がしくならないよう設計しています。

Python エージェントが呼び出す LLM は **OpenAI Responses API** を利用しています。従来の Chat Completions API では
`reasoning` パラメータが拒否されるため、Responses API の `reasoning.effort` と `text.verbosity` を併用し、
gpt-5 系モデルに対して安定した JSON 応答と推論強度の指定を両立させています。

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

### 3.4 AgentBridge HTTP プラグイン

`bridge-plugin/` ディレクトリに Paper 用の HTTP ブリッジプラグイン（AgentBridge）を追加しました。WorldGuard・CoreProtect と連携して継続採掘ジョブのリージョン管理やバルク環境評価を提供します。

1. `bridge-plugin/libs/` に CoreProtect の jar を配置します（`.gitkeep` のみコミット済み）。
2. Java 21 + Gradle を用意し、プラグイン直下で `./gradlew shadowJar` を実行すると `build/libs/AgentBridge-0.1.0.jar` が生成されます。
3. Paper サーバーの `plugins/` へ配置し、初回起動後に生成される `plugins/AgentBridge/config.yml` の `api_key` を `.env` の `BRIDGE_API_KEY` と一致させます。

HTTP サーバーは `config.yml` の `bind` / `port` で調整でき、`GET /v1/health` にアクセスすると WorldGuard/CoreProtect の有効状態を確認できます。`POST /v1/jobs/*` 系エンドポイントは必ず `X-API-Key` ヘッダーで保護してください。

### 3.5 継続採掘モード CLI

Python 側に `python/cli.py` を追加し、継続採掘ジョブを CLI から起動できるようにしました。

```bash
python -m python.cli tunnel --world world --anchor 100 12 200 --dir 1 0 0 --section 2x2 --len 64 --owner Taishi
```

`--dir` はカードinal方向ベクトル（例: `1 0 0`）を指定します。今後 `auto` 推定を実装予定のため、現時点では必須引数です。ジョブ開始後は AgentBridge 経由でバルク環境評価と CoreProtect チェックを行い、Mineflayer には `mineBlocks` / `placeTorch` コマンドを送信します。`.env` に追加した `BRIDGE_URL` などの変数で接続先やたいまつ間隔を調整できます。

## 4. .env

`env.example` を `.env` にコピーし、中身を設定します（Python側で読み込み）。

* `OPENAI_API_KEY`: OpenAI の API キー
* `OPENAI_BASE_URL`（任意。例: `https://api.openai.com/v1` のようにスキーム付きで指定。スキームを省いた場合は `http://` が自動補完され、実行時ログへ警告が出力されます）
* `OPENAI_MODEL`: 既定 `gpt-5-mini`
* `OPENAI_TEMPERATURE`: 0.0～2.0 の範囲で温度を調整。**温度固定モデル（例: gpt-5-mini）では無視され、ログに警告が出力されます。**
* `OPENAI_VERBOSITY`: gpt-5 系モデル専用の応答詳細度。`low` / `medium` / `high` のいずれかを設定します（未設定なら API 既定値を利用）。
* `OPENAI_REASONING_EFFORT`: gpt-5 系モデル専用の推論強度。`low` / `medium` / `high` のいずれかを指定し、空欄の場合は OpenAI 側の既定値に従います。
* `WS_URL`: Python→Node の WebSocket（既定 `ws://node-bot:8765`。Docker Compose ではサービス名解決で疎通）
* `WS_HOST` / `WS_PORT`: Node 側 WebSocket サーバーのバインド先（既定 `0.0.0.0:8765`）
* `AGENT_WS_HOST` / `AGENT_WS_PORT`: Python エージェントが Node からのチャットを受け付ける WebSocket サーバーのバインド先（既定 `0.0.0.0:9000`）
* `AGENT_WS_URL`: Node 側が Python へ接続するための URL。Docker Compose では `ws://python-agent:9000` を既定とし、ローカル実行時は `ws://127.0.0.1:9000` などへ変更します。スキームを省略すると `ws://` が自動補完され、ホストやポートが未設定・不正な値だった場合も Docker 環境に合わせて安全な既定へフォールバックします。
* `DEFAULT_MOVE_TARGET`: LLM プラン内で座標が省略された移動ステップ用のフォールバック座標（例 `0,64,0`）
* `MC_HOST` / `MC_PORT`: Paper サーバー（既定 `localhost:25565`、Docker 実行時は自動で `host.docker.internal` へフォールバック）
* `MC_VERSION`: Mineflayer が利用する Minecraft プロトコルのバージョン。Paper 1.21.1 を想定した既定値 `1.21.1` を含め、minecraft-data が対応するラベルを指定してください。
* `MC_RECONNECT_DELAY_MS`: 接続失敗時に Mineflayer ボットが再接続を試みるまでの待機時間（ミリ秒、既定 `5000`）
* `MOVE_GOAL_TOLERANCE`: moveTo コマンドで利用する GoalNear の許容距離（ブロック数）。既定値は `3` で、1～30 の範囲に丸められます。
* `BOT_USERNAME`: ボットの表示名（例 `HelperBot`）
* `AUTH_MODE`: `offline`（開発時推奨）/ `microsoft`

## 5. 使い方（ゲーム内）

プレイヤーがチャットで日本語の自然文を送ります。例：

* 「パンが無い」 → 小麦収穫→パン作成→手渡し or チェスト格納
* 「鉄が足りない」 → ツール確認→採掘計画→ブランチマイニング
* 「ついてきて」 → 追尾モード
* 「ここに小屋を建てて」 → 建材確認→不足なら収集→建築
* 「現在値教えて」 → Mineflayer が現在位置 (X/Y/Z) をチャットで即座に報告

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
   さらに未実装ステップや Mineflayer 側で拒否されたアクションを検知した場合は、ゲーム内チャットで
   「手順〇〇で問題が発生しました」と即時通知し、Python ログにも `execution barrier detected` 警告を
   出力するため、プレイヤーへのフィードバックと開発者の原因特定が容易になりました。障壁が同じ
   ステップで繰り返し発生しても毎回チャットとログへ通知され、どの操作が停滞しているかを逐次
   把握できます。また、LLM 計画から座標を抽出できず既定座標へ移動した場合も、「指示に座標が含まれ
   ていない」旨を即座に知らせるため、座標指定の不足にすぐ気付けます。加えて、障壁通知メッセージは
   直前のログ状況や記憶している座標情報を LLM へ送り、丁寧な日本語で確認事項をまとめた上でプレイヤー
   へ送信するため、チャット内容がより状況に即した文面になります。

これらのログを突き合わせることで、「チャットが受信されたか」「Python 側がコマンドを送ったか」「Mineflayer が応答したか」を
時系列で把握でき、問題の切り分けが容易になります。

### 5.2 OpenAI 設定で温度を変更したい場合

一部の OpenAI モデル（特に gpt-5-mini 系）は API 側で温度が固定されており、`temperature` パラメータを送信するとリクエストが拒否されます。
本リポジトリでは `OPENAI_TEMPERATURE` を設定しても、そのモデルが温度変更不可であれば自動的に送信を抑止し、警告ログを出力します。

### 5.3 レッドストーン採掘の自律行動

2025 年 10 月時点で、Python エージェントは「レッドストーンを集めて」のような採掘指示を受けると、
LLM 計画の中に含まれる「採掘」ステップを自動的に `mineOre` コマンドへ変換して Node 側の Mineflayer へ送ります。
Mineflayer は周囲 12 ブロック（指示文に「広範囲」等が含まれる場合は最大 18 ブロック）をスキャンし、
`redstone_ore` / `deepslate_redstone_ore` を優先的に探索します。見つかった場合は自動で接近→掘削を行い、
結果は Python ログとゲーム内チャットへ反映されます。採掘ログは `[MineOreCommand] mined ...` として出力されるため、
実際にどの座標でどの鉱石を回収したかを追跡可能です。採掘対象が見つからなかった場合や Mineflayer が移動に失敗した場合は、
`Target ore not found within scan radius` 等のエラーが返り、Python 側では障壁通知としてプレイヤーへ状況が共有されます。

- **温度変更が許可されているモデルに切り替える**: `OPENAI_MODEL` を温度可変モデルに変更すると、`OPENAI_TEMPERATURE` の値が 0.0～2.0 の範囲で反映されます。
- **無効な温度指定をした場合**: 数値以外や範囲外の値を設定すると、INFO/ WARNING ログにフォールバックの旨が表示され、既定値 `0.3` が自動的に利用されます。
- **デバッグ方法**: `python/planner.py` のログに `OPENAI_TEMPERATURE` を無視した理由や、フォールバックが発生した詳細が記録されるため、再発防止に活用してください。

温度変更に失敗する場合は、まずログに出力される警告メッセージを確認し、`OPENAI_MODEL` と `OPENAI_TEMPERATURE` の組み合わせがサポート対象か見直してください。

### 5.3 自動移動の許容範囲と空腹対策

2025 年 10 月時点の Mineflayer ではブロックへ完全一致しないと移動完了とみなされず、段差や水流の影響で「目的地付近に到達しているのに失敗扱い」になるケースが散見されました。本リポジトリでは `moveTo` コマンドを受け取った際に `GoalNear` を採用し、目的座標から ±3 ブロック以内（環境変数 `MOVE_GOAL_TOLERANCE` で 1～30 に調整可能）なら完了として扱います。これにより、プレイヤー指定の XYZ 座標へ到達したにもかかわらず障壁報告が発生する不具合を低減できます。

さらに、ボットが飢餓ダメージを受け始めたタイミング（満腹度 0 の状態）ではインベントリを自動確認し、食料を所持していればその場で装備・摂取してスタミナを回復します。食料が存在しない場合は「空腹ですが食料を所持していません。補給をお願いします。」とチャット通知するため、プレイヤーが補給すべきタイミングを把握しやすくなります。

### 5.4 座標移動指示への即応

- プレイヤーが「X=-36, Y=73, Z=-66」や「{XYZ: -36 / 73 / -66}」といった多様な記法で座標を提示しても、Python エージェント側で正規表現を拡充して自動抽出します。従来のように既定座標へ退避したり、同じ座標の再提示をお願いする頻度が大幅に減りました。
- 元チャットから抽出した座標をプラン実行開始時点で保持し、LLM の出力ステップに数値が含まれなくてもそのまま移動を開始します。これにより「今から向かってよいか？」といった許可確認を挟まず、指示を受け取った直後に移動コマンドを Mineflayer へ送出できます。
- 位置確認やインベントリ確認だけを行うメタ的なステップは自動でスキップし、移動や採取などの実行ステップを優先するため、チャットへの応答が冗長にならずテンポよく行動を開始できます。
- 段差や足場の処理を促す抽象的な文でも「移動継続」のヒントとして扱い、既知の座標へ向かう行動を止めないヒューリスティックを導入しました。Mineflayer の pathfinder が吸収できる範囲は確認待ちを挟まずに処理されるため、「具体的な段差処理方法を教えてほしい」といった追加質問が大幅に減ります。


### 5.5 検出報告タスクの整理

- 現在位置や所持品など、アクションを伴わずに状況を報告するだけの指示は Python エージェント内で「検出報告タスク」として切り分けました。これにより、進捗報告テンプレートが誤って挿入されるケースを防ぎ、質問→回答という自然なチャットフローを維持します。
- LLM 応答が既に丁寧な返答を生成している場合は追加メッセージを抑制し、重複応答を避けながら Node 側の即時レポートと矛盾しないようにしています。検出報告タスクの内容は `Memory` に記録され、後続ステップで同じ確認を繰り返さないためのヒントとして活用されます。
- `gatherStatus` コマンド経由で現在位置・所持品・体力/満腹度と掘削許可を自動取得し、要約文をそのままプレイヤーへ共有します。これにより「座標を教えて」「ツルハシを持っているか」などの再質問が不要になり、採掘指示を受け取った直後に準備状況を報告した上で作業へ移行できます。


### 5.6 Paper サーバーの警告 (`HelperBot moved wrongly!` / `minecraft:movement_speed`) への方針

- Mineflayer の移動制御ではパルクールやダッシュをあえて有効化し、危険地帯での生存性と高機動を優先します。`HelperBot moved wrongly!` 警告が発生する場合がありますが、アクロバットなルート選択を阻害しないことを重視した意図的な設定です。サーバーが強制的に位置補正を行った場合でも、直近の目的地を再セットして移動を継続します。
- OpenAI からの移動命令が継続している際に `forcedMove` が発生した場合は、1 秒以内の連続補正を無視しつつログへ警告を残します。これによりプレイヤーはサーバーが移動を補正した事実を把握できます。
- 1.21.1 以降で属性名が `minecraft:generic.movement_speed` へ統一されたため、Mineflayer が旧名 `minecraft:movement_speed` を送出しても自動で置き換え、Paper 側の `Ignoring unknown attribute` 警告を防ぎます。
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

