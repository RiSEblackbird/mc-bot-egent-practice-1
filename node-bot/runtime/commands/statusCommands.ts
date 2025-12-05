import type { Counter, Histogram } from '@opentelemetry/api';
import type { Bot } from 'mineflayer';
import type { Movements as MovementsClass } from 'mineflayer-pathfinder';
import type { Item } from 'prismarine-item';
import Vec3, { Vec3 as Vec3Type } from 'vec3';

import type { AgentBridge } from '../agentBridge.js';
import type { NavigationController } from '../navigationController.js';
import type { AgentRoleDescriptor } from '../roles.js';
import type { PerceptionBroadcastState } from '../services/telemetryBroadcast.js';
import type {
  DigPermissionSnapshot,
  EnvironmentSnapshot,
  GatherStatusKind,
  GeneralStatusSnapshot,
  HazardSummary,
  InventoryItemSnapshot,
  InventorySnapshot,
  LightingSummary,
  NearbyEntitySummary,
  NullableDurabilityValue,
  PerceptionSnapshot,
  PositionReference,
  PositionSnapshot,
  VptNavigationHint,
  VptObservationHotbarSlot,
  WeatherSummary,
} from '../snapshots.js';
import type { CommandResponse } from '../types.js';
import { EQUIP_TOOL_MATCHERS } from './equipItemCommand.js';

export interface StatusCommandContext {
  getActiveBot: () => Bot | null;
  navigationController: NavigationController;
  agentBridge: AgentBridge;
  perceptionBroadcastState: PerceptionBroadcastState;
  perceptionConfig: {
    entityRadius: number;
    blockRadius: number;
    blockHeight: number;
  };
  telemetry: {
    perceptionSnapshotHistogram: Histogram;
    perceptionErrorCounter: Counter;
  };
  getActiveAgentRole: () => AgentRoleDescriptor;
}

interface EnchantmentInfo {
  id: string;
  level: number;
}

type MutableMovements = NavigationController['getDigPermissiveMovements'] extends () => infer T
  ? T & { canDig?: boolean; digCost?: number }
  : MovementsClass & { canDig?: boolean; digCost?: number };

const ENCHANT_NAME_MAP: Record<string, string> = {
  efficiency: '効率強化',
  unbreaking: '耐久力',
  fortune: '幸運',
  silk_touch: 'シルクタッチ',
  mending: '修繕',
};

const ROMAN_NUMERALS = ['I', 'II', 'III', 'IV', 'V', 'VI', 'VII', 'VIII', 'IX', 'X'];

/**
 * ステータス関連コマンドとスナップショット生成を集約するファクトリ。
 * Bot 取得や計測の依存関係を DI で受け取り、テスト容易性を高める。
 */
