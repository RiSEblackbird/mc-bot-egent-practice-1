import type { Bot } from 'mineflayer';
import type { Movements as MovementsClass } from 'mineflayer-pathfinder';
import type { Item } from 'prismarine-item';

import type {
  DigPermissionSnapshot,
  HazardSummary,
  LightingSummary,
  PerceptionSnapshot,
  VptNavigationHint,
  VptObservationHotbarSlot,
  WeatherSummary,
} from '../snapshots.js';

/**
 * mineflayer Bot から観測・移動系のスナップショットを生成するための純粋ユーティリティ集。
 * 依存注入前提の関数にしておくことで、ナビゲーションコントローラの内部状態に依存せずテストしやすくする。
 */
export type NavigationTarget = { x: number; y: number; z: number } | null;

export type MovementsWithDig = MovementsClass & { canDig?: boolean; digCost?: number };

export interface DigPermissionContext {
  gameMode: string;
  fallbackMovements: MovementsWithDig | null;
}

export interface NavigationHintContext {
  bot: Bot;
  lastMoveTarget: NavigationTarget;
}

/**
 * ナビゲーション目標と Bot の現在位置から、VPT 用の向きと距離ヒントを計算する。
 */
export function computeNavigationHint(context: NavigationHintContext): VptNavigationHint | null {
  const { bot, lastMoveTarget } = context;

  if (!lastMoveTarget) {
    return null;
  }

  const entity = bot.entity;
  if (!entity) {
    return null;
  }

  const dx = lastMoveTarget.x + 0.5 - entity.position.x;
  const dz = lastMoveTarget.z + 0.5 - entity.position.z;
  const horizontalDistance = Math.sqrt(dx * dx + dz * dz);
  const verticalOffset = lastMoveTarget.y - entity.position.y;
  const targetYawRadians = Math.atan2(-dx, dz);
  const targetYawDegrees = radToDeg(targetYawRadians);

  return {
    targetYawDegrees,
    horizontalDistance,
    verticalOffset,
  };
}

/**
 * 現在のゲームモードと移動プロファイルから、ブロック破壊の許可状況を判定する。
 */
export function evaluateDigPermission(context: DigPermissionContext): DigPermissionSnapshot {
  const { gameMode, fallbackMovements } = context;
  const fallbackMovementInitialized = Boolean(fallbackMovements);
  const fallbackAllowsDig = Boolean(fallbackMovements?.canDig);
  const gameModeAllows = !['adventure', 'spectator'].includes(gameMode);
  const allowed = gameModeAllows && fallbackAllowsDig;

  let reason = '掘削許可付きの移動プロファイルを利用可能です';
  if (!gameModeAllows) {
    reason = `ゲームモード ${gameMode} ではブロック破壊が制限されています`;
  } else if (!fallbackMovementInitialized) {
    reason = '掘削許可付きの移動プロファイルがまだ初期化されていません';
  } else if (!fallbackAllowsDig) {
    reason = '現在の移動プロファイルでは canDig が無効化されています';
  }

  return {
    allowed,
    gameMode,
    fallbackMovementInitialized,
    reason,
  };
}

/**
 * ホットバーの 9 スロットの状態をシリアライズして返す。
 */
export function buildHotbarSnapshot(targetBot: Bot): VptObservationHotbarSlot[] {
  const slots: VptObservationHotbarSlot[] = [];
  for (let index = 0; index < 9; index++) {
    const slotIndex = 36 + index;
    const item = targetBot.inventory.slots[slotIndex] as Item | null;
    if (item) {
      slots.push({
        slot: slotIndex,
        name: item.name,
        displayName: item.displayName ?? item.name,
        count: item.count,
      });
      continue;
    }

    slots.push({ slot: slotIndex, name: '', displayName: '', count: 0 });
  }
  return slots;
}

/**
 * 明るさに関するリスクを検知し、警告メッセージの配列を返す。
 */
export function resolveLightingWarnings(lighting: LightingSummary): string[] {
  const warnings: string[] = [];
  if (typeof lighting.block === 'number' && lighting.block < 7) {
    warnings.push(`周囲の明るさが低く敵対モブが湧きやすい状態です (block=${lighting.block})`);
  }
  return warnings;
}

/**
 * 敵対モブの存在をわかりやすく伝えるための警告メッセージを生成する。
 */
export function resolveEntityWarnings(nearbyEntities: PerceptionSnapshot['nearby_entities']): string[] {
  if (nearbyEntities.hostiles <= 0) {
    return [];
  }
  const labels = nearbyEntities.details
    .filter((entity) => entity.kind === 'hostile')
    .slice(0, 3)
    .map((entity) => `${entity.name}(${entity.distance.toFixed(1)}m${entity.bearing})`);
  return [`敵対モブを検知: ${labels.join('、')}`];
}

/**
 * 周辺環境のサマリー文字列を組み立て、コンテキスト表示に活用する。
 */
export function buildPerceptionSummary(
  nearbyEntities: PerceptionSnapshot['nearby_entities'],
  hazards: HazardSummary,
  weather: WeatherSummary,
  lighting: LightingSummary,
): string {
  const parts: string[] = [];
  if (nearbyEntities.hostiles > 0) {
    parts.push(`敵対モブ${nearbyEntities.hostiles}体`);
  }
  if (hazards.liquids > 0) {
    parts.push(`液体${hazards.liquids}`);
  }
  if (hazards.voids > 0) {
    parts.push(`落下リスク${hazards.voids}`);
  }
  parts.push(`天候:${weather.label}`);
  if (typeof lighting.block === 'number') {
    parts.push(`明るさ:${lighting.block}`);
  }
  return parts.join(' / ');
}

/**
 * ラジアンを度数へ変換するシンプルなユーティリティ。
 */
export function radToDeg(value: number): number {
  return (value * 180) / Math.PI;
}
