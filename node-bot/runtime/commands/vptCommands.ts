import type { Bot } from 'mineflayer';

import type {
  GeneralStatusSnapshot,
  VptAction,
  VptControlName,
  VptNavigationHint,
  VptObservationHotbarSlot,
  VptObservationSnapshot,
} from '../snapshots.js';
import type { CommandResponse } from '../types.js';
import { radToDeg } from '../perception/perceptionUtils.js';

export interface VptCommandContext {
  getActiveBot: () => Bot | null;
  vptCommandsEnabled: boolean;
  vptTickIntervalMs: number;
  vptMaxSequenceLength: number;
  buildGeneralStatusSnapshot: (targetBot: Bot) => GeneralStatusSnapshot;
  buildHotbarSnapshot: (targetBot: Bot) => VptObservationHotbarSlot[];
  computeNavigationHint: (targetBot: Bot) => VptNavigationHint | null;
}

const SUPPORTED_VPT_CONTROLS: readonly VptControlName[] = [
  'forward',
  'back',
  'left',
  'right',
  'jump',
  'sprint',
  'sneak',
  'attack',
  'use',
] as const;
const SUPPORTED_VPT_CONTROLS_SET = new Set<string>(SUPPORTED_VPT_CONTROLS);

/**
 * VPT 系コマンドをまとめ、依存注入でテストしやすい構造にしたハンドラ集約。
 */
