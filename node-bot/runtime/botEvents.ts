import type { Bot } from 'mineflayer';
import minecraftData from 'minecraft-data';
import type { Movements as MovementsClass } from 'mineflayer-pathfinder';

import { BotChatMessenger, ChatBridge } from './services/chatBridge.js';
import { SustainabilityService } from './services/sustainabilityService.js';
import {
  broadcastAgentPerception,
  broadcastAgentStatus,
  type PerceptionBroadcastState,
} from './services/telemetryBroadcast.js';
import type { BotLifecycleService } from './services/lifecycleService.js';
import type { AgentRoleDescriptor } from './roles.js';
import type { NavigationController } from './navigationController.js';

type MovementConstructor = new (bot: Bot, data: ReturnType<typeof minecraftData>) => MovementsClass;

export interface BotEventDependencies {
  agentControlWebsocketUrl: string;
  currentPositionKeywords: string[];
  primaryAgentId: string;
  lifecycleService: BotLifecycleService;
  navigationController: NavigationController;
  perceptionBroadcastState: PerceptionBroadcastState;
  perceptionBroadcastIntervalMs: number;
  starvationFoodLevel: number;
  hungerWarningCooldownMs: number;
  movementConstructor: MovementConstructor;
  minecraftReconnectDelayMs: number;
  getActiveAgentRole: () => AgentRoleDescriptor;
  getPerceptionSnapshot: () => (() => Promise<unknown>) | null | undefined;
}

interface BroadcastDeps {
  agentControlWebsocketUrl: string;
  currentPositionKeywords: string[];
  primaryAgentId: string;
  perceptionBroadcastState: PerceptionBroadcastState;
  perceptionBroadcastIntervalMs: number;
  getActiveAgentRole: () => AgentRoleDescriptor;
  getPerceptionSnapshot: () => (() => Promise<unknown>) | null | undefined;
}

function createBroadcasters(
  lifecycleService: BotLifecycleService,
  deps: BroadcastDeps,
) {
  const { agentControlWebsocketUrl, currentPositionKeywords, primaryAgentId } = deps;

  const broadcastAgentPosition = async (targetBot: Bot): Promise<void> => {
    const { x, y, z } = targetBot.entity.position;
    const rounded = { x: Math.floor(x), y: Math.floor(y), z: Math.floor(z) };
    const previous = lifecycleService.getLastBroadcastPosition();
    if (previous && previous.x === rounded.x && previous.y === rounded.y && previous.z === rounded.z) {
      return;
    }
    lifecycleService.setLastBroadcastPosition(rounded);

    await lifecycleService.emitAgentEvent({
      channel: 'multi-agent',
      event: 'position',
      agentId: primaryAgentId,
      timestamp: Date.now(),
      payload: {
        ...rounded,
        dimension: targetBot.game.dimension ?? 'unknown',
        roleId: deps.getActiveAgentRole().id,
      },
    });
  };

  const broadcastAgentStatusEvent = async (
    targetBot: Bot,
    extraPayload: Record<string, unknown> = {},
  ): Promise<void> => {
    await broadcastAgentStatus({
      targetBot,
      agentBridge: lifecycleService.getAgentBridge(),
      primaryAgentId,
      getActiveAgentRole: deps.getActiveAgentRole,
      extraPayload,
    });
  };

  const broadcastAgentPerceptionEvent = async (
    targetBot: Bot,
    options: { force?: boolean } = {},
  ): Promise<void> => {
    const snapshotBuilder = deps.getPerceptionSnapshot();
    if (!snapshotBuilder) return;

    await broadcastAgentPerception({
      targetBot,
      agentBridge: lifecycleService.getAgentBridge(),
      primaryAgentId,
      buildPerceptionSnapshotSafe: snapshotBuilder,
      state: deps.perceptionBroadcastState,
      perceptionBroadcastIntervalMs: deps.perceptionBroadcastIntervalMs,
      force: options.force,
    });
  };

  const createChatBridge = (chatMessenger: BotChatMessenger): ChatBridge =>
    new ChatBridge(
      { agentControlWebsocketUrl, currentPositionKeywords },
      { chatMessenger },
    );

  return {
    broadcastAgentPosition,
    broadcastAgentStatusEvent,
    broadcastAgentPerceptionEvent,
    createChatBridge,
  };
}

