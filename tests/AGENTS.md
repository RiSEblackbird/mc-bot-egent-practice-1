# AGENTS.md

## 適用範囲

- このファイルは `tests/` 以下に適用する。
- このdirectoryは主にPython実装のtest置き場であり、`node-bot/tests/` と `bridge-plugin/src/test/` は各componentの近接ルールで管理する。
- Python実装との対応を確認する場合は `python/AGENTS.md` も読む。

## 作業進行

- 共通の進行、GitHub配送、公開安全性、完了報告はルート `AGENTS.md` と発動したtask Skillに従う。
- このファイルはPython test固有の実装規約と検証観点だけを正本化する。

## テスト構成

- `tests/`: Python unit test。
- `tests/integration/`: module間結合やadapter境界の検証。
- `tests/e2e/`: critical flowの高レベル確認。
- `tests/stubs/`: 外部依存やprotocolを置き換える補助stub。

## ルール

- 既定は `pytest` とし、既存の `unittest` caseは必要がない限り無理に書き換えない。
- 外部service、OpenAI API、Minecraft server、Bridge HTTPの実network呼び出しを避け、stub、fake、monkeypatchで再現する。
- 回帰testは内部実装より観測可能な挙動、公開契約、error signal、state transitionを優先する。
- async処理のtestは `pytest.mark.anyio` など既存patternに揃える。
- 時刻、乱数、network、外部API、port、DB namespaceなどの非決定要素を固定し、localとCIで同じ結果になるようにする。
- flaky testは再実行で隠さず、非同期、競合、待機条件、環境差の原因を修正する。一時skipする場合は理由、復旧条件、追跡先を残す。
- 重複する入力dataやtest doubleはこのdirectory配下へ集約し、同じ失敗scenarioを別々に再実装しない。

## 回帰テスト方針

- bug修正では、同じ入力、前提、境界値で再発を検知できるtestを原則として追加する。
- test名、fixture、入力、期待値から「何が壊れていて、何を守るか」が読める形にする。
- timeout、retry、fallback、部分失敗、空response、不正payloadなど壊れやすい条件を正常系と対で固定する。
- 再発防止に十分な最小layerを選び、単一関数で再現できる不具合をむやみにE2Eへ上げない。
- 仕様変更で期待値が変わる場合は、古い期待値を消すだけで終わらせず、新しい仕様を固定する。

## 変更時の着眼点

- planner / LangGraphでは成功pathだけでなくtimeout、invalid output、recovery pathを確認する。
- orchestrator / runtimeでは構造化logやbridge eventなど副次的signalも確認する。
- E2Eは価値の高い導線に絞り、unit / integrationで十分な場合はそちらを優先する。
