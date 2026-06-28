# 参照する標準・研究・実務知見

この文書は、UI/UXガバナンスの背景となる参照先を整理します。

AIエージェントは、参照先の名前を並べるだけでレビューを終えてはいけません。参照した知見は、画面上の観察点、Pass/Fail条件、証跡、修正案へ変換してください。

## 1. アクセシビリティ標準

- W3C Web Content Accessibility Guidelines (WCAG) 2.2
  https://www.w3.org/TR/WCAG22/

用途:

- キーボード操作
- フォーカス
- コントラスト
- ターゲットサイズ
- 名前・役割・値
- エラー識別
- ステータスメッセージ
- リフロー
- 入力支援

## 2. 認知アクセシビリティ

- W3C Making Content Usable for People with Cognitive and Learning Disabilities
  https://www.w3.org/TR/coga-usable/

用途:

- 明確な目的
- 見つけやすい重要タスク
- 理解しやすい言葉
- 記憶に頼らない設計
- ミスの予防と回復
- 集中しやすい構造

## 3. HCI・ユーザビリティ原則

- Nielsen Norman Group: 10 Usability Heuristics for User Interface Design
  https://www.nngroup.com/articles/ten-usability-heuristics/

- Nielsen Norman Group: Usability 101
  https://www.nngroup.com/articles/usability-101-introduction-to-usability/

用途:

- システム状態の可視化
- 現実世界との対応
- ユーザー制御と自由
- 一貫性
- エラー予防
- 記憶より認識
- 効率性
- 最小限で意味のある設計
- エラー回復
- ヘルプ
- useful = utility + usability の考え方

## 4. 日本語UIとタイポグラフィ

- デジタル庁デザインシステム: タイポグラフィ（アクセシビリティ）
  https://design.digital.go.jp/dads/foundations/typography/accessibility/

用途:

- 日本語長文の読みやすさ
- 行高
- 行長
- 文字拡大
- 文字画像の回避
- フォント変更への耐性

## 5. AIエージェント運用

- OpenAI Codex: Custom instructions with AGENTS.md
  https://developers.openai.com/codex/guides/agents-md

- OpenAI Codex: Agent Skills
  https://developers.openai.com/codex/skills

- Claude Code: Memory / CLAUDE.md
  https://code.claude.com/docs/en/memory

用途:

- `AGENTS.md` を起点にしたルール設計
- Skillによる重い作業手順の分離
- `CLAUDE.md` から `AGENTS.md` を参照する設計

## 6. 最新研究の扱い

最新研究は重要ですが、単発研究をただちにP0ルールへ昇格させてはいけません。

取り込み手順:

1. 研究が扱う対象ユーザー、タスク、環境を確認する。
2. 既存標準・HCI原則・認知アクセシビリティ指針と矛盾しないか確認する。
3. 画面上の観察点へ変換する。
4. Pass/Fail条件へ変換できるか確認する。
5. 強制ルール、推奨ルール、検証仮説のどれに置くか判断する。

## 7. 採用しない参照の扱い

次は採用根拠として弱いです。

- 出典不明のブログ記事
- 根拠のないSNS投稿
- 対象条件が極端に限定された単発実験
- デザインの流行だけを根拠にした記事
- 実装ツール固有の都合だけで作られた規約

採用する場合は、補助観点として扱い、P0化しないでください。