export function createBotEventHandlers(deps: BotEventDependencies) {
  const broadcasters = createBroadcasters(deps.lifecycleService, deps);

  function registerBotEventHandlers(targetBot: Bot): void {
    const chatMessenger = new BotChatMessenger(() => targetBot);
    const sustainabilityService = new SustainabilityService(
      {
        starvationFoodLevel: deps.starvationFoodLevel,
        hungerWarningCooldownMs: deps.hungerWarningCooldownMs,
      },
      { chatMessenger },
    );
    const chatBridge = broadcasters.createChatBridge(chatMessenger);

    const client = targetBot._client;
    const originalWrite: typeof client.write = client.write.bind(client);
    client.write = ((name: string, params: any) => {
      if (name === 'update_attributes' && params && Array.isArray(params.attributes)) {
        let mutated = false;
        const patchedAttributes = params.attributes.map((attr: any) => {
          if (attr && attr.key === 'minecraft:movement_speed') {
            mutated = true;
            return { ...attr, key: 'minecraft:generic.movement_speed' };
          }
          return attr;
        });

        if (mutated) {
          params = { ...params, attributes: patchedAttributes };
        }
      }

      return originalWrite(name, params);
    }) as typeof client.write;

    targetBot.once('spawn', () => {
      const mcData = minecraftData(targetBot.version);
      const foodsByName = ((mcData as unknown as { foodsByName?: Record<string, unknown> }).foodsByName) ?? {};
      sustainabilityService.updateFoodDictionary(foodsByName);

      const MovementsWithData = deps.movementConstructor;
      const digFriendlyMovements = new MovementsWithData(targetBot, mcData);
      deps.navigationController.configureMovementProfile(digFriendlyMovements, true);

      const cautiousMovementProfile = new MovementsWithData(targetBot, mcData);
      deps.navigationController.configureMovementProfile(cautiousMovementProfile, false);
      deps.navigationController.setMovementProfiles(cautiousMovementProfile, digFriendlyMovements);

      targetBot.pathfinder.setMovements(cautiousMovementProfile);
      console.log('[Bot] movement profiles initialized (cautious default / digging fallback).');
      targetBot.chat('起動しました。（Mineflayer）');
      void broadcasters.broadcastAgentStatusEvent(targetBot, { lifecycle: 'spawn' });
      void broadcasters.broadcastAgentPosition(targetBot);
      void broadcasters.broadcastAgentPerceptionEvent(targetBot, { force: true });
    });

    targetBot.on('health', () => {
      void sustainabilityService.monitorCriticalHunger(targetBot);
      void broadcasters.broadcastAgentStatusEvent(targetBot);
      void broadcasters.broadcastAgentPerceptionEvent(targetBot);
    });

    targetBot.on('move', () => {
      void broadcasters.broadcastAgentPosition(targetBot);
      void broadcasters.broadcastAgentPerceptionEvent(targetBot);
    });

    targetBot.on('forcedMove', () => {
      const now = Date.now();
      const shouldLog = deps.navigationController.recordForcedMove(now);
      if (shouldLog) {
        console.warn('[Bot] server corrected our position (forcedMove). Monitoring for retries.');
      }
    });

    targetBot.on('chat', (username: string, message: string) => {
      if (username === targetBot.username) return;
      console.info(`[Chat] <${username}> ${message}`);
      void chatBridge.handleIncomingChat(targetBot, username, message);
    });

    targetBot.on('error', (error: Error & { code?: string }) => {
      console.error('[Bot] connection error detected', error);
      const isConnectionFailure = error.code === 'ECONNREFUSED' || !targetBot.entity;

      if (isConnectionFailure) {
        deps.lifecycleService.handleConnectionLoss('connection_error');
      }
    });

    targetBot.once('kicked', (reason) => {
      console.warn(`[Bot] kicked from server: ${reason}. Retrying in ${deps.minecraftReconnectDelayMs}ms.`);
      deps.lifecycleService.handleConnectionLoss('kicked');
    });

    targetBot.once('end', (reason) => {
      console.warn(`[Bot] disconnected (${String(reason ?? 'unknown reason')}). Retrying in ${deps.minecraftReconnectDelayMs}ms.`);
      deps.lifecycleService.handleConnectionLoss('ended');
    });
  }

  return {
    registerBotEventHandlers,
    broadcastAgentPosition: broadcasters.broadcastAgentPosition,
    broadcastAgentStatusEvent: broadcasters.broadcastAgentStatusEvent,
    broadcastAgentPerceptionEvent: broadcasters.broadcastAgentPerceptionEvent,
  };
}

export type BotEventHandlers = ReturnType<typeof createBotEventHandlers>;
