# Agent Task Prompt Template

AI agent に UI/UX 作業を依頼するときに使う。

```md
あなたは `AGENTS.md` で管理されているリポジトリで作業しています。

Task:
[タスクを書く]

Requirements:
- 最初に `AGENTS.md` を読む。
- UI/UX に触れる場合、利用可能なら `ui-ux-review` skill を使う。
- `docs/ai-governance/02-uiux-review-framework.md` と `docs/ai-governance/03-evidence-and-completion-gates.md` を読む。
- Cursor rules を作らない。
- 既存の非衝突 project policy を保持する場合を除き、`CLAUDE.md` は `@AGENTS.md` だけにする。
- state matrix、novice simulation、accessibility review、visual hierarchy review、counter-review、completion gate report を作る。
- 実行していない verification を実行済みと主張しない。

Final report must include:
- files changed
- P0/P1/P2 findings
- evidence
- tests run
- tests not run
- remaining risk
```
