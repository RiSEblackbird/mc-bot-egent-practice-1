---
name: Operations / Production Investigation
about: Minecraft server、Paper plugin、Node bot、Python agent、Bridge、CI、外部APIの実環境調査
title: "[Ops]: "
---

## 事象・依頼内容

<!-- 何を調査・改善するか。 -->

## 対象環境

- local / CI / staging / production:
- Minecraft / Paper server:
- Python agent / Node bot / Bridge:
- container / deployment:
- 外部API:
- 発生日時または期間:

## 影響

<!-- 利用者影響、world / data影響、公開安全性、運用影響。 -->

## 調査・対応判断の理由と根拠

<!--
なぜ今調査・対応するか。確認済み事実、ユーザーから提示された判断材料、仮説、未確認事項を分けて書く。
詳細: docs/ai-governance/14-issue-quality-gate.md
-->

## 現在のユーザー体験

- 対象ユーザー:
- 利用文脈・達成したいこと:
- 現在ユーザーが経験していること:
- ユーザー視点での認識・負担・感情:
- 結果として生じている体験状態:
- 根拠区分（該当するものを残す）: ユーザー申告 / 実ユーザー観察 / 観測事実からの推定 / 未確認の仮説

<!-- 未確認のユーザー主観を事実として断定しない。詳細: docs/ai-governance/14-issue-quality-gate.md -->

## 対応後に目指すユーザー体験

- 調査のみの場合に直接的な体験変化がないことと理由:
- 対応または後続判断を通じた体験上の変化:
- ユーザーが理解・判断・実行・回復できるようになること:
- 結果として目指す体験状態:
- その変化を確認する方法:

## 確認済み事実

<!-- 実環境log、実data、world観測に基づく事実だけを書く。推測と混ぜず、公開Issueには必要最小限の要約だけを載せる。 -->

## 未確認事項

-

## 仮説

<!-- 仮説として明示する。断定しない。 -->

## 対応方針

<!-- 調査のみ / 修正PR / document化 / 監視追加 / 手順整備など。 -->

## 非対象

<!-- 今回は調査・変更しない環境、world、data、運用範囲。 -->

## 受け入れ条件

- [ ] 実環境log、実data、Minecraft world状態の確認要否が明記されている。
- [ ] 確認できた事実と推測が分離されている。
- [ ] 公開してはいけない情報がIssue / PR / docsに含まれていない。
- [ ] 修正または調査結果の検証方法が明記されている。
- [ ] 残るriskと次の最短アクションが明記されている。

## 検証方針

<!-- server / plugin log、world再観測、WebSocket、Bridge HTTP / SSE、CI、external API status、local testなど。 -->

## ロールバック・復旧方針

<!-- 必要な場合だけ。不要なら理由を書く。 -->

## 完了時に残す証跡

- PR本文:
- Issue comment:
- 運用document:
- CI / dry-run:
- その他:

## 公開安全性チェック

- [ ] 認証情報、Cookie、authorization header、API keyを含めていない。
- [ ] 個人情報、player識別子、chat全文を含めていない。
- [ ] log原文、精密座標、非公開server addressをそのまま貼っていない。
- [ ] request ID、trace ID、job ID、session IDなど、一意に掘れる値を必要以上に公開していない。
