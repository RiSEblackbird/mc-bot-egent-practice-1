import type { Bot } from 'mineflayer';
import minecraftData from 'minecraft-data';
import { Vec3 as Vec3Type } from 'vec3';

import type { NavigationController } from '../navigationController.js';
import type { AgentRoleDescriptor } from '../roles.js';
import type { BotChatMessenger } from '../services/chatBridge.js';
import type { CommandResponse, MultiAgentEventPayload } from '../types.js';

export interface CoreCommandDependencies {
  navigationController: NavigationController;
  chatCommandMessenger: BotChatMessenger;
  primaryAgentId: string;
  miningApproachTolerance: number;
  goals: typeof import('mineflayer-pathfinder').goals;
  applyAgentRoleUpdate: (roleId: string, source: string, reason?: string) => AgentRoleDescriptor;
  emitAgentEvent: (event: MultiAgentEventPayload) => Promise<void>;
  getActiveBot: () => Bot | null;
}

interface MineResultDetail {
  x: number;
  y: number;
  z: number;
  blockName: string;
}

const DEFAULT_MINE_SCAN_RADIUS = 12;
const DEFAULT_MINE_MAX_TARGETS = 3;
const MAX_MINE_SCAN_RADIUS = 32;
const MAX_MINE_TARGETS = 8;

export function createCoreCommandHandlers(deps: CoreCommandDependencies) {
  function handleChatCommand(args: Record<string, unknown>): CommandResponse {
    const text = typeof args.text === 'string' ? args.text : '';
    const delivered = deps.chatCommandMessenger.sendChat(text);

    if (!delivered) {
      console.warn('[ChatCommand] rejected because bot is unavailable');
      return { ok: false, error: 'Bot is not connected to the Minecraft server yet' };
    }

    console.log(`[ChatCommand] sent in-game chat: ${text}`);
    return { ok: true };
  }

  async function handleMoveToCommand(args: Record<string, unknown>): Promise<CommandResponse> {
    return deps.navigationController.handleMoveToCommand(args, { getActiveBot: deps.getActiveBot });
  }

  async function handleMineOreCommand(args: Record<string, unknown>): Promise<CommandResponse> {
    const oresRaw = Array.isArray(args.ores) ? args.ores : [];
    const normalizedOres = oresRaw
      .map((value) => (typeof value === 'string' ? value.trim().toLowerCase() : ''))
      .filter((value) => value.length > 0);

    if (normalizedOres.length === 0) {
      normalizedOres.push('redstone_ore', 'deepslate_redstone_ore');
    }

    const scanRadiusRaw = Number(args.scanRadius);
    const scanRadius = Number.isFinite(scanRadiusRaw) && scanRadiusRaw > 0
      ? Math.min(Math.floor(scanRadiusRaw), MAX_MINE_SCAN_RADIUS)
      : DEFAULT_MINE_SCAN_RADIUS;

    const maxTargetsRaw = Number(args.maxTargets);
    const maxTargets = Number.isFinite(maxTargetsRaw) && maxTargetsRaw > 0
      ? Math.min(Math.floor(maxTargetsRaw), MAX_MINE_TARGETS)
      : DEFAULT_MINE_MAX_TARGETS;

    const activeBot = deps.getActiveBot();

    if (!activeBot) {
      console.warn('[MineOreCommand] rejected because bot is unavailable');
      return { ok: false, error: 'Bot is not connected to the Minecraft server yet' };
    }

    const data = minecraftData(activeBot.version);
    const blocksByName = data.blocksByName as Record<string, { id: number; name: string }>;
    const targetIds = new Set<number>();
    const unknownOres: string[] = [];

    for (const oreName of normalizedOres) {
      const blockInfo = blocksByName[oreName];
      if (blockInfo && typeof blockInfo.id === 'number') {
        targetIds.add(blockInfo.id);
      } else {
        unknownOres.push(oreName);
      }
    }

    if (targetIds.size === 0) {
      console.warn('[MineOreCommand] no known ore ids resolved', { normalizedOres });
      return { ok: false, error: 'Requested ore types are not recognized for this version' };
    }

    if (unknownOres.length > 0) {
      console.warn('[MineOreCommand] some ore names were not resolved', { unknownOres });
    }

    const foundPositions: Vec3Type[] = activeBot.findBlocks({
      matching: (block) => Boolean(block && targetIds.has(block.type)),
      maxDistance: scanRadius,
      count: maxTargets,
    });

    if (foundPositions.length === 0) {
      console.warn(
        `[MineOreCommand] target ores not found within radius ${scanRadius}`,
        { normalizedOres },
      );
      return { ok: false, error: 'Target ore not found within scan radius' };
    }

    const results: MineResultDetail[] = [];
    for (const position of foundPositions) {
      const block = activeBot.blockAt(position);
      if (!block || !targetIds.has(block.type)) {
        continue;
      }

      const goal = new deps.goals.GoalNear(position.x, position.y, position.z, deps.miningApproachTolerance);
      const movements = deps.navigationController.getDigPermissiveMovements() ?? activeBot.pathfinder.movements;

      try {
        await deps.navigationController.gotoWithForcedMoveRetry(activeBot, goal, movements);
      } catch (moveError) {
        console.error('[MineOreCommand] failed to approach ore block', moveError);
        continue;
      }

      const refreshed = activeBot.blockAt(position);
      if (!refreshed || !targetIds.has(refreshed.type)) {
        console.warn('[MineOreCommand] ore disappeared before digging', position);
        continue;
      }

      try {
        await activeBot.dig(refreshed, true);
        deps.navigationController.recordMoveTarget({ x: position.x, y: position.y, z: position.z });
        results.push({
          x: position.x,
          y: position.y,
          z: position.z,
          blockName: refreshed.name,
        });
        console.log(
          `[MineOreCommand] mined ${refreshed.name} at (${position.x}, ${position.y}, ${position.z}) using tolerance ${deps.miningApproachTolerance}`,
        );
      } catch (digError) {
        console.error('[MineOreCommand] failed to dig ore block', digError);
      }
    }

    if (results.length === 0) {
      return { ok: false, error: 'Failed to mine target ores' };
    }

    return {
      ok: true,
      data: {
        minedBlocks: results.length,
        details: results,
        unresolvedOres: unknownOres,
      },
    };
  }

  async function handleSetAgentRoleCommand(args: Record<string, unknown>): Promise<CommandResponse> {
    const roleIdRaw = typeof args.roleId === 'string' ? args.roleId : '';
    const reasonRaw = typeof args.reason === 'string' ? args.reason : '';
    const descriptor = deps.applyAgentRoleUpdate(roleIdRaw, 'command', reasonRaw);

    await deps.emitAgentEvent({
      channel: 'multi-agent',
      event: 'roleUpdate',
      agentId: deps.primaryAgentId,
      timestamp: Date.now(),
      payload: {
        roleId: descriptor.id,
        label: descriptor.label,
        responsibilities: descriptor.responsibilities,
        reason: reasonRaw,
      },
    });

    return { ok: true, data: { roleId: descriptor.id, label: descriptor.label } };
  }

  return {
    handleChatCommand,
    handleMoveToCommand,
    handleMineOreCommand,
    handleSetAgentRoleCommand,
  };
}

export type CoreCommandHandlers = ReturnType<typeof createCoreCommandHandlers>;
