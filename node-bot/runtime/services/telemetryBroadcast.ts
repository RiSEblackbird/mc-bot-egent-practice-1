import type { Bot } from 'mineflayer';

import { AgentBridge } from '../agentBridge.js';
import type { AgentRoleDescriptor } from '../roles.js';
import type { PerceptionSnapshot } from '../snapshots.js';

export type PerceptionBroadcastState = {
  lastSnapshot: PerceptionSnapshot | null;
  lastBroadcastAt: number;
};

/**
 * 知覚情報のキャッシュ状態を初期化するためのヘルパー。
 * Bot のライフサイクルと紐づくため、呼び出し元で 1 インスタンスを共有することを前提としている。
 */
export function createPerceptionBroadcastState(): PerceptionBroadcastState {
  return {
    lastSnapshot: null,
    lastBroadcastAt: 0,
  };
}

/**
 * 体力や満腹度などの基礎ステータスを AgentBridge 経由で通知する。
 * Bot インスタンスと橋渡しサービスの双方を引数で受け取り、Node 側の依存を局所化する。
 */
export async function broadcastAgentStatus(params: {
  targetBot: Bot;
  agentBridge: AgentBridge;
  primaryAgentId: string;
  getActiveAgentRole: () => AgentRoleDescriptor;
  extraPayload?: Record<string, unknown>;
}): Promise<void> {
  const { targetBot, agentBridge, primaryAgentId, getActiveAgentRole, extraPayload = {} } = params;
  const health = Math.round(targetBot.health);
  const rawMaxHealth = Number((targetBot as Record<string, unknown>).maxHealth ?? 20);
  const maxHealth = Number.isFinite(rawMaxHealth) ? rawMaxHealth : 20;
  const food = Math.round(targetBot.food);
  const saturation = Number.isFinite(targetBot.foodSaturation)
    ? Math.round((targetBot.foodSaturation ?? 0) * 10) / 10
    : 0;

  await agentBridge.emit({
    channel: 'multi-agent',
    event: 'status',
    agentId: primaryAgentId,
    timestamp: Date.now(),
    payload: {
      health,
      maxHealth,
      food,
      saturation,
      roleId: getActiveAgentRole().id,
      ...extraPayload,
    },
  });
}

/**
 * 周囲のブロックやエンティティ情報を収集し、一定間隔で AgentBridge へ配信する。
 * 知覚スナップショットのキャッシュを持ち回ることで、他モジュールからも直近の情報を再利用できる。
 */
export async function broadcastAgentPerception(params: {
  targetBot: Bot;
  agentBridge: AgentBridge;
  primaryAgentId: string;
  buildPerceptionSnapshotSafe: (targetBot: Bot, reason: string) => PerceptionSnapshot | null;
  state: PerceptionBroadcastState;
  perceptionBroadcastIntervalMs: number;
  force?: boolean;
}): Promise<void> {
  const {
    targetBot,
    agentBridge,
    primaryAgentId,
    buildPerceptionSnapshotSafe,
    state,
    perceptionBroadcastIntervalMs,
    force = false,
  } = params;

  if (!force && Date.now() - state.lastBroadcastAt < perceptionBroadcastIntervalMs) {
    return;
  }

  const snapshot = buildPerceptionSnapshotSafe(targetBot, 'agent-event');
  if (!snapshot) {
    return;
  }

  state.lastBroadcastAt = Date.now();
  state.lastSnapshot = snapshot;

  await agentBridge.emit({
    channel: 'multi-agent',
    event: 'perception',
    agentId: primaryAgentId,
    timestamp: Date.now(),
    payload: snapshot,
  });
}
