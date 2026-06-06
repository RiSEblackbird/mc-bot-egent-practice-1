# Visual Hierarchy and Information Architecture

interface は意図的に attention を導く必要がある。

## 1. 階層順

すべての画面は、次の順序を視覚的に読み取れるようにする。

1. product/page area または screen purpose
2. current object、filter、mode、scope
3. primary task または primary action
4. required input
5. secondary action
6. supporting detail
7. diagnostics、metadata、rare action

## 2. Primary action ルール

- decision area ごとに primary action は 1 つ。
- primary action label は結果を表す動詞を含む。
- primary action を icon-only にしない。
- destructive primary action には risk に応じた confirmation または recovery を用意する。
- disabled primary action は、なぜ使えないかを説明する。

## 3. Grouping ルール

proximity、alignment、heading、whitespace で grouping を伝える。

ユーザーが implementation structure、DOM order、hidden context から関係を推測しなければならない場合は fail とする。

## 4. Typography ルール

- 読みやすい default size を使う。
- 長文 text は line height 1.5 前後以上を使う。
- 長い all-caps text を避ける。
- 特に日本語では、長い italic text を避ける。
- 読む量が多い箇所では行長を扱いやすく保つ。
- logo など不可避な場合を除き、画像化された text を使わない。

## 5. Density ルール

高密度 UI は、次を満たす場合のみ許容する。

- primary task がなお明確である。
- scanning order が明確である。
- critical action が埋もれていない。
- 長い content で layout が崩れない。
- narrow viewport でも使える layout がある。

## 6. Visual affordance ルール

interactive element は interactive に見える必要がある。

次は reject する。

- body text に見えるが button として動く text
- 有効なのに disabled に見える button
- 無効なのに enabled に見える control
- 重要 action の hover-only affordance
- 小さく曖昧な icon

## 7. Information architecture ルール

- navigation label は互いに区別できる。
- tab、filter、count の scope は明示する。
- 深い flow または multi-step flow には breadcrumb または同等の orientation を置く。
- scope が明白でない search は、何を search するかを示す。
- empty state と no-results state を混同しない。

## 8. Content stress test

次の条件で visual hierarchy を確認する。

- 0 件、1 件、多数件
- 長い名前
- metadata 欠落
- warning banner と error banner
- narrow viewport
- 翻訳された文字列
- 200% text zoom
- 高密度の実データ
