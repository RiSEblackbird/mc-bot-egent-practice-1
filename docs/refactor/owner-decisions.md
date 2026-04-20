# Owner Decisions (Refactor Foundation)

本ファイルは、実装を進めながらも最終値を所有者判断に委ねる項目を集約する。

## 未確定項目

| 項目 | 現状 | 推奨方針（実装側） | 最終決定者 |
| --- | --- | --- | --- |
| LICENSE | 未設定 | 現状は追加しない。公開形態に合わせて別途決定。 | Repository owner |
| 本番 secret 実値 | ダミー / ローカル値のみ | `.env` へ実値を書かず、秘密管理基盤へ移管する。 | Ops / Security owner |
| ダッシュボードの prod 公開可否 | 明示未決 | 既定は無効または token 必須を維持。 | Product owner |
| 本番認証方式（Microsoft auth 等） | 開発向け設定あり | `env.prod.example` は安全側を維持し、本番要件で確定。 | Product + Security owner |
| 本番 checkpointer backend | dev は SQLite 前提 | インターフェース互換を維持し、Postgres 等は運用要件で選定。 | Platform owner |

## 判断待ちでも固定した実装境界

- コード上は **安全側デフォルト** を維持し、実値依存を持ち込まない。
- 互換レイヤ（legacy adapter）は削除計画を伴って管理する。
- 本番要件が確定していない箇所は docs に明記し、暗黙仕様にしない。
