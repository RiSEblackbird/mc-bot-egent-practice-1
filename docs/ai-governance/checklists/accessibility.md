# Accessibility Checklist

## Keyboard

- [ ] 主タスクをキーボードだけで完了できる。
- [ ] focus order が visual/logical order に沿っている。
- [ ] keyboard trap がない。
- [ ] modal/menu/popover の focus behavior が正しい。

## Focus

- [ ] focus が見える。
- [ ] focus が隠れない。
- [ ] focus style が一貫している。
- [ ] focus が色だけに依存していない。

## Names and labels

- [ ] control に accessible name がある。
- [ ] icon button に label がある。
- [ ] 該当する場合、visible label が accessible name と一致している。
- [ ] input に永続的な label または同等の説明がある。

## Semantics

- [ ] heading が意味を持ち、順序が正しい。
- [ ] 該当する場合、landmark/region structure が意味を持つ。
- [ ] list/table/form/button が適切な semantics を使っている。
- [ ] status update を知覚できる。

## Visual accessibility

- [ ] text contrast が最低基準を満たす。
- [ ] 意味のある UI parts の non-text contrast が最低基準を満たす。
- [ ] target size が十分である。
- [ ] 意味を色だけで伝えていない。
- [ ] text resize/reflow ができる。

## Forms and errors

- [ ] required field が分かる。
- [ ] validation error が field と issue を特定している。
- [ ] 分かる場合は suggestion/recovery が示されている。
- [ ] user input が保持される。

## Motion

- [ ] unsafe flashing がない。
- [ ] critical information の理解に motion が必須ではない。
- [ ] 関係する場合、reduced motion を尊重している。
