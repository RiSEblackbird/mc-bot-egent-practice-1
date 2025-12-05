import type { Bot } from 'mineflayer';
import { goals } from 'mineflayer-pathfinder';
import type { Movements as MovementsClass } from 'mineflayer-pathfinder';
import type { CommandResponse } from './types.js';

/**
 * Mineflayer の移動系処理を集約し、bot.ts のコンテキストサイズを抑制するコントローラー。
 *
 * - 強制移動検知に伴うリトライ判定
 * - 移動プロファイル（慎重/掘削許可）の管理
 * - moveTo コマンドの実装本体
 */
export class NavigationController {
  private lastMoveTarget: { x: number; y: number; z: number } | null = null;
  private lastForcedMoveAt = 0;
  private lastForcedMoveLoggedAt = 0;
  private cautiousMovements: MovementsClass | null = null;
  private digPermissiveMovements: MovementsClass | null = null;

  constructor(
    private readonly options: {
      moveGoalToleranceMeters: number;
      forcedMoveRetryWindowMs: number;
      forcedMoveMaxRetries: number;
      forcedMoveRetryDelayMs: number;
      // mineflayer-pathfinder の挙動を環境変数経由で注入し、bot.ts 側のハードコーディングを回避する。
      pathfinder: {
        allowParkour: boolean;
        allowSprinting: boolean;
        digCost: { enable: number; disable: number };
      };
    },
  ) {}

  setMovementProfiles(cautious: MovementsClass, digPermissive: MovementsClass): void {
    this.cautiousMovements = cautious;
    this.digPermissiveMovements = digPermissive;
  }

  getCautiousMovements(): MovementsClass | null {
    return this.cautiousMovements;
  }

  getDigPermissiveMovements(): MovementsClass | null {
    return this.digPermissiveMovements;
  }

  getLastMoveTarget(): { x: number; y: number; z: number } | null {
    return this.lastMoveTarget;
  }

  recordMoveTarget(target: { x: number; y: number; z: number }): void {
    this.lastMoveTarget = target;
  }

  /**
   * forcedMove イベント発火時にタイムスタンプを記録し、追加ログ出力が必要か返す。
   */
  recordForcedMove(now: number): boolean {
    this.lastForcedMoveAt = now;
    const shouldLog = now - this.lastForcedMoveLoggedAt >= 1_000;
    if (shouldLog) {
      this.lastForcedMoveLoggedAt = now;
    }
    return shouldLog;
  }

  configureMovementProfile(movements: MovementsClass, allowDigging: boolean): void {
    const mutable = movements as MovementsClass & {
      canDig?: boolean;
      digCost?: number;
      allowParkour?: boolean;
      allowSprinting?: boolean;
    };
    mutable.allowParkour = this.options.pathfinder.allowParkour;
    mutable.allowSprinting = this.options.pathfinder.allowSprinting;
    mutable.canDig = allowDigging;

    if (allowDigging) {
      mutable.digCost = this.options.pathfinder.digCost.enable;
      return;
    }

    const currentCost = mutable.digCost ?? this.options.pathfinder.digCost.enable;
    mutable.digCost = Math.max(currentCost, this.options.pathfinder.digCost.disable);
  }

  private resolveGoalNearTolerance(targetBot: Bot, target: { x: number; y: number; z: number }): number {
    const entity = targetBot.entity;

    if (!entity) {
      return this.options.moveGoalToleranceMeters;
    }

    const verticalGap = Math.abs(target.y - entity.position.y);

    if (verticalGap >= 2) {
      const tightenedTolerance = Math.min(this.options.moveGoalToleranceMeters, 1);
      return Math.max(1, tightenedTolerance);
    }

    return this.options.moveGoalToleranceMeters;
  }

  private shouldRetryDueToForcedMove(error: unknown): boolean {
    if (Date.now() - this.lastForcedMoveAt > this.options.forcedMoveRetryWindowMs) {
      return false;
    }

    const message = error instanceof Error ? error.message : String(error);
    return message.includes('GoalChanged');
  }

