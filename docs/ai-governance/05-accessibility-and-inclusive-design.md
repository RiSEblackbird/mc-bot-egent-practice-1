# Accessibility and Inclusive Design

アクセシビリティは任意の polish ではない。ユーザーに見える作業の completion gate である。

## 1. 基準

リポジトリにより厳しい要件がない限り、WCAG 2.2 AA を最低基準にする。WCAG の testable criteria だけでは覆いきれない問題でも、理解を助ける cognitive accessibility guidance を考慮する。

## 2. 必須確認

### Keyboard

- 主タスクをキーボードだけで完了できる。
- focus order が visual order と logical order に合っている。
- keyboard trap がない。
- modal、menu、popover、drawer が focus を正しく管理する。

### Focus

- focus indicator が見える。
- focus が sticky header、dialog、overlay の裏に隠れない。
- focus style が一貫している。
- focus を色だけで伝えていない。

### Names, roles, values

- control に accessible name がある。
- icon button に明示的な label がある。
- visible label がある場合、必要に応じて accessible name に含まれている。
- custom control が role、state、value を公開している。

### Semantics

- page と region に意味のある heading がある。
- heading order が論理的である。
- 有用な場合は landmark を使う。
- list、table、form、button が適切な semantics を使う。

### Contrast と視覚認識

- 通常 text contrast は 4.5:1 以上。
- 大きい text contrast は 3:1 以上。
- 意味のある icon、boundary、focus indicator は該当する場合 3:1 以上。
- state を色だけで伝えない。

### Target size

- pointer target は 24x24 CSS px 以上、または有効な例外を満たす。
- touch-oriented interface では 44-48 px/dp 以上を推奨する。
- 隣接する destructive action と safe action には十分な距離を置く。

### Text と layout

- content や機能を失わず text resize できる。
- 長文 content は読みやすい line height を使う。
- 読む量が多い content では長すぎる行を避ける。
- 本当に必要な場合を除き、horizontal scrolling なしで reflow する。

### Motion

- flashing content を避ける。
- critical feedback に不要な motion を使わない。
- motion が存在する場合は reduced-motion preference を尊重する。

### Errors と forms

- required field が識別できる。
- validation error が field と issue を特定している。
- 分かる場合は suggestion を示す。
- error 後も user input を保持する。
- authentication や repeated-entry flow で不要な記憶負荷を避ける。

## 3. 自動確認

利用可能な場合は次の tool を使う。

- axe または同等の rendered-DOM checker
- 関係する場合は eslint accessibility plugin
- keyboard flow の browser test
- Storybook accessibility check
- focus と state snapshot の visual regression

自動 accessibility check だけでは十分ではない。keyboard review、state review、cognitive walkthrough の代替にはならない。

## 4. P0 accessibility failure

- keyboard で主タスクを完了できない。
- focus が見えない、または隠れている。
- actionable control に accessible name がない。
- visible label のない icon-only primary action。
- essential text または control の contrast が最低基準未満。
- error message が input と programmatic または visual に関連付いていない。
- status update を知覚できない。
- accessible alternative のない drag-only action。
- accessible alternative のない記憶/パズル要求を含む authentication または verification。
