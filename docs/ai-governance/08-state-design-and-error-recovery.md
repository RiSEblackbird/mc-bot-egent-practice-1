# State Design and Error Recovery

state design は必須である。happy path 以外の状態が設計されるまで、UI は完了ではない。

## 1. 必須 state

変更された画面/コンポーネントごとに、次の state を分類する。

| State | 必須? | Notes |
|---|---:|---|
| 通常 | yes | 通常の利用可能状態 |
| 読み込み中 | async の場合 | progress または skeleton を示す。可能な限り layout jump を避ける |
| 空 | data が存在しない可能性がある場合 | なぜ空か、次に何をするかを説明する |
| 該当なし | search/filter がある場合 | scope と広げ方を説明する |
| 部分データ | あり得る場合 | 利用可能なものと失敗したものを示す |
| 成功 | action が data を変える場合 | 結果と次の行動を確認できる |
| 警告 | risk がある場合 | action 前に consequence を説明する |
| エラー | operation が失敗し得る場合 | cause、impact、recovery を示す |
| バリデーションエラー | input がある場合 | field-specific message と suggestion を示す |
| 無効 | control が使えない場合 | reason と enabling condition を示す |
| 権限なし | permission がある場合 | permission boundary と、該当する場合は request path を説明する |
| オフライン/利用不可 | network または service に依存する場合 | retry と preservation behavior を示す |

## 2. 読み込み中 state

次に答える。

- 何を読み込んでいるか。
- system はまだ動いているか。
- user は cancel できるか、または別の場所で作業を続けられるか。
- user input は保持されるか。

長い処理で context のない indefinite spinner を避ける。

## 3. 空 state

次に答える。

- これは想定どおりか。
- ここにはどんな data が表示されるか。
- user が最初に何をできるか。
- permission または setup requirement があるか。

## 4. 該当なし state

empty state と同じ見え方にしない。

次に答える。

- どの query/filter の結果が 0 件だったか。
- どの scope を search したか。
- user はどう広げる、または filter を clear できるか。

## 5. Error state

次に答える。

- 何が失敗したか。
- 何がまだ安全か。
- 何を retry できるか。
- どの data が失われる可能性があるか。
- retry が失敗する場合、どこで助けを得られるか。

## 6. Disabled state

説明のない disabled は usability failure である。

次を使う。

- visible helper text
- inline reason
- accessible tooltip pattern
- validation guidance
- permission explanation

hover-only explanation に依存しない。

## 7. Permission denied

次に答える。

- どの permission が足りないか。
- permission がなくても何が見えるか。
- 分かる場合、誰が access を付与できるか。
- まだ利用できる action は何か。

## 8. Error recovery severity

risk に応じた recovery を使う。

| Risk | 必須 recovery |
|---|---|
| Low | retry または undo |
| Medium | confirmation、input 保持、impact の説明 |
| High | explicit confirmation、強い warning、関係する場合は audit trail |
| Irreversible | 明確な object/consequence/reversibility statement を必須にする |

## 9. State matrix requirement

すべての UI/UX review は state matrix を含める。`templates/state-matrix.md` を使う。
