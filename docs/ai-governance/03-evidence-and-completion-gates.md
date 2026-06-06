# Evidence and Completion Gates

UI/UX 作業は、証跡が存在するときだけ完了である。

## 1. 必須 artifact

UI/UX 変更では、次を作成または更新する。

1. UI/UX review report
2. State matrix
3. Novice simulation
4. Accessibility review
5. Visual hierarchy review
6. Counter-review
7. Completion gate report

`docs/ai-governance/templates/` のテンプレートを使う。

## 2. 完了 gate

### Gate 1: Scope gate

次を満たす場合のみ pass。

- 変更された画面/コンポーネントが列挙されている。
- ユーザー goal が書かれている。
- first meaningful action が書かれている。
- 影響を受ける state が列挙されている。

### Gate 2: 初見理解 gate

次を満たす場合のみ pass。

- 画面目的が見える。
- 現在の scope/location が見える。
- 主要操作を視覚的に見つけられる。
- 重要な結果や影響が操作前に説明されている。

### Gate 3: State gate

次を満たす場合のみ pass。

- 通常、読み込み中、空、該当なし、エラー、無効、権限なし状態が意図的に設計されている、または非該当と明示されている。
- 各状態に次の行動または明確な説明がある。
- エラー状態と無効状態に回復 guidance がある。

### Gate 4: Accessibility gate

次を満たす場合のみ pass。

- 主タスクがキーボードだけで動く、または例外が文書化されている。
- focus が見え、隠れない。
- control に accessible name がある。
- 必要な場所に label と instruction がある。
- contrast/target/semantic の確認が実施されている、または未実行理由が明示されている。
- 既知の WCAG AA blocker が残っていない。

### Gate 5: Visual clarity gate

次を満たす場合のみ pass。

- 主要操作が視覚的に最も目立つ。
- hierarchy が scan しやすい。
- content density に理由がある。
- 長い content と狭い viewport が考慮されている。
- metadata が task-relevant content を圧倒していない。

### Gate 6: Counter-review gate

次を満たす場合のみ pass。

- adversarial review が実施されている。
- P0/P1/P2 findings が列挙されている。
- unresolved risks が列挙されている。
- final pass/fail の理由が示されている。

## 3. 良い証跡

良い証跡は次を満たす。

- 具体的である。
- 対象 screen/state/component が明確である。
- command を実行した場合は出力を含む。
- 利用可能な場合は screenshot/trace を含む。
- 限界を列挙する。

悪い証跡の例:

- 「looks good」
- 「should work」
- 手順のない「手動テストした」
- happy path screenshot だけ
- keyboard と focus を見ずに a11y pass と主張する
- 実ユーザーなしに user validation と主張する

## 4. 証跡を生成できない場合

screenshot、browser test、自動 accessibility check を生成できない場合は次を行う。

1. 理由を正確に書く。
2. 利用可能な最善の代替証跡を示す。
3. residual risk を明記する。
4. 代替証跡が実際に risk を覆っていない限り、gate を完全 pass にしない。

## 5. 最終 pass/fail 表現

次のいずれかを使う。

- `PASS`: P0 が残っておらず、P1/P2 が文書化または解決済み。
- `PASS WITH RISK`: P0 は残っていないが、証跡が不完全、または P1 が明示的延期付きで残っている。
- `FAIL`: P0 が残っている、または検証が実質的に不足している。

これ以外の柔らかい分類を作らない。
