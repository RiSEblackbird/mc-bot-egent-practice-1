---
name: production-investigation
description: "Minecraft server、Paper plugin、Node bot、Python agent、Bridge HTTP / SSE、CI、外部APIの実環境異常を、観測証跡とcode上の仮説を分離して調査する時に使う。"
---

# 実環境調査 Skill

## 発動条件

利用者が実server、Minecraft world、deploy後の挙動、実運用log、外部API、CI上の異常を調査するよう求めた場合に使います。localだけで完結する不具合修正には必須ではありません。

## 1. 調査契約

- 調査対象、影響、発生時間帯、期待挙動、利用可能な環境を特定する。
- code、設定、local再現だけで実環境状態を断定しない。
- 実環境log、実data、server状態を確認した事実だけを「実環境で観測」と表現する。
- 確認できない範囲は、code上の仮説、再現条件、未確認事項として分離する。

## 2. 証跡

状況に応じて次を確認します。

- Minecraft / Paper serverの起動状態、plugin log、world、player、permission、保護領域
- Node botの接続、WebSocket、command、navigation、telemetry event
- Python agentのResponses API、LangGraph state、checkpoint、recovery、structured log
- Bridge HTTP / SSEの認証、request / response、subscription、job state
- GitHub Actionsのworkflow run、job、check、対象commit
- container / composeのprocess、health、network、設定差
- 外部APIのstatusと、このrepoで保持する最小限の相関情報
- 実環境とrepositoryのversion・設定差

調査メモには、何を、どの環境で、どの時間範囲・条件で確認したかを残す。公開物には追跡可能な実識別子、座標、user data、log原文を載せない。

## 3. Minecraft固有の再観測

停止、切断、retry、checkpoint復元後は、次を再観測してからworld操作を再開する。

- inventoryと装備
- player / botの位置と接続状態
- 周辺block、entity、hazard
- 保護領域とpermission
- 対象jobまたはdirectiveの現在状態
- checkpoint以降に起きた外部変更

古いsnapshotやcode上の予定だけで、安全な再開を断定しない。

## 4. 安全性

- read-only確認を優先する。
- world変更、command実行、server restart、rollback、再deploy、traffic変更、secret変更は、影響を説明して明示的な権限を得てから行う。
- secret、token、Cookie、authorization header、PII、chat全文、座標、内部URLを表示・記録しない。
- query結果やlogを外部LLMへ渡す場合は、最小化とmaskingを先に行う。
- 公開Issue、PR、運用文書を作る場合は公開安全性Skillも適用する。

## 5. 原因判定

原因を断定するには、少なくとも次を接続する。

1. 観測されたfailureまたは異常
2. そのfailureを説明するcode・設定・data・world state
3. 再現、対照確認、修正後確認のいずれか
4. 他の主要仮説を除外した根拠

接続できない場合は「最有力仮説」または「未特定」と報告する。

## 6. 報告

- 観測事実
- 影響範囲
- 原因または仮説と確度
- 実施した対応
- 実施していない操作
- 残るrisk
- 次の最短アクション

実環境へ到達できなかった場合は、到達不能の理由、確認済みのlocal範囲、残る不確実性を明記する。
