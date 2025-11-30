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

### 2.1 シミュレーション関連パラメータ

- `MINEDOJO_SIM_ENV` / `MINEDOJO_SIM_SEED` / `MINEDOJO_SIM_MAX_STEPS`  
  `MineDojoSelfDialogueExecutor` が自己対話フローを走らせる際のワールド設定です。LangGraph から
  `executor="minedojo"` の directive が届いた場合、これらの値をベースに簡易シミュレーションを初期化し、
  `python/services/minedojo_client.py` が取得したデモと同じ条件で再生できるようにします。

## 3. データ利用ポリシー

1. **ライセンス遵守**: MineDojo の公開データセットは学術研究用途を前提としています。商用利用や再配布を行う場合は、公式ライセンスに従って許諾を得てください。
2. **機密情報の保護**: API 応答やデモ軌跡にはプレイヤー名・座標などの情報が含まれる場合があります。`var/cache/minedojo/` 以下のファイルは `.gitignore` に登録済みですが、ログやスクリーンショットにも不要な情報が残らないよう注意してください。
3. **アクセス制御**: `MINEDOJO_API_KEY` は機密情報です。`.env` などの秘匿ファイルで管理し、共有リポジトリへコミットしないでください。CI で利用する場合はシークレット管理機構（GitHub Actions Secrets 等）を利用してください。
4. **キャッシュの無害化**: 共有マシンで開発する場合は、不要になったキャッシュを `rm -rf var/cache/minedojo` で削除し、他メンバーが誤って過去データを使用しないようにしてください。
5. **テレメトリ抑止**: テスト環境では `MINEDOJO_API_KEY` を空にし、ローカルデータセットのみで検証することを推奨します。これにより外部 API への不要なリクエストや利用規約違反を防げます。

## 4. ワークフロー統合

1. エージェントはタスク分類結果からカテゴリを決定し、`python/agent.py` の `_MINEDOJO_MISSION_BINDINGS` でミッション ID を解決します。
2. `MineDojoClient` がキャッシュ→ローカル→API の順にミッション情報とデモを探索し、成功した場合は `Actions.play_vpt_actions` にデモを自動送信します。
3. LLM プロンプトへはミッション概要とデモ要約が `minedojo_context` キーとして注入されます。ミッション ID・タグを含む構造化メタデータを LangGraph 状態へ残すため、後続のステップでも同一ミッションかどうかを判別しやすくなりました。
4. MineDojo から得た情報は `tests/integration/test_minedojo_adapter.py` で検証しており、スタブクライアントを差し込むことで外部サービスへアクセスせずに回帰テストを実行できます。デモの自動登録と再利用の流れは `tests/integration/test_minedojo_skill_registration.py` で統合的に確認できます。
5. `MineDojoSelfDialogueExecutor` は `MINEDOJO_SIM_*` で指定された環境を使い、`python/services/minedojo_client.py` が返すデモを
  `Actions.play_vpt_actions` へ引き渡す前に検証します。シミュレーション失敗時は `recovery_hints` に反省点を追記し、
  LangGraph が次のプラン生成で参照できるようにします。
6. `python/agent.py` の `_maybe_trigger_minedojo_autorecovery` が、プラン生成に失敗した場合や MineDojo 対応カテゴリで十分な
  手順が得られなかった場合に自己対話ループを自動で起動し、チャットへ確認メッセージを送信します。

## 5. 自動スキル登録とタグ検索

* 取得したデモは `Actions.registerSkill` へも送信され、`SkillRepository` にミッション ID と `mission:<id>`/`minedojo` タグ付きで永続化されます。Mineflayer 側の NDJSON ログと同じタグを付与することで、スキルの有無を即座に突き合わせられます。
* スキル照合はミッション ID やタグを重み付けに利用するため、「同じミッションをもう一度」などの曖昧なリクエストでも既存スキルを優先的に `invoke_skill` 経由で呼び出します。再学習を挟まずにデモ由来スキルを再利用できる点をユーザーへ明示してください。
* デモメタデータは `mission_id`・`demo_id`・`tags`・`summary` を含む構造体として LangGraph 状態・Memory に残り、Mineflayer 側のタグ付けフォーマットと揃えています。MineDojo のタグやミッション ID が意図せず漏えいしないよう、`.gitignore` に登録済みのキャッシュディレクトリから外へ持ち出さない運用を徹底してください。

## 6. ActionDirective と executor の連携

- `python/planner.py` が出力する `PlanOut.directives[].executor` に `"minedojo"` を指定すると、`agent.AgentOrchestrator` は `_handle_minedojo_directive()` を介して `MineDojoSelfDialogueExecutor` を直接呼び出し、ReAct トレースとスキル登録を自動的に更新します。
- directive の `args.mission_id` を省略した場合でも `_MINEDOJO_MISSION_BINDINGS` に登録されたカテゴリ（例: `mine` → `obtain_diamond`）からミッション ID を解決します。個別のデモを指定したい場合は `args.skill_id` / `args.demo_id` を埋め、MineDojo API 側の ID 体系と合わせてください。
- `executor="mineflayer"` の directive は従来どおり `Actions` へ meta 付きで送信されます。`node-bot/runtime/telemetry.ts` が `command.meta.directive_id` を OpenTelemetry に記録するため、MineDojo と Mineflayer のどちらが処理したステップかをダッシュボードで即座に判別できます。
- `executor="chat"` を指定すると Python エージェントが `actions.say()` でフォローアップを送信し、`args.message` が指定されていない場合は `directive.label` をそのままプレイヤーへ relay します。MineDojo の状況説明や確認待ちフローを構造化ステップとして扱えるのが利点です。

## 7. トラブルシューティング

* **API キー未設定時に警告が出る**: 仕様です。ローカルデータセットが利用可能であれば自動でフォールバックします。
* **JSON の形式エラー**: `MineDojoClient` は例外をログへ出力し、失敗時はミッション/デモを返しません。テスト用に `python -m json.tool` でフォーマットを検証するか、`tests/integration/test_minedojo_adapter.py` を実行して構造を確認してください。
* **Mineflayer へデモが届かない**: `Actions.play_vpt_actions` が存在しない環境では自動送信をスキップします。Mineflayer 実装側で同名コマンドをサポートしているか確認してください。`MineDojoSelfDialogueExecutor` はこの場合 `executor="chat"` へフォールバックし、状況説明だけを LangGraph に残します。

