# MineDojo 連携ガイド

Python エージェントから MineDojo のミッション/デモを参照する際の手順と、データ利用に関する注意事項をまとめます。

## 1. ディレクトリ構成

`MINEDOJO_DATASET_DIR` を指定した場合は、以下の構成で JSON ファイルを配置してください。

```
<MINEDOJO_DATASET_DIR>/
  missions/
    obtain_diamond.json
    harvest_wheat.json
    build_simple_house.json
  demos/
    obtain_diamond.json
    harvest_wheat.json
    build_simple_house.json
```

* `missions/*.json` … ミッションのメタ情報。`title`・`objective`・`tags` 等のキーを含めます。
* `demos/*.json` … デモの配列を格納する JSON。`[{"id": ..., "summary": ..., "actions": [...]}, ...]` の形式を推奨します。
* `actions` 配列は Mineflayer へそのまま転送可能な `{ "type": "moveTo", "args": {...} }` フォーマットを想定しています。過剰なデータは `python/services/minedojo_client.py` 側でフィルタリングされます。

## 2. 環境変数

| 変数名 | 用途 | 既定値 |
| --- | --- | --- |
| `MINEDOJO_API_BASE_URL` | MineDojo API のベース URL | `https://api.minedojo.org/v1` |
| `MINEDOJO_API_KEY` | API 認証用トークン。未設定ならローカルデータセットのみ利用 | なし |
| `MINEDOJO_DATASET_DIR` | ローカル JSON データセットのルートパス | なし |
| `MINEDOJO_CACHE_DIR` | API 応答とデモをキャッシュするディレクトリ | `var/cache/minedojo` |
| `MINEDOJO_REQUEST_TIMEOUT` | API リクエストのタイムアウト秒数 | `10.0` |

## 3. データ利用ポリシー

1. **ライセンス遵守**: MineDojo の公開データセットは学術研究用途を前提としています。商用利用や再配布を行う場合は、公式ライセンスに従って許諾を得てください。
2. **機密情報の保護**: API 応答やデモ軌跡にはプレイヤー名・座標などの情報が含まれる場合があります。`var/cache/minedojo/` 以下のファイルは `.gitignore` に登録済みですが、ログやスクリーンショットにも不要な情報が残らないよう注意してください。
3. **アクセス制御**: `MINEDOJO_API_KEY` は機密情報です。`.env` などの秘匿ファイルで管理し、共有リポジトリへコミットしないでください。CI で利用する場合はシークレット管理機構（GitHub Actions Secrets 等）を利用してください。
4. **キャッシュの無害化**: 共有マシンで開発する場合は、不要になったキャッシュを `rm -rf var/cache/minedojo` で削除し、他メンバーが誤って過去データを使用しないようにしてください。
5. **テレメトリ抑止**: テスト環境では `MINEDOJO_API_KEY` を空にし、ローカルデータセットのみで検証することを推奨します。これにより外部 API への不要なリクエストや利用規約違反を防げます。

## 4. ワークフロー統合

1. エージェントはタスク分類結果からカテゴリを決定し、`python/agent.py` の `_MINEDOJO_MISSION_BINDINGS` でミッション ID を解決します。
2. `MineDojoClient` がキャッシュ→ローカル→API の順にミッション情報とデモを探索し、成功した場合は `Actions.play_vpt_actions` にデモを自動送信します。
3. LLM プロンプトへはミッション概要とデモ要約が `minedojo_context` キーとして注入されます。反省ログや検出レポートと同様に `Memory` へ保存されるため、後続の再計画でも参照可能です。
4. MineDojo から得た情報は `tests/integration/test_minedojo_adapter.py` で検証しており、スタブクライアントを差し込むことで外部サービスへアクセスせずに回帰テストを実行できます。

## 5. トラブルシューティング

* **API キー未設定時に警告が出る**: 仕様です。ローカルデータセットが利用可能であれば自動でフォールバックします。
* **JSON の形式エラー**: `MineDojoClient` は例外をログへ出力し、失敗時はミッション/デモを返しません。テスト用に `python -m json.tool` でフォーマットを検証するか、`tests/integration/test_minedojo_adapter.py` を実行して構造を確認してください。
* **Mineflayer へデモが届かない**: `Actions.play_vpt_actions` が存在しない環境では自動送信をスキップします。Mineflayer 実装側で同名コマンドをサポートしているか確認してください。

