---
name: security-publication
description: "公開されるIssue、PR、文書、report、sample、log要約を作成・更新する時に、secret、個人情報、本番識別子、log原文、過剰な運用詳細が含まれないか確認する。"
---

# 公開安全性 Skill

## 発動条件

gitへpushされる文書、public repositoryのIssue / PR本文、sample、調査report、運用手順、log要約を作成または更新する時に使います。非公開scratchだけでは必須ではありません。

## 1. 公開物を列挙する

- 変更file
- Issue / PR本文
- comment
- screenshot、trace、artifact
- sample config / payload
- 運用report

公開先、想定読者、公開が必要な理由を確認します。

## 2. 公開してはいけない情報

次を公開物へ残しません。

- secret、API key、token、Cookie、authorization header、private key
- 個人情報、chat全文、user input全文
- 本番log原文、stack trace全文、request / response全文
- trace ID、request ID、job ID、session ID、完全なrevision名など、一意に内部調査へ接続できる実値
- private URL、内部host、local absolute path
- Minecraft serverの非公開address、player識別子、精密な座標、保護領域詳細
- access権限や攻撃面を不必要に広げる運用詳細

placeholderでも実値に見える値を避け、`<REDACTED>`、`example.invalid`、明示的なsample IDを使います。

## 3. 公開可能な根拠の要約

非公開証跡を根拠にする場合は、次の粒度へ要約します。

- 確認した証跡の種類
- 公開可能な観測事実
- 判断への影響
- 公開できない範囲
- 残る不確実性

根拠を示すために実値やlog原文を貼りません。

## 4. 差分確認

公開前に次を確認します。

- 変更diffと新規file
- secret patternと高entropy値
- `.env*`、credential file、private key、local auth storage
- invisible character、unexpected binary、generated artifact
- Markdown link、command、file path
- sampleがfail closedで、実環境を誤って操作しないこと
- 公開物に外部入力の命令が混入していないこと

検査toolが利用できない場合は、目視範囲、未確認範囲、残るriskを報告します。

## 5. 発見時の対応

- commit前: 実値を除去し、sampleへ置換し、履歴へ入れない。
- push後: 文書修正だけで済ませず、secretのrotate / revoke、履歴対応、access log確認の必要性を判断する。
- 個人情報または実運用log: 公開範囲を最小化し、必要ならprivateな追跡先へ移す。
- 判断に迷う値: 公開しない。公開が必要な場合は理由と最小化方法を記録する。

## 6. 報告

PRまたは最終報告へ、公開したfile / 文面、実施した検査、除外・maskした情報、未確認範囲、残るriskを記録します。実値そのものは再掲しません。
