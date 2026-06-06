# UI Copy and Microcopy Rules

UI copy は飾りではなく interface の一部である。

## 1. ユーザー言語ルール

内部実装用語ではなく、ユーザーの言葉を使う。

次は reject する。

- 内部 model 名
- database field 名
- 説明のない status code
- 説明のない abbreviation
- 初回接触時の product-specific jargon

## 2. Action label ルール

action label は結果を示す。

望ましい例:

- 「一覧を作成」
- 「変更を保存」
- 「メンバーを招待」
- 「アップロードを再試行」

避ける曖昧な label:

- 「OK」
- 結果が不明な「送信」
- scope が不明な「適用」
- 重要 action の icon-only control

## 3. Error message ルール

操作可能な error は次を含む。

1. 何が起きたか。
2. 分かる場合、なぜ起きたか。
3. 何が影響を受けるか。
4. ユーザーが次に何をできるか。

次だけで終わらせない。

- 「エラー」
- 「失敗しました」
- 「問題が発生しました」
- 「入力が不正です」

## 4. Empty state ルール

empty state は次を説明する。

- なぜ空なのか。
- それが想定どおりか。
- ユーザーが次に何をできるか。
- データができた後に何が表示されるか。

empty、no-results、permission-denied、error state に同じ copy を使わない。

## 5. Disabled state ルール

disabled control は次を伝える。

- なぜ disabled なのか。
- 何をすれば enabled になるか。
- ユーザーに permission があるか。
- 待機、選択、入力、plan/account state のどれが必要か。

inline に説明を置けない場合は、近くの helper text または accessible tooltip pattern を使う。

## 6. Confirmation ルール

destructive または irreversible action の copy は次を示す。

- 影響を受ける object
- consequence
- reversible かどうか
- 回復手段がある場合はその内容

## 7. Consistency ルール

1 つの概念には 1 つの label を使う。ユーザーにとって意味のある区別がない限り、同義語を交互に使わない。

繰り返し出る domain term がある場合は、local terminology list を維持する。
