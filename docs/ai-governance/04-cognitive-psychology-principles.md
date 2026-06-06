# Cognitive Psychology Principles for UI/UX Review

この文書は、認知心理学の考え方をレビュー可能な UI ルールへ変換する。

## 1. 認知負荷

ユーザーの working memory には限界がある。interface は不要な思考負荷を避ける。

ルール:

- 前の画面の値を覚えさせない。
- multi-step flow では progress、breadcrumb、step label で現在地を見えるようにする。
- 関連する control と情報を group 化する。
- advanced option は progressive disclosure で出す。
- recall より recognition を優先する。

レビュー質問:

- ユーザーは何を覚える必要があるか。
- UI がそれを表示できないか。
- 各画面に支配的な decision が 1 つあるか。
- 無関係な detail が task と競合していないか。

## 2. Recognition over recall

ユーザーは、見える label、layout、affordance から利用可能な action を認識できるべきである。

ルール:

- primary action を icon の記憶に依存させない。
- 必須 action を hover-only UI に隠さない。
- 重要な control には visible label を使う。
- recoverable error の後も、選択 context と user input を保持する。

## 3. Mental model

ユーザーは慣れた pattern を通して UI を解釈する。

ルール:

- 強い根拠がない限り、一般的な action は慣習的な位置に置く。
- ユーザーが使う用語を使う。
- 似た action は見た目と挙動を一貫させる。
- system status と consequence を明示する。

## 4. Attention と signal-to-noise

視覚的 attention には限界がある。

ルール:

- すべての item を同じ視覚的重みにしない。
- hierarchy で title、context、primary action、feedback へ視線を導く。
- ユーザーの action を変えない純粋な診断情報は抑える。
- badge、count、timestamp、internal status を primary task と競合させない。

## 5. Error prevention and recovery

ユーザーは時間圧、注意散漫、不確実性の中で間違える。

ルール:

- destructive mistake は起きる前に予防する。
- 低リスク action は可能なら reversible にする。
- validation error の後も user input を保持する。
- error message は原因箇所の近くに表示し、必要なら summary も出す。
- failure label だけでなく next step を示す。

## 6. Decision complexity

似た選択肢が多すぎると、判断が遅くなり質も落ちる。

ルール:

- 画面ごとの decision point を減らす。
- primary、secondary、destructive action を分離する。
- まれな action や expert action は progressive disclosure を使う。
- 1 つの decision area に複数の visually primary button を置かない。

## 7. Spatial memory と consistency

ユーザーは繰り返される配置から期待を作る。

ルール:

- recurring navigation と action は一貫した位置に置く。
- state 自体が task を変える場合を除き、primary action を state ごとに動かさない。
- focus order は visual order と揃える。

## 8. レビュー出力

cognitive review では、必ず次を列挙する。

- ユーザーが知覚する必要があるもの
- ユーザーが覚える必要があるもの
- ユーザーが判断する必要があるもの
- ユーザーが回復できるもの
- UI が認知負荷を下げている箇所、または上げている箇所
