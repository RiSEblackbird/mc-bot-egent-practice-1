# UI/UX Review Framework

この枠組みは、デザイン判断を AI が実行可能なレビュー手順へ落とし込む。最優先は、初見ユーザーにとって妥協のない使いやすさである。

## 1. 最優先指令

ユーザーに見える interface は、説明書なしで次を明確にする。

1. これは何か。
2. 自分はどこにいるか。
3. 何ができるか。
4. 最初に何をすればよいか。
5. 何が変わったか。
6. 次に何が起きるか。
7. 失敗したら何ができるか。

interface がこれらに答えられない場合、完成ではない。

## 2. 「初見ユーザーにやさしい」の定義

interface は、ユーザーが次をできる場合に限って初見ユーザーにやさしい。

- 目に見える手がかりからページやコンポーネントの目的を理解できる。
- 主要操作を認識できる。
- 必要な入力を理解できる。
- 操作の結果を予測できる。
- よくあるミスから回復できる。
- 通常、空、読み込み中、エラー、無効、権限なし状態を区別できる。
- キーボードだけで主タスクを完了できる。
- 過密さ、専門用語、視覚的競合に妨げられず内容を読める。

## 3. レビュー pass

すべての UI/UX 変更で次の pass を実行する。

### Pass A: 画面目的と最初の行動

次に答える。

- この画面/コンポーネントは何のためか。
- 想定される初見ユーザーは誰か。
- 最初の意味ある行動は何か。
- 主要操作は探さずに見えるか。
- UI は現在の範囲、選択中の対象、filter、tab、mode を説明しているか。

答えにソースコード知識や事前の製品知識が必要なら fail とする。

### Pass B: 認知ウォークスルー

主タスクを行う novice user について確認する。

1. ここで達成できる目標を理解できるか。
2. 正しい control を見つけられるか。
3. その control が意図した操作を行うと理解できるか。
4. 操作後の feedback を理解できるか。
5. 間違えた場合、作業を失わず回復できるか。

### Pass C: 状態設計

関係するすべての状態を確認する。

- 通常
- 読み込み中
- 空
- 該当なし
- 部分データ
- 成功
- 警告
- エラー
- バリデーションエラー
- 無効
- 権限なし
- オフライン/利用不可
- 長いコンテンツ
- 狭い viewport
- 文字拡大

各状態は次を伝える。

- 何が起きたか
- なぜ重要か
- 次に何ができるか
- system がまだ動いているか
- ユーザーデータが安全か

### Pass D: 視覚階層

画面には明確な階層が必要である。

1. 目的/title
2. 現在の context/scope
3. 主要操作
4. 二次操作
5. 補助情報
6. metadata と diagnostics

metadata、badge、count、内部状態が主タスクと視覚的に競合する場合は fail とする。

### Pass E: アクセシビリティと inclusive design

WCAG 2.2 AA を最低基準とする。理解を助ける場合は、WCAG を超えて cognitive accessibility guidance を適用する。

最低限、次を確認する。

- キーボード操作
- キーボードトラップがない
- visible focus
- focus が隠れない
- accessible name
- label と instruction
- semantic heading
- status message
- contrast
- target size
- error identification
- error suggestion
- 色だけに意味を依存しない
- text resize と reflow
- 関係する場合は reduced motion

### Pass F: 文言と用語

UI 文言は実装用語ではなく、ユーザーの言葉を使う。

すべての操作ラベルは次に答える。

- これを押すと何が起きるか。
- どの対象に作用するか。
- 取り消せるか。

すべてのエラーは次に答える。

- 何が起きたか。
- 分かる場合、なぜ起きたか。
- 何に影響するか。
- 今何ができるか。

### Pass G: Content stress

次の条件で、頭の中または画面上で確認する。

- 長い日本語
- 長い英語
- 全角/半角混在文字列
- 任意データの欠落
- 多数項目
- 0 件
- 1 件
- 長い名前
- 狭い viewport
- 200% zoom または同等の文字拡大
- 遅い network
- 連続した validation error

### Pass H: Automation と証跡

利用可能なリポジトリ tooling を使う。例:

- lint
- typecheck
- unit test
- integration test
- browser test
- accessibility test
- visual regression test

tooling がない場合は、その gap を文書化し、証跡付きで手動推論を行う。

### Pass I: Counter-review

作業を却下するための adversarial review を行う。デザインを褒めるのではなく、P0 blocker を探す。

## 4. 重大度モデル

### P0: 完了不可

P0 は、その変更を完了扱いにしてはいけないことを意味する。

- 初見ユーザーが目的を理解できない。
- 最初の意味ある行動が不明確。
- 現在の scope/location が不明確。
- 主要操作が icon-only または視覚的に埋もれている。
- 状態設計が読み込み中/空/該当なし/エラー/無効/権限なしを混同している。
- エラーに回復手段がない。
- 無効状態に説明がない。
- キーボードでタスクを完了できない。
- focus がない、または隠れている。
- accessible name または label がない。
- contrast または target size の最低基準を満たさない。
- 破壊的操作に適切な予防または回復がない。
- 証跡 artifact がない。
- 検証を偽って主張している。

### P1: 明示的に延期しない限り merge 前に修正

- 用語が一貫していない。
- empty state が弱い。
- helper text が不明確。
- 視覚密度が高すぎる。
- 二次操作が主要操作と競合している。
- workaround はあるが responsive behavior が弱い。
- 修正方法が明確な non-blocking a11y issue。
- 非クリティカル状態の screenshot がない。

### P2: 改善機会

- polish issue
- 軽微な spacing inconsistency
- より明確な microcopy
- より良い grouping
- より強い progressive disclosure
- 将来の automation opportunity

## 5. 数値基準

リポジトリにより厳しい基準がない場合は、次を既定値として使う。

| 領域 | 最低基準 |
|---|---|
| 本文 text | 16px または相当を推奨。小さくする場合は理由があり、読みやすさを保つ |
| 長文 line height | 1.5 以上 |
| 段落間隔 | 長文では段落間に line height の 1.5 倍以上を推奨 |
| 日本語の行長 | 長文では 40 全角文字前後を目安にする |
| text contrast | WCAG AA: 通常文字 4.5:1、大きい文字 3:1 |
| non-text contrast | 意味のある UI graphic と component boundary は 3:1 以上 |
| pointer target | WCAG 2.2 AA: 24x24 CSS px 以上、または有効な spacing/exception |
| touch target | touch が想定される場合は 44-48px/dp を推奨 |
| focus indicator | 明確に見え、一貫し、隠れず、色だけに依存しない |
| motion | motion だけで重要情報を伝えない。reduced motion を尊重する |

## 6. Design anti-patterns

次は reject または flag する。

- icon-only の主要操作
- placeholder を label 代わりにした form
- 回復手段のない「Something went wrong」
- 理由がない disabled button
- 次の行動がない empty state
- scope が不明な tab/filter count
- 結果や影響を説明しない status badge
- 色だけに依存した visual hierarchy
- hover-only disclosure
- 隠れた destructive side effect
- 1 つの意思決定領域に複数の primary button
- 小さすぎる click target
- すべての datum が同じ重みで詰め込まれた card
- 実装用語をユーザーに見せる
- multi-step flow で記憶に依存する

## 7. 必須出力

すべての UI/UX review は次を出力する。

- screen purpose summary
- primary user task
- first meaningful action
- state matrix
- cognitive walkthrough
- accessibility review
- visual hierarchy review
- copy review
- counter-review
- P0/P1/P2 findings
- evidence list
- tests run / not run
- final pass/fail
