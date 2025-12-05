// 日本語コメント：NavigationController の異常系挙動を集中的に検証するユニットテスト
// 役割：座標バリデーションと Bot 未接続時の扱い、強制移動記録のレート制御を安全に確認する
import { describe, expect, it, vi } from 'vitest';
import type { Bot } from 'mineflayer';
import type { Movements as MovementsClass } from 'mineflayer-pathfinder';

import { NavigationController } from '../runtime/navigationController.js';

/**
 * 実際の mineflayer へ依存せずに pathfinder を模倣するための極小モック。
 * コマンド実行フローが bot オブジェクトへアクセスした際にクラッシュしないよう、必要最低限の構造を持たせている。
 */
function createFakeBot(): Bot {
  const fakeMovements = createFakeMovements();
  return {
    entity: {
      position: { x: 0, y: 0, z: 0 },
    },
    pathfinder: {
      goto: vi.fn(),
      setMovements: vi.fn(),
      movements: fakeMovements,
    },
  } as unknown as Bot;
}

/**
 * Movements クラスの形状だけを満たす素朴なモック。
 * NavigationController が移動プロファイルを設定する際にも型エラーが発生しないようにするための保険として用意。
 */
function createFakeMovements(): MovementsClass {
  return {} as MovementsClass;
}

/**
 * テストごとに同じ設定値で NavigationController を初期化する補助関数。
 * 強制移動のリトライ閾値など、実装で利用する定数をテスト環境でも再現性高く扱うためにまとめている。
 */
function createController(): NavigationController {
  return new NavigationController({
    moveGoalToleranceMeters: 2,
    forcedMoveRetryWindowMs: 2_000,
    forcedMoveMaxRetries: 2,
    forcedMoveRetryDelayMs: 300,
    pathfinder: {
      allowParkour: true,
      allowSprinting: true,
      digCost: { enable: 1, disable: 96 },
    },
  });
}

describe('NavigationController recordForcedMove', () => {
  it('短時間に連続して記録された場合は二重ログを抑制する', () => {
    const controller = createController();

    const firstLog = controller.recordForcedMove(1_000);
    const secondLog = controller.recordForcedMove(1_500);
    const thirdLog = controller.recordForcedMove(2_200);

    expect(firstLog).toBe(true);
    expect(secondLog).toBe(false);
    expect(thirdLog).toBe(true);
  });
});

describe('NavigationController handleMoveToCommand (abnormal)', () => {
  it('無効な座標が渡された場合は即座に失敗し、Bot は参照しない', async () => {
    const controller = createController();
    const fakeBot = createFakeBot();
    const getActiveBot = vi.fn(() => fakeBot);

    const response = await controller.handleMoveToCommand({ x: 'nan', y: 2, z: 3 }, { getActiveBot });

    expect(response.ok).toBe(false);
    expect(response.error).toBe('Invalid coordinates');
    expect(getActiveBot).not.toHaveBeenCalled();
    expect(controller.getLastMoveTarget()).toBeNull();
  });

  it('Bot が未接続の場合は安全に拒否する', async () => {
    const controller = createController();
    const getActiveBot = vi.fn<() => Bot | null>(() => null);

    const response = await controller.handleMoveToCommand({ x: 1, y: 64, z: 1 }, { getActiveBot });

    expect(response.ok).toBe(false);
    expect(response.error).toBe('Bot is not connected to the Minecraft server yet');
    expect(controller.getLastMoveTarget()).toBeNull();
    expect(getActiveBot).toHaveBeenCalledTimes(1);
  });
});