export function createStatusCommandHandlers(context: StatusCommandContext) {
  const {
    getActiveBot,
    navigationController,
    agentBridge,
    perceptionBroadcastState,
    perceptionConfig,
    telemetry,
    getActiveAgentRole,
  } = context;
  const { entityRadius, blockRadius, blockHeight } = perceptionConfig;
  const { perceptionSnapshotHistogram, perceptionErrorCounter } = telemetry;

  /**
   * gatherStatus コマンドのエントリーポイント。Bot 未接続時の防御と入力正規化を一箇所にまとめる。
   */
  async function handleGatherStatusCommand(args: Record<string, unknown>): Promise<CommandResponse> {
    const kindRaw = typeof args.kind === 'string' ? args.kind.trim().toLowerCase() : '';
    const supportedKinds: GatherStatusKind[] = ['position', 'inventory', 'general', 'environment'];
    const normalizedKind = supportedKinds.find((candidate) => candidate === kindRaw) ?? null;

    if (!normalizedKind) {
      console.warn('[GatherStatusCommand] unsupported kind received', { kindRaw });
      return { ok: false, error: `Unsupported status kind: ${kindRaw || 'unknown'}` };
    }

    const activeBot = getActiveBot();

    if (!activeBot) {
      console.warn('[GatherStatusCommand] rejected because bot is unavailable');
      return { ok: false, error: 'Bot is not connected to the Minecraft server yet' };
    }

    switch (normalizedKind) {
      case 'position':
        return { ok: true, data: buildPositionSnapshot(activeBot) };
      case 'inventory':
        return { ok: true, data: buildInventorySnapshot(activeBot) };
      case 'general':
        return { ok: true, data: buildGeneralStatusSnapshot(activeBot) };
      case 'environment':
        return { ok: true, data: buildEnvironmentSnapshot(activeBot) };
      default: {
        const exhaustiveCheck: never = normalizedKind;
        void exhaustiveCheck;
        return { ok: false, error: 'Unsupported status kind' };
      }
    }
  }

  function buildPositionSnapshot(targetBot: Bot): PositionSnapshot {
    const { x, y, z } = targetBot.entity.position;
    const rounded = { x: Math.floor(x), y: Math.floor(y), z: Math.floor(z) };
    const dimension = targetBot.game.dimension ?? 'unknown';
    const formatted = `現在位置は X=${rounded.x} / Y=${rounded.y} / Z=${rounded.z}（ディメンション: ${dimension}）です。`;
    return { kind: 'position', position: rounded, dimension, formatted };
  }

  function buildInventorySnapshot(targetBot: Bot): InventorySnapshot {
    const rawItems = targetBot.inventory.items();
    const totalSlots = targetBot.inventory.slots.length;
    const occupiedSlots = rawItems.length;
    const items = rawItems.map((item) => createInventoryItemSnapshot(item));
    const pickaxeItems = rawItems.filter((item) => EQUIP_TOOL_MATCHERS.pickaxe(item));
    const pickaxes = pickaxeItems.map((item) => createInventoryItemSnapshot(item));

    const pickaxeSummaries = pickaxes.map((item) => formatInventoryItemSummary(item));
    const torchCount = rawItems
      .filter((item) => item.name === 'torch')
      .reduce((acc, item) => acc + item.count, 0);

    const base = `所持品は ${occupiedSlots}/${totalSlots} スロットを使用中`;
    const pickaxeSegment = pickaxeSummaries.length > 0 ? `主要ツルハシ: ${pickaxeSummaries.join('、')}` : 'ツルハシは所持していません';
    const torchSegment = torchCount > 0 ? `松明: ${torchCount} 本` : '松明は未所持';
    const formatted = `${base}。${pickaxeSegment}。${torchSegment}。`;

    return {
      kind: 'inventory',
      occupiedSlots,
      totalSlots,
      items,
      pickaxes,
      formatted,
    };
  }

  function createInventoryItemSnapshot(item: Item): InventoryItemSnapshot {
    const maxDurability = resolveDurabilityValue((item as Record<string, unknown>).maxDurability);
    const durabilityUsed = resolveDurabilityValue((item as Record<string, unknown>).durabilityUsed);
    const directDurability = resolveDurabilityValue((item as Record<string, unknown>).durability);
    const durability =
      directDurability ??
      (maxDurability !== null && durabilityUsed !== null ? Math.max(0, maxDurability - durabilityUsed) : null);

    return {
      slot: item.slot,
      name: item.name,
      displayName: item.displayName,
      count: item.count,
      enchantments: describeEnchantments(item),
      maxDurability,
      durabilityUsed,
      durability,
    };
  }

  function resolveDurabilityValue(value: unknown): NullableDurabilityValue {
    return typeof value === 'number' && Number.isFinite(value) ? value : null;
  }

  function buildGeneralStatusSnapshot(targetBot: Bot): GeneralStatusSnapshot {
    const health = Math.round(targetBot.health);
    const rawMaxHealth = Number((targetBot as Record<string, unknown>).maxHealth ?? 20);
    const maxHealth = Number.isFinite(rawMaxHealth) ? rawMaxHealth : 20;
    const food = Math.round(targetBot.food);
    const rawSaturation = Number(targetBot.foodSaturation ?? 0);
    const saturation = Number.isFinite(rawSaturation) ? Math.round(rawSaturation * 10) / 10 : 0;
    const oxygenLevel = Math.round(targetBot.oxygenLevel);
    const digPermission = evaluateDigPermission(targetBot);

    const formatted = `体力: ${health}/${maxHealth}、満腹度: ${food}/20、飽和度: ${saturation.toFixed(1)}、採掘許可: ${digPermission.allowed ? 'あり' : `なし（${digPermission.reason}）`}。`;

    return {
      kind: 'general',
      health,
      maxHealth,
      food,
      saturation,
      oxygenLevel,
      digPermission,
      agentRole: getActiveAgentRole(),
      formatted,
      perception: samplePerceptionSnapshot(targetBot),
    };
  }

  function buildEnvironmentSnapshot(targetBot: Bot): EnvironmentSnapshot {
    return {
      kind: 'environment',
      perception: samplePerceptionSnapshot(targetBot, 'environment-status'),
      role: getActiveAgentRole(),
      eventQueueSize: agentBridge.getQueueSize(),
    };
  }

  function clonePerceptionSnapshot(snapshot: PerceptionSnapshot | null): PerceptionSnapshot | null {
    if (!snapshot) {
      return null;
    }
    return JSON.parse(JSON.stringify(snapshot)) as PerceptionSnapshot;
  }

  function samplePerceptionSnapshot(targetBot: Bot, reason: string = 'general-status'): PerceptionSnapshot | null {
    const snapshot = buildPerceptionSnapshotSafe(targetBot, reason);
    if (snapshot) {
      perceptionBroadcastState.lastSnapshot = snapshot;
      return clonePerceptionSnapshot(snapshot);
    }
    return clonePerceptionSnapshot(perceptionBroadcastState.lastSnapshot);
  }

  function buildPerceptionSnapshotSafe(targetBot: Bot, reason: string): PerceptionSnapshot | null {
    const startedAt = Date.now();
    try {
      const snapshot = buildPerceptionSnapshot(targetBot);
      perceptionSnapshotHistogram.record(Date.now() - startedAt, {
        'perception.reason': reason,
        'perception.dimension': snapshot.position.dimension,
      });
      return snapshot;
    } catch (error) {
      perceptionErrorCounter.add(1, { 'perception.reason': reason });
      console.warn('[Perception] failed to build snapshot', error);
      return null;
    }
  }

  function buildPerceptionSnapshot(targetBot: Bot): PerceptionSnapshot {
    const entity = targetBot.entity;
    if (!entity) {
      throw new Error('Bot entity is not initialized');
    }
    const floored = entity.position.clone().floored();
    const dimension = targetBot.game.dimension ?? 'unknown';
    const weather = resolveWeatherSummary(targetBot);
    const timeInfo = resolveTimeSummary(targetBot);
    const lighting = resolveLightingSummary(targetBot, floored);
    const nearbyEntities = scanNearbyEntities(targetBot, entity.position);
    const hazards = scanHazardsAround(targetBot, floored);
    const warnings = [...hazards.warnings, ...resolveLightingWarnings(lighting), ...resolveEntityWarnings(nearbyEntities)];
    const summary = buildPerceptionSummary(nearbyEntities, hazards, weather, lighting);

    return {
      kind: 'perception',
      timestamp: Date.now(),
      position: { x: floored.x, y: floored.y, z: floored.z, dimension },
      health: Math.round(targetBot.health),
      food_level: Math.round(targetBot.food),
      weather,
      time: timeInfo,
      lighting,
      hazards,
      nearby_entities: nearbyEntities,
      warnings,
      summary,
    };
  }

  function resolveWeatherSummary(targetBot: Bot): WeatherSummary {
    const rainLevel = Number((targetBot as Record<string, unknown>).rainLevel ?? 0);
    const thunderLevel = Number((targetBot as Record<string, unknown>).thunderLevel ?? 0);
    const isRainingFlag = Boolean((targetBot as Record<string, unknown>).isRaining ?? rainLevel > 0);
    const label = isRainingFlag ? (thunderLevel > 0 ? 'thunder' : 'rain') : 'clear';
    return {
      isRaining: isRainingFlag,
      rainLevel,
      thunderLevel,
      label,
    };
  }

  function resolveTimeSummary(targetBot: Bot) {
    const time = targetBot.time ?? { age: 0, day: 0, timeOfDay: 0 };
    const timeOfDay = Number(time.timeOfDay ?? 0);
    const age = Number(time.age ?? 0);
    const day = Number(time.day ?? 0);
    const isDay = timeOfDay >= 0 && timeOfDay < 12_000;
    return {
      age,
      day,
      timeOfDay,
      isDay,
    };
  }

  function resolveLightingSummary(targetBot: Bot, position: Vec3Type): LightingSummary {
    const world = (targetBot as Record<string, any>).world;
    const readLevel = (method: 'getSkyLight' | 'getBlockLight'): number | null => {
      if (world && typeof world[method] === 'function') {
        try {
          return world[method](position);
        } catch {
          return null;
        }
      }
      return null;
    };

    return {
      sky: readLevel('getSkyLight'),
      block: readLevel('getBlockLight'),
    };
  }

  function resolveLightingWarnings(lighting: LightingSummary): string[] {
    const warnings: string[] = [];
    if (typeof lighting.block === 'number' && lighting.block < 7) {
      warnings.push(`周囲の明るさが低く敵対モブが湧きやすい状態です (block=${lighting.block})`);
    }
    return warnings;
  }

  function resolveEntityWarnings(nearbyEntities: PerceptionSnapshot['nearby_entities']): string[] {
    if (nearbyEntities.hostiles <= 0) {
      return [];
    }
    const labels = nearbyEntities.details
      .filter((entity) => entity.kind === 'hostile')
      .slice(0, 3)
      .map((entity) => `${entity.name}(${entity.distance.toFixed(1)}m${entity.bearing})`);
    return [`敵対モブを検知: ${labels.join('、')}`];
  }

  function buildPerceptionSummary(
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

  function scanNearbyEntities(targetBot: Bot, origin: Vec3Type): PerceptionSnapshot['nearby_entities'] {
    const hostiles: NearbyEntitySummary[] = [];
    const allDetails: NearbyEntitySummary[] = [];
    const players: NearbyEntitySummary[] = [];
    const originVec = origin.clone();

    for (const entity of Object.values(targetBot.entities)) {
      if (!entity || !entity.position || entity === targetBot.entity) {
        continue;
      }
      const distance = entity.position.distanceTo(originVec);
      if (!Number.isFinite(distance) || distance > entityRadius) {
        continue;
      }
      const dx = entity.position.x - originVec.x;
      const dz = entity.position.z - originVec.z;
      const bearing = resolveBearingLabel(dx, dz);
      const detail: NearbyEntitySummary = {
        name: entity.displayName ?? entity.name ?? entity.uuid ?? 'unknown',
        kind: classifyEntityKind(entity),
        distance,
        bearing,
        position: {
          x: Math.floor(entity.position.x),
          y: Math.floor(entity.position.y),
          z: Math.floor(entity.position.z),
        },
      };
      allDetails.push(detail);
      if (detail.kind === 'hostile') {
        hostiles.push(detail);
      } else if (detail.kind === 'player') {
        players.push(detail);
      }
    }

    allDetails.sort((a, b) => a.distance - b.distance);
    return {
      total: allDetails.length,
      hostiles: hostiles.length,
      players: players.length,
      details: allDetails.slice(0, 5),
    };
  }

  function classifyEntityKind(entity: any): string {
    const type = (entity?.type ?? '').toString().toLowerCase();
    const kind = (entity?.kind ?? '').toString().toLowerCase();
    if (type === 'player') {
      return 'player';
    }
    if (kind.includes('hostile')) {
      return 'hostile';
    }
    if (kind.includes('passive')) {
      return 'passive';
    }
    return 'other';
  }

  function scanHazardsAround(targetBot: Bot, center: Vec3Type): HazardSummary {
    const radius = blockRadius;
    const height = blockHeight;
    let liquids = 0;
    let lava = 0;
    let magma = 0;
    let voids = 0;
    let closestLiquid: PositionReference | null = null;
    let closestVoid: PositionReference | null = null;

    for (let dx = -radius; dx <= radius; dx++) {
      for (let dy = -height; dy <= height; dy++) {
        for (let dz = -radius; dz <= radius; dz++) {
          const checkPos = new Vec3(center.x + dx, center.y + dy, center.z + dz);
          let block: any = null;
          try {
            block = targetBot.blockAt(checkPos, true);
          } catch {
            block = null;
          }
          if (!block) {
            continue;
          }
          const name = String(block.name ?? '');
          const distance = Math.sqrt(dx * dx + dy * dy + dz * dz);
          const reference: PositionReference = {
            x: checkPos.x,
            y: checkPos.y,
            z: checkPos.z,
            distance,
            bearing: resolveBearingLabel(dx, dz),
          };

          if (block.liquid || name.includes('water') || name.includes('lava')) {
            liquids += 1;
            if (name.includes('lava')) {
              lava += 1;
            }
            if (!closestLiquid || reference.distance < closestLiquid.distance) {
              closestLiquid = reference;
            }
          }
          if (name === 'magma_block') {
            magma += 1;
          }
          if ((block.boundingBox === 'empty' || name.includes('air')) && dy < 0) {
            const below = new Vec3(checkPos.x, checkPos.y - 1, checkPos.z);
            let belowBlock: any = null;
            try {
              belowBlock = targetBot.blockAt(below, true);
            } catch {
              belowBlock = null;
            }
            if (!belowBlock || belowBlock.boundingBox === 'empty') {
              voids += 1;
              if (!closestVoid || reference.distance < closestVoid.distance) {
                closestVoid = reference;
              }
            }
          }
        }
      }
    }

    const warnings: string[] = [];
    if (liquids > 0) {
      warnings.push('周囲に液体を検知しました');
    }
    if (voids > 0) {
      warnings.push('足元に空洞が存在します');
    }

    return {
      liquids,
      lava,
      magma,
      voids,
      warnings,
      closestLiquid,
      closestVoid,
    };
  }

  function resolveBearingLabel(dx: number, dz: number): string {
    const angle = (Math.atan2(-dx, dz) * 180) / Math.PI;
    const normalized = (angle + 360) % 360;
    if (normalized >= 337.5 || normalized < 22.5) return '北';
    if (normalized >= 22.5 && normalized < 67.5) return '北東';
    if (normalized >= 67.5 && normalized < 112.5) return '東';
    if (normalized >= 112.5 && normalized < 157.5) return '南東';
    if (normalized >= 157.5 && normalized < 202.5) return '南';
    if (normalized >= 202.5 && normalized < 247.5) return '南西';
    if (normalized >= 247.5 && normalized < 292.5) return '西';
    return '北西';
  }

  function evaluateDigPermission(targetBot: Bot): DigPermissionSnapshot {
    const gameMode = targetBot.game.gameMode ?? 'survival';
    const fallbackMovements = navigationController.getDigPermissiveMovements() as MutableMovements | null;
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

  function describeEnchantments(item: Item): string[] {
    return extractEnchantments(item).map((entry) => {
      const shortId = entry.id.replace(/^minecraft:/, '');
      const label = ENCHANT_NAME_MAP[shortId] ?? shortId;
      const levelIndex = Math.max(0, Math.min(ROMAN_NUMERALS.length - 1, entry.level - 1));
      const roman = entry.level >= 1 && entry.level <= ROMAN_NUMERALS.length ? ROMAN_NUMERALS[levelIndex] : String(entry.level);
      return `${label} ${roman}`;
    });
  }

  function extractEnchantments(item: Item): EnchantmentInfo[] {
    const result: EnchantmentInfo[] = [];
    const nbt = item.nbt as any;
    if (!nbt?.value) {
      return result;
    }

    const enchantList = nbt.value.Enchantments ?? nbt.value.enchantments;
    const entries = enchantList?.value;

    if (!Array.isArray(entries)) {
      return result;
    }

    for (const entry of entries) {
      const idValue = typeof entry?.id?.value === 'string' ? entry.id.value : typeof entry?.id === 'string' ? entry.id : null;
      const levelValueRaw = entry?.lvl?.value ?? entry?.lvl;
      const levelValue = Number(levelValueRaw);
      if (!idValue || !Number.isFinite(levelValue)) {
        continue;
      }
      result.push({ id: idValue, level: levelValue });
    }

    return result;
  }

  function formatInventoryItemSummary(item: InventoryItemSnapshot): string {
    if (!item.enchantments.length) {
      return `${item.displayName} x${item.count}`;
    }

    return `${item.displayName} x${item.count}（${item.enchantments.join('、')}）`;
  }

  function computeNavigationHint(targetBot: Bot): VptNavigationHint | null {
    const lastMoveTarget = navigationController.getLastMoveTarget();
    if (!lastMoveTarget) {
      return null;
    }

    const entity = targetBot.entity;
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

  function radToDeg(value: number): number {
    return (value * 180) / Math.PI;
  }

  return {
    handleGatherStatusCommand,
    buildGeneralStatusSnapshot,
    buildHotbarSnapshot,
    computeNavigationHint,
    buildPerceptionSnapshotSafe,
  };
}

function buildHotbarSnapshot(targetBot: Bot): VptObservationHotbarSlot[] {
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
