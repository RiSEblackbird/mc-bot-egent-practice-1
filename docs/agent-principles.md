# Agent Principles

この文書は、このリポジトリで設計・実装を判断するためのheuristicをまとめます。実行順序、安全境界、完了条件はルート [`AGENTS.md`](../AGENTS.md)、エージェントルールの配置は [`docs/agent-harness.md`](agent-harness.md) を優先します。

## Hard gateとheuristic

この文書の原則は、原則としてheuristicです。複数の原則が競合する場合は、今回の要件、既存構造、変更容易性、誤用リスク、検証可能性を比較して決めます。

次はhard gateとして扱います。

- secret、個人情報、認証情報を公開しない。
- データ整合性と公開契約を壊さない。
- Python agent、Node bot、Bridge pluginの公開契約を片側だけで変更しない。
- 未実施の検証や未確認の実環境状態を事実として報告しない。
- P0、必須CI失敗、未解決の重大指摘を隠して完了扱いにしない。
- 無関係な差分、world、user dataを破壊しない。

数値目安を超えたことだけでFailにせず、読み手や変更者が安全に扱えるかで判断します。

## 長期タスク

- 最初に目標、完了条件、非対象、依存関係、検証方法を明らかにする。
- boundedな依頼は、真のblockerがない限り調査、実装、検証、配送まで同じ作業で完遂する。
- 途中経過を残す場合は、完了事項、未完了事項、次の最短アクション、検証、残るリスクを機械的に追える形にする。
- 再実行される副作用にはidempotency、checkpoint、deduplicationの必要性を検討する。
- 作業をsliceへ分けても、ユーザーが求めた最終成果を未完のまま通常終了しない。

## 長時間処理とMinecraft state

- Responses API、LangGraph、WebSocket、SSEの長時間処理は、cancel、timeout、resume、polling、cleanupの境界を明確にする。
- checkpointはcode内の進行だけでなく、外部side effectと整合する位置へ置く。
- `recovery_hints`、backlog、structured event、job stateは、再開者が推測せず状態を復元できる粒度で記録する。
- Minecraft worldは停止中に変化し得る。再開直後はinventory、位置、周辺block、保護領域、接続状態を再観測し、checkpointとの差を確認してから再計画する。
- retryやfallbackで重複command、二重採掘、二重配置、接続leak、重複通知を起こさないようidempotencyを検討する。

## ドキュメント

- 実装、挙動、setup、architecture、運用の意味が変わったら、対応する正本を同じ変更内で更新する。
- READMEは入口、docsは詳細仕様、nested `AGENTS.md`はpath固有契約、plansは進行中taskとして責務を分ける。
- 同じ長文を複数文書へcopyせず、正本と要約linkを分ける。
- 作業メモや将来予定を恒久文書へ混ぜず、現時点の契約、制約、手順を書く。
- 公開物では [`security-publication` Skill](../.agents/skills/security-publication/SKILL.md) を適用する。

## KISSとYAGNI

- 要件を満たす最小の構造から始め、使われない拡張点や設定を先行追加しない。
- nest、間接参照、dynamic import、metaprogrammingは、単純な構造より明確な利点がある場合に使う。
- 将来可能性だけを理由にinterface、factory、plugin、汎用DSLを増やさない。
- セキュリティ、可観測性、error処理、データ整合性に必要な備えは、利用前でも追加できる。

## DRYと共通化

- 重複回数だけで抽象化を強制しない。
- 変更理由、lifecycle、契約が同じ重複は共通化を検討する。
- 偶然似ている処理や、今後別方向に変わる可能性が高い処理は分けて保つ。
- 共通化によって呼び出し側の意図が隠れる場合は、明示的な重複を許容する。
- 定数、schema、validation、test dataは、複数箇所の不整合を防げる単位へ集約する。

## SRP、SoC、依存方向

- file、class、function、componentの責務を、名前とpublic APIから説明できる状態にする。
- dashboard / Presentation、Application、Domain、Infrastructureの関心を区別し、外部技術の詳細をdomain判断へ漏らさない。
- logging、metrics、retry、authorizationなどの横断的関心は、一貫して適用できる境界へ置く。
- component内の取得、状態、描画、業務判断を分ける価値と、分割による追跡costを比較する。
- 分割は行数ではなく、独立して変更・検証できる責務の境界で判断する。

## OCPと外部統合

- 種別追加のたびに広範囲の条件分岐を変更する構造では、strategy、registry、polymorphismを検討する。
- OpenAI、Mineflayer、Paper plugin、WorldGuard、CoreProtectなどの抽象化は、差し替え、契約test、障害分離に実益がある境界へ置く。
- 一つの実装しかなく、差し替え需要もない場合は、interface追加を目的化しない。
- 既存の公開契約を変える場合は、compatibility、migration、versioning、rollbackを検討する。

## POLAと可読性

- 同種のAPI、非同期処理、error、validation messageは一貫した契約にする。
- side effectや破壊的操作は名前、引数、戻り値から予測できるようにする。
- 略語や内部用語は一般性があるものに限り、利用者向けUIへ実装用語を出さない。
- commentはcodeから分からない理由、制約、契約を補い、処理内容の逐語説明を避ける。
- 新規参加者が安全な変更箇所と検証方法を判断できる情報を残す。

## エラー処理と可観測性

- errorを消すこと自体を目的にせず、根本原因と利用者影響を特定する。
- 想定可能な失敗は、retry、fallback、停止、user通知の方針を明示する。
- fallbackでdata不整合や設定不備を隠さない。
- logは構造化し、level、event、必要最小限のcontextを持たせる。
- 主要use caseでは、成功率、latency、retry、failure categoryを観測できる設計を検討する。
- Python agent、Node bot、Bridge pluginをまたぐfailureでは、相関に必要な最小限の識別情報を保ち、公開物へ実識別子を出さない。

## テスト

- Unit Testを判断logic、Integration Testを境界契約、E2Eをcritical導線へ使う。
- 時刻、乱数、network、外部API、port、world stateなどの非決定要素を制御する。
- UI testはrole、label、visible text、状態変化を優先し、CSS classや偶然のDOM構造へ結合しない。
- API、WebSocket、SSEのrequest、response、status、error形式が変わる場合はcontract testで固定する。
- bug修正では、修正前に失敗する条件と期待結果を回帰testへ残す。
- coverage値は未検査領域を探すsignalとして使い、数字だけを目的化しない。
- flaky testは再実行で隠さず、待機条件、競合、非同期、環境差の原因を直す。

## ファイルと依存

- fileが大きくなった場合は、責務、変更理由、test境界に沿った分割を検討する。
- 依存は最小限に保ち、標準機能や既存依存で十分な場合は追加しない。
- lock fileを依存変更へ追従させ、生成物、環境依存file、credentialを追跡しない。
- 設定は環境ごとの差を明示し、secret managementへ分離する。
