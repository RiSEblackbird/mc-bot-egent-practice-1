import { constants as fsConstants } from 'node:fs';
import { access, appendFile, mkdir } from 'node:fs/promises';
import { dirname } from 'node:path';
import type { Bot } from 'mineflayer';
import type { RegisteredSkill } from '../snapshots.js';
import type { CommandResponse } from '../types.js';

/**
 * skill 系コマンドの状態と永続化処理をまとめるコンテキスト。
 * 新規メンバーが迷わないよう、Bot 参照の取り回しや履歴ファイルの準備を一元化する。
 */
export interface SkillCommandContext {
  skillHistoryPath?: string;
  getActiveBot: () => Bot | null;
}

/**
 * skill コマンド用のハンドラ群を生成する。
 * 履歴ファイルの初期化やログ書き出しもこの関数が責務を持つ。
 */
export function createSkillCommandHandlers(context: SkillCommandContext) {
  const { skillHistoryPath, getActiveBot } = context;
  const skillRegistry = new Map<string, RegisteredSkill>();
  let skillHistoryInitialized = false;

  /**
   * skill 履歴の保存先を事前に確保する。書き込み権限を確認し、必要ならばディレクトリを作成する。
   */
  async function ensureSkillHistorySink(): Promise<void> {
    if (!skillHistoryPath || skillHistoryInitialized) {
      return;
    }
    try {
      await access(skillHistoryPath, fsConstants.F_OK);
      skillHistoryInitialized = true;
      return;
    } catch {
      try {
        await mkdir(dirname(skillHistoryPath), { recursive: true });
        await appendFile(skillHistoryPath, '');
        skillHistoryInitialized = true;
      } catch (error) {
        console.error('[SkillLog] failed to prepare history sink', error);
      }
    }
  }

  /**
   * skill 関連イベントを構造化ログとして出力し、必要に応じて履歴ファイルへも追記する。
   */
  function logSkillEvent(level: 'info' | 'warn' | 'error', event: string, contextPayload: Record<string, unknown>): void {
    const payload = {
      level,
      event,
      timestamp: new Date().toISOString(),
      context: contextPayload,
    };
    console.log(JSON.stringify(payload));

    if (!skillHistoryPath) {
      return;
    }
    ensureSkillHistorySink()
      .then(() => appendFile(skillHistoryPath, `${JSON.stringify(payload)}\n`))
      .catch((error) => console.error('[SkillLog] failed to append event', error));
  }

  /**
   * skill の登録要求を処理するハンドラ。入力検証と永続化を一括で扱う。
   */
  function handleRegisterSkillCommand(args: Record<string, unknown>): CommandResponse {
    const skillId = typeof args.skillId === 'string' ? args.skillId.trim() : '';
    const title = typeof args.title === 'string' ? args.title.trim() : '';
    const description = typeof args.description === 'string' ? args.description.trim() : '';
    const stepsRaw = Array.isArray(args.steps) ? args.steps : [];
    const steps: string[] = stepsRaw
      .filter((step): step is string => typeof step === 'string' && step.trim().length > 0)
      .map((step) => step.trim());
    const tagsRaw = Array.isArray(args.tags) ? args.tags : [];
    const tags: string[] = tagsRaw
      .filter((tag): tag is string => typeof tag === 'string' && tag.trim().length > 0)
      .map((tag) => tag.trim());

    if (!skillId || !title || !description || steps.length === 0) {
      return { ok: false, error: 'Invalid skill registration payload' };
    }

    const record: RegisteredSkill = {
      id: skillId,
      title,
      description,
      steps,
      tags,
      createdAt: Date.now(),
    };

    skillRegistry.set(skillId, record);
    logSkillEvent('info', 'skill.registered', {
      skillId,
      title,
      stepCount: steps.length,
      tags,
    });

    return { ok: true, data: { registered: true } };
  }

  /**
   * skill 再生要求を処理し、存在しない場合の警告や Bot への通知をまとめて行う。
   */
  function handleInvokeSkillCommand(args: Record<string, unknown>): CommandResponse {
    const skillId = typeof args.skillId === 'string' ? args.skillId.trim() : '';
    const contextHint = typeof args.context === 'string' ? args.context : '';

    if (!skillId) {
      return { ok: false, error: 'skillId is required' };
    }

    const record = skillRegistry.get(skillId);
    if (!record) {
      logSkillEvent('warn', 'skill.invoke.missing', { skillId, context: contextHint });
      return { ok: false, error: `Skill ${skillId} is not registered` };
    }

    logSkillEvent('info', 'skill.invoke', {
      skillId,
      title: record.title,
      context: contextHint,
      stepCount: record.steps.length,
    });

    const activeBot = getActiveBot();
    if (activeBot) {
      activeBot.chat(`[Skill] ${record.title} を再生します。登録ステップ数: ${record.steps.length}`);
    }

    return { ok: true, data: { steps: record.steps } };
  }

  /**
   * skill 探索要求を処理し、チャット通知とログ出力を一箇所にまとめる。
   */
  function handleSkillExploreCommand(args: Record<string, unknown>): CommandResponse {
    const skillId = typeof args.skillId === 'string' ? args.skillId.trim() : '';
    const description = typeof args.description === 'string' ? args.description.trim() : '';
    const contextHint = typeof args.context === 'string' ? args.context : '';

    if (!skillId || !description) {
      return { ok: false, error: 'Invalid exploration payload' };
    }

    logSkillEvent('info', 'skill.explore', {
      skillId,
      description,
      context: contextHint,
    });

    const activeBot = getActiveBot();
    if (activeBot) {
      activeBot.chat(`[Skill] ${skillId} の探索を開始します。ヒント: ${description}`);
    }

    return { ok: true, data: { exploring: skillId } };
  }

  return {
    handleRegisterSkillCommand,
    handleInvokeSkillCommand,
    handleSkillExploreCommand,
    ensureSkillHistorySink,
  };
}
