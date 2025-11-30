# 建築フェーズ・ステートマシン仕様

建築モジュールでは、長期ジョブが途中で中断されても安全に再開できるよう、
フェーズ管理を LangGraph ノードと純粋関数へ分離します。本書ではフェーズ定義と
遷移条件、LangGraph ノード実装時の設計指針を整理します。

## 1. フェーズ定義

| フェーズ | 目的 | 主な入力 | 出力・副作用 | 完了条件 |
| --- | --- | --- | --- | --- |
| `survey` | 建築対象の調査と青写真の確定 | 建築指示、既存の設計テンプレート | `building_layout` と `building_material_requirements` の確定 | レイアウトと必要資材が揃う |
| `procurement` | 不足資材の調達計画を立案 | 要求資材、インベントリ、予約済み資材 | 調達タスクリスト（例: 採掘・伐採・クラフト指示） | すべての資材が予約済み or 在庫に十分存在 |
| `placement` | ブロックの配置順序を決定 | 建築レイアウト、チェックポイント（配置済み数） | 位置付きの配置バッチ | レイアウト全体を配置完了 |
| `inspection` | 完成状態の検査と仕上げ | 配置ログ、環境センサー | 状態報告、仕上げ作業（照明/装飾） | プレイヤー確認 or 自動検査で合格 |

### フェーズ遷移

- `survey → procurement`: レイアウトと資材要求が確定した時点で遷移。初期状態では
  `survey` を維持し、必要な情報が揃ったタイミングで `advance_building_state` が
  自動的に `procurement` へ切り替えます。
- `procurement → placement`: `plan_material_procurement` が不足ゼロを返した瞬間に遷移。
  これにより、資材不足が解消されるまで配置フェーズの実行を抑制します。`_synchronize_reserved_materials`
  が要求量と `inventory_summary` を突き合わせて予約数を丸めるため、LangGraph からの再開時にも矛盾しません。
- `placement → inspection`: `plan_block_placement` が空リストを返し、`placed_blocks` が
  レイアウト総数に達した際に遷移します。未配置ブロックがある場合は
  `placement` を維持します。
- ロールバック: 失敗フェーズが `placement` であれば `rollback_building_state` により
  `procurement` へ戻し、配置済みカウントを巻き戻します。`survey` まで戻る場合は
  資材予約も初期化し、設計見直しを想定します。

### ステートマシン図（文章表現）

```
survey --(資材要求確定)--> procurement --(不足なし)--> placement --(全配置完了)--> inspection
 \                                                                ^
  \--(情報不足)---------------(例外復旧)-------------------------/
```

## 2. LangGraph ノード設計指針

1. **純粋関数で計画を決定し、副作用ノードを分離する**
   - `python/services/building_service.py` の `advance_building_state` が、
     チェックポイント・資材情報・レイアウトを入力として `procurement_plan` と
     `placement_plan` を返します。LangGraph ノードではこの結果をバックログへ記録し、
     実際の Mineflayer 呼び出しは後段ノードに委譲してください。
2. **チェックポイントを常にメモリへ保存する**
   - `checkpoint_to_dict` を用いて `Memory` に保存し、次回のノード起動時に
     `restore_checkpoint` で復元します。LangGraph ノードの境界ごとにシリアライズした
     データのみを渡すことで、長期ジョブを安全に再開できます。
3. **資材不足の説明責務をノード側で担保する**
   - `plan_material_procurement` の結果が空でない場合は、どの資材が何個不足しているかを
     backlog へ追記し、プレイヤーと LLM が状況を共有できるようにしてください。
4. **配置バッチは小分けにして失敗時のロールバックを簡単にする**
   - `plan_block_placement` の `batch_size` を LangGraph ノード側で調整することで、
     失敗時に巻き戻すブロック数を抑制できます。Mineflayer 実装が揃うまでは
     デフォルト値（5 個）を維持するのが安全です。
5. **ロールバック戦略を常に用意する**
   - 例外が発生した場合は `rollback_building_state` を利用し、チェックポイントを一段階
     巻き戻してから再度 `advance_building_state` を呼び出します。これにより、
     途中まで進んだ配置でも安全にやり直せます。
6. **チェックポイント ID と構造化ログの一貫性を保つ**
   - `python/agent_orchestrator.py::handle_building` は `building_checkpoint_base_id` を
     `building:{plan_step}` 形式で生成し、`phase` と `placed_blocks` を付与した
     `checkpoint_id` を構造化ログ (`log_structured_event`) に記録します。復旧時は
     `event_level=recovery` を付けるため、ログ検索や OpenTelemetry でフェーズ毎の
     進捗が即座に追跡できます。
7. **backlog へ調達・配置内容を文字列で残す**
   - LangGraph の `backlog` には `procurement`／`placement` を
     `"oak_planks:12"` や `"oak_planks@10,65,3"` のような可読テキストで残し、
     Mineflayer で実装されていないアクションでもプレイヤーへ説明できる状態を
     維持します。

## 3. 実装メモ

- `python/agent_orchestrator.py` の `handle_building` ノードは、Memory に保存された
  `building_checkpoint` / `building_material_requirements` / `building_layout`
  / `inventory_summary` を `advance_building_state` に渡して計画を再構築します。
- backlog へは現在フェーズ、資材不足、配置予定バッチを文字列で追記し、未実装の
  下位アクションでもプレイヤーへ進捗説明が可能な状態を保ちます。
- Mineflayer 実装が追加された際は、`advance_building_state` が返す配置バッチを用いて
  実行ノードを増設するだけで済むように設計されています。