  private isNoPathError(error: unknown): boolean {
    const message = error instanceof Error ? error.message : String(error);
    return message.toLowerCase().includes('no path');
  }

  async gotoWithForcedMoveRetry(
    targetBot: Bot,
    goal: InstanceType<typeof goals.GoalNear>,
    movements: MovementsClass,
  ): Promise<void> {
    const { pathfinder: activePathfinder } = targetBot;
    const previousMovements = activePathfinder.movements;
    const shouldRestoreMovements = previousMovements !== movements;

    if (shouldRestoreMovements) {
      activePathfinder.setMovements(movements);
    }

    try {
      for (let attempt = 0; attempt <= this.options.forcedMoveMaxRetries; attempt++) {
        try {
          await activePathfinder.goto(goal);
          return;
        } catch (error) {
          if (this.shouldRetryDueToForcedMove(error) && attempt < this.options.forcedMoveMaxRetries) {
            console.warn(
              `[MoveToCommand] retrying due to forcedMove correction (attempt ${attempt + 1}/${this.options.forcedMoveMaxRetries})`,
            );
            await this.delay(this.options.forcedMoveRetryDelayMs);
            continue;
          }

          throw error;
        }
      }
    } finally {
      if (shouldRestoreMovements) {
        activePathfinder.setMovements(previousMovements);
      }
    }

    throw new Error('Pathfinding failed after forcedMove retries');
  }

  async handleMoveToCommand(
    args: Record<string, unknown>,
    dependencies: { getActiveBot: () => Bot | null },
  ): Promise<CommandResponse> {
    const x = Number(args.x);
    const y = Number(args.y);
    const z = Number(args.z);

    if ([x, y, z].some((value) => Number.isNaN(value))) {
      console.warn('[MoveToCommand] invalid coordinate(s) detected', { x, y, z });
      return { ok: false, error: 'Invalid coordinates' };
    }

    const activeBot = dependencies.getActiveBot();

    if (!activeBot) {
      console.warn('[MoveToCommand] rejected because bot is unavailable');
      return { ok: false, error: 'Bot is not connected to the Minecraft server yet' };
    }

    this.recordMoveTarget({ x, y, z });
    const tolerance = this.resolveGoalNearTolerance(activeBot, { x, y, z });
    const goal = new goals.GoalNear(x, y, z, tolerance);
    const preferredMovements = this.cautiousMovements ?? activeBot.pathfinder.movements;
    const fallbackMovements = this.digPermissiveMovements;

    try {
      await this.gotoWithForcedMoveRetry(activeBot, goal, preferredMovements);
      const { position } = activeBot.entity;
      console.log(
        `[MoveToCommand] pathfinder completed near (${x}, ${y}, ${z}) actual=(${position.x.toFixed(2)}, ${position.y.toFixed(2)}, ${position.z.toFixed(2)}) tolerance=${tolerance} profile=cautious`,
      );
      return { ok: true };
    } catch (primaryError) {
      if (this.isNoPathError(primaryError) && fallbackMovements) {
        console.warn(
          '[MoveToCommand] no walkable route found without digging. Retrying with digging-enabled fallback profile.',
        );

        try {
          await this.gotoWithForcedMoveRetry(activeBot, goal, fallbackMovements);
          const { position } = activeBot.entity;
          console.log(
            `[MoveToCommand] fallback pathfinder completed near (${x}, ${y}, ${z}) actual=(${position.x.toFixed(2)}, ${position.y.toFixed(2)}, ${position.z.toFixed(2)}) tolerance=${tolerance} profile=dig-enabled`,
          );
          return { ok: true };
        } catch (fallbackError) {
          console.error('[Pathfinder] dig-enabled fallback also failed', fallbackError);
          return { ok: false, error: 'Pathfinding failed' };
        }
      }

      console.error('[Pathfinder] failed to move', primaryError);
      return { ok: false, error: 'Pathfinding failed' };
    }
  }

  private delay(ms: number): Promise<void> {
    return new Promise((resolve) => {
      setTimeout(resolve, ms);
    });
  }
}