export function createVptCommandHandlers(context: VptCommandContext) {
  const {
    getActiveBot,
    vptCommandsEnabled,
    vptTickIntervalMs,
    vptMaxSequenceLength,
    buildGeneralStatusSnapshot,
    buildHotbarSnapshot,
    computeNavigationHint,
  } = context;

  let isVptPlaybackActive = false;

  /**
   * VPT 観測用のスナップショットを生成し、Bot 未接続時には即座にエラーを返す。
   * DI で受け取ったステータス生成関数を用いることで、テスト時にモックを差し替えやすくする。
   */
  function handleGatherVptObservationCommand(args: Record<string, unknown>): CommandResponse {
    const activeBot = getActiveBot();

    if (!activeBot) {
      console.warn('[GatherVptObservation] rejected because bot is unavailable');
      return { ok: false, error: 'Bot is not connected to the Minecraft server yet' };
    }

    const entity = activeBot.entity;

    if (!entity) {
      return { ok: false, error: 'Bot entity is not initialized yet' };
    }

    const position = {
      x: Number(entity.position.x),
      y: Number(entity.position.y),
      z: Number(entity.position.z),
    };
    const velocity = entity.velocity ?? ({ x: 0, y: 0, z: 0 } as any);
    const yawDegrees = radToDeg(entity.yaw ?? 0);
    const pitchDegrees = radToDeg(entity.pitch ?? 0);
    const general = buildGeneralStatusSnapshot(activeBot);
    const hotbar = buildHotbarSnapshot(activeBot);
    const navigationHint = computeNavigationHint(activeBot);
    const heldItem = activeBot.heldItem ? activeBot.heldItem.displayName ?? activeBot.heldItem.name : null;

    const snapshot: VptObservationSnapshot = {
      position,
      velocity: { x: velocity.x, y: velocity.y, z: velocity.z },
      orientation: { yawDegrees, pitchDegrees },
      status: { health: general.health, food: general.food, saturation: general.saturation },
      onGround: Boolean(entity.onGround),
      hotbar,
      heldItem,
      navigationHint,
      timestamp: Date.now(),
      tickAge: Number(activeBot.time?.age ?? 0),
      dimension: activeBot.game.dimension ?? 'unknown',
    };

    return { ok: true, data: snapshot };
  }

  /**
   * 受け取った VPT アクション列をサニタイズしてから Mineflayer へ適用する。
   * 1 度に 1 シーケンスのみ再生することで、意図しない多重入力を防ぐ。
   */
  async function handlePlayVptActionsCommand(args: Record<string, unknown>): Promise<CommandResponse> {
    if (!vptCommandsEnabled) {
      return { ok: false, error: 'CONTROL_MODE=command のため VPT 再生は無効化されています。' };
    }

    const rawActions = args.actions;
    let sanitized: VptAction[];

    try {
      sanitized = sanitizeVptActions(rawActions);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      console.warn('[VPT] invalid payload', { message });
      return { ok: false, error: message };
    }

    if (sanitized.length === 0) {
      return { ok: true, data: { executed: 0 } };
    }

    if (sanitized.length > vptMaxSequenceLength) {
      return {
        ok: false,
        error: `actions length exceeds limit (${sanitized.length} > ${vptMaxSequenceLength})`,
      };
    }

    const activeBot = getActiveBot();
    if (!activeBot) {
      console.warn('[VPT] playback rejected because bot is unavailable');
      return { ok: false, error: 'Bot is not connected to the Minecraft server yet' };
    }

    if (isVptPlaybackActive) {
      return { ok: false, error: 'Another VPT playback is already in progress' };
    }

    const metadata = typeof args.metadata === 'object' && args.metadata !== null ? args.metadata : undefined;

    try {
      isVptPlaybackActive = true;
      await executeVptActionSequence(activeBot, sanitized, metadata);
      return { ok: true, data: { executed: sanitized.length } };
    } catch (error) {
      console.error('[VPT] failed to execute action sequence', error);
      return { ok: false, error: 'Failed to execute VPT action sequence' };
    } finally {
      isVptPlaybackActive = false;
    }
  }

  function sanitizeVptActions(rawActions: unknown): VptAction[] {
    if (!Array.isArray(rawActions)) {
      throw new Error('actions must be an array');
    }

    return rawActions.map((item, index) => sanitizeVptAction(item, index));
  }

  function sanitizeVptAction(raw: unknown, index: number): VptAction {
    if (typeof raw !== 'object' || raw === null) {
      throw new Error(`actions[${index}] must be an object`);
    }

    const record = raw as Record<string, unknown>;
    const kindRaw = typeof record.kind === 'string' ? record.kind.trim().toLowerCase() : '';

    if (!kindRaw) {
      throw new Error(`actions[${index}].kind is required`);
    }

    switch (kindRaw) {
      case 'control': {
        const controlRaw = typeof record.control === 'string' ? record.control.trim().toLowerCase() : '';
        if (!SUPPORTED_VPT_CONTROLS_SET.has(controlRaw)) {
          throw new Error(`actions[${index}].control '${controlRaw}' is not supported`);
        }
        if (typeof record.state !== 'boolean') {
          throw new Error(`actions[${index}].state must be a boolean`);
        }
        const state = record.state;
        const durationTicks = sanitizeDuration(record.durationTicks, index);
        return {
          kind: 'control',
          control: controlRaw as VptControlName,
          state,
          durationTicks,
        };
      }
      case 'look': {
        const yaw = Number(record.yaw);
        const pitch = Number(record.pitch ?? 0);
        if (!Number.isFinite(yaw) || !Number.isFinite(pitch)) {
          throw new Error(`actions[${index}] look.yaw/look.pitch must be numeric`);
        }
        const relative = record.relative !== undefined ? Boolean(record.relative) : false;
        const durationTicks = record.durationTicks !== undefined ? sanitizeDuration(record.durationTicks, index) : 0;
        return {
          kind: 'look',
          yaw,
          pitch,
          relative,
          ...(durationTicks > 0 ? { durationTicks } : {}),
        };
      }
      case 'wait': {
        const durationTicks = sanitizeDuration(record.durationTicks, index);
        return { kind: 'wait', durationTicks };
      }
      default:
        throw new Error(`actions[${index}].kind='${kindRaw}' is not supported`);
    }
  }

  function sanitizeDuration(raw: unknown, index: number): number {
    const value = Number(raw);
    if (!Number.isFinite(value) || value < 0) {
      throw new Error(`actions[${index}].durationTicks must be a non-negative number`);
    }
    return Math.round(value);
  }

  async function executeVptActionSequence(
    targetBot: Bot,
    actions: VptAction[],
    metadata?: Record<string, unknown>,
  ): Promise<void> {
    if (targetBot.pathfinder.isMoving()) {
      targetBot.pathfinder.stop();
    }

    targetBot.clearControlStates();

    const pressedControls = new Set<VptControlName>();

    console.log('[VPT] playback start', {
      actionCount: actions.length,
      metadata,
    });

    try {
      for (const action of actions) {
        switch (action.kind) {
          case 'control': {
            targetBot.setControlState(action.control, action.state);
            if (action.state) {
              pressedControls.add(action.control);
            } else {
              pressedControls.delete(action.control);
            }
            await waitTicks(action.durationTicks);
            break;
          }
          case 'look': {
            const entity = targetBot.entity;
            const yawRadians = degToRad(action.yaw);
            const pitchRadians = degToRad(action.pitch ?? 0);
            let targetYaw = yawRadians;
            let targetPitch = pitchRadians;
            if (action.relative && entity) {
              targetYaw = entity.yaw + yawRadians;
              targetPitch = entity.pitch + pitchRadians;
            }
            await targetBot.look(targetYaw, clampPitch(targetPitch), true);
            if (action.durationTicks && action.durationTicks > 0) {
              await waitTicks(action.durationTicks);
            }
            break;
          }
          case 'wait': {
            await waitTicks(action.durationTicks);
            break;
          }
          default: {
            const exhaustiveCheck: never = action;
            void exhaustiveCheck;
          }
        }
      }
    } finally {
      for (const control of pressedControls) {
        targetBot.setControlState(control, false);
      }
      targetBot.clearControlStates();
    }

    console.log('[VPT] playback completed', {
      actionCount: actions.length,
      metadata,
    });
  }

  function waitTicks(ticks: number): Promise<void> {
    const clamped = Math.max(0, Math.round(ticks));
    if (clamped <= 0) {
      return Promise.resolve();
    }
    return delay(clamped * vptTickIntervalMs);
  }

  return { handleGatherVptObservationCommand, handlePlayVptActionsCommand };
}

function clampPitch(radians: number): number {
  const minPitch = -Math.PI / 2;
  const maxPitch = Math.PI / 2;
  return Math.max(minPitch, Math.min(maxPitch, radians));
}

function degToRad(value: number): number {
  return (value * Math.PI) / 180;
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
