# ドキュメント公開セキュリティチェックリスト

この文書は、git に push される Markdown / text / サンプル / レポート / 運用記録に、秘密情報や不要な攻撃材料を残さないための確認基準です。

## 適用対象

- `README.md`, `docs/**`, `.github/**`, `plans/**` など、リポジトリで追跡される文書
- PR本文へ転記する検証結果、ログ要約、障害・運用記録
- サンプル設定、コマンド例、スクリーンショット説明、テスト fixture の説明文

## push 前の必須確認

次のいずれかが含まれる場合は、commit / push 前に削除、マスク、または公開可能な抽象表現へ置き換える。

| 種別 | 書いてはいけないもの | 置き換え例 |
|---|---|---|
| 認証情報 | API key, token, password, private key, certificate, webhook secret, OAuth client secret, service account JSON | `<redacted>`, `環境変数で管理` |
| セッション情報 | Cookie, `Authorization` header, JWT, CSRF token, signed URL | `認証ヘッダーを確認` |
| 個人情報 | メールアドレス、ユーザーID、氏名、IPアドレス、問い合わせ本文、ユーザー入力全文 | `対象ユーザー`, `該当入力` |
| 実環境リソース識別子 | 非公開 service URL、Minecraft server address、内部ホスト名、内部IP、cloud project ID、完全な deploy / revision 名 | `対象サーバー`, `対象revision`, `内部URLは非公開` |
| 運用ログ原文 | リクエスト/レスポンス全文、ログエントリ全文、stack trace全文、trace ID / request ID / job ID の実値 | 必要な事実だけを要約 |
| 調査クエリ | 本番ログを一意に掘れる完全な Logs Explorer query | クエリ観点だけを書く |
| 時刻粒度 | 攻撃やリトライ再現に不要な秒単位時刻 | 日付、時間帯、前後関係 |

## 書いてよいもの

- 既にソースコードで公開されているファイルパス、関数名、公開APIパス
- commit SHA、PR番号、GitHub Actions の公開 check 名
- 秘密値を含まないエラー種別、HTTP status、状態遷移、原因の要約
- 「正確な値は private log で確認済み」と分かる、公開用に丸めた証跡

## 運用記録での追加ルール

- 本番ログを根拠にする場合、ログ原文ではなく `観測した事実`、`判断`、`対応`、`残リスク` に分けて要約する。
- `job_id`, `request_id`, `trace_id`, `session_id`, user identifier など、単独で秘密でなくても追跡に使える実値は書かない。
- deploy revision、Minecraft server address、内部ホスト、デプロイ時刻は、再発確認に必要なら private log に残し、公開文書では `対象サーバー`, `対象revision`, `同日JSTの時間帯` などへ丸める。
- exact value を書く必要があると判断した場合は、公開リポジトリに置く前にメンテナ判断を挟み、PR本文に理由を書く。

## 漏洩を見つけた場合

- push 前なら、該当値を削除してから commit する。
- push 後なら、値を削除するだけでなく、該当 secret の rotate / revoke、影響調査、必要な履歴対応を検討する。
- 「公開済みだからそのままでよい」と判断しない。既に別場所に出ていても、このリポジトリで再拡散しない。

## 最低限の確認方法

- `git diff --check`
- 変更差分の目視確認
- `secret`, `token`, `key`, `password`, `cookie`, `Authorization`, `Bearer`, `client_secret`, `private_key`, `service_account`, `request_id`, `trace_id`, `job_id` などの語で差分を検索
- 文書中の日時・revision・project ID・URLが、公開する必要のある粒度か確認
