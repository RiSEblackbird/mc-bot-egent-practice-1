// 日本語コメント：Mineflayer ボット（WSコマンド受信）
// 役割：Python からの JSON コマンドを実ゲーム操作へ変換する
import type { Bot } from 'mineflayer';
import Vec3, { Vec3 as Vec3Type } from 'vec3';
import { SpanStatusCode } from '@opentelemetry/api';
// mineflayer-pathfinder は CommonJS 形式のため、ESM 環境では一度デフォルトインポートしてから必要要素を取り出す。
// そうしないと Node.js 実行時に named export の解決に失敗するため、本構成では明示的な分割代入を採用する。
import mineflayerPathfinder from 'mineflayer-pathfinder';
import type { Movements as MovementsClass } from 'mineflayer-pathfinder';
import minecraftData from 'minecraft-data';
import { bootstrapRuntime } from './runtime/bootstrap.js';
import { CUSTOM_SLOT_PATCH } from './runtime/slotPatch.js';
import {
  AgentRoleDescriptor,
} from './runtime/roles.js';
import { startCommandServer } from './runtime/server.js';
import { runWithSpan, summarizeArgs } from './runtime/telemetryRuntime.js';
import { NavigationController } from './runtime/navigationController.js';
import { createEquipItemCommandHandler } from './runtime/commands/equipItemCommand.js';
import { createSkillCommandHandlers } from './runtime/commands/skillCommands.js';
import { createStatusCommandHandlers } from './runtime/commands/statusCommands.js';
import { createVptCommandHandlers } from './runtime/commands/vptCommands.js';
import {
  broadcastAgentPerception,
  broadcastAgentStatus,
  createPerceptionBroadcastState,
  type PerceptionBroadcastState,
} from './runtime/services/telemetryBroadcast.js';
import { BotLifecycleService } from './runtime/services/lifecycleService.js';
import { BotChatMessenger, ChatBridge } from './runtime/services/chatBridge.js';
import { SustainabilityService } from './runtime/services/sustainabilityService.js';
import type { FoodDictionary } from './runtime/snapshots.js';
import type { CommandPayload, CommandResponse, MultiAgentEventPayload } from './runtime/types.js';

// 型情報を維持するため、実体の分割代入時にモジュール全体の型定義を参照させる。
const { pathfinder, Movements, goals } = mineflayerPathfinder as typeof import('mineflayer-pathfinder');

// ---- チャット応答用の補助定数 ----
// 「現在値」など位置確認に関する質問を検知するためのキーワード集合。
const CURRENT_POSITION_KEYWORDS = ['現在値', '現在地', '現在位置', '今どこ', 'いまどこ'];

// ---- Minecraft プロトコル差分パッチ ----
// 詳細な Slot 構造体の上書きロジックは runtime/slotPatch.ts に切り出し、複数バージョンへ一括適用する。

// ---- 環境変数・定数設定 ----
// 起動フローの可読性を高めるため、設定・テレメトリ・AgentBridge 生成は bootstrapRuntime に集約する。
const { configValues, telemetry, agentBridge } = bootstrapRuntime();
const {
  control,
  minecraft,
  websocket,
  agentBridge: agentBridgeConfig,
  moveGoalTolerance,
  movement,
  skills,
  perception,
} = configValues;
const {
  tracer,
  commandDurationMs: commandDurationHistogram,
  reconnectCounter,
  directiveCounter,
  perceptionSnapshotDurationMs: perceptionSnapshotHistogram,
  perceptionErrorCounter,
} = telemetry;
const vptCommandsEnabled = control.vptCommandsEnabled;
const vptTickIntervalMs = control.tickIntervalMs;
const vptMaxSequenceLength = control.maxSequenceLength;
const skillHistoryPath = skills.historyPath;
const perceptionEntityRadius = perception.entityRadius;
const perceptionBlockRadius = perception.blockRadius;
const perceptionBlockHeight = perception.blockHeight;
const perceptionBroadcastIntervalMs = perception.broadcastIntervalMs;
const moveGoalToleranceMeters = moveGoalTolerance.tolerance;
const pathfinderMovement = movement.pathfinder;
const forcedMove = movement.forcedMove;
const agentControlWebsocketUrl = agentBridgeConfig.url;
const MINING_APPROACH_TOLERANCE = 1;

// ---- Mineflayer ボット本体のライフサイクル管理 ----
const PRIMARY_AGENT_ID = 'primary';
// 知覚ブロードキャストのキャッシュを保持し、サービス間で共有する。
const perceptionBroadcastState: PerceptionBroadcastState = createPerceptionBroadcastState();

// NavigationController へ移動系設定を集約して注入し、環境変数の変更だけで掘削許可やリトライ挙動を切り替えられるようにする。
const navigationController = new NavigationController({
  moveGoalToleranceMeters,
  forcedMoveRetryWindowMs: forcedMove.retryWindowMs,
  forcedMoveMaxRetries: forcedMove.maxRetries,
  forcedMoveRetryDelayMs: forcedMove.retryDelayMs,
  pathfinder: {
    allowParkour: pathfinderMovement.allowParkour,
    allowSprinting: pathfinderMovement.allowSprinting,
    digCost: {
      enable: pathfinderMovement.digCost.enabled,
      disable: pathfinderMovement.digCost.disabled,
    },
  },
});

// Mineflayer のライフサイクルや役割ステートをまとめて扱うサービス。
// bot.ts 側ではインスタンス生成とイベント組み立てだけに集中する。
const lifecycleService = new BotLifecycleService({
  tracer,
  reconnectCounter,
  minecraft,
  customSlotPatch: CUSTOM_SLOT_PATCH,
  pathfinderPlugin: pathfinder,
  agentBridge,
});

const STARVATION_FOOD_LEVEL = 0;
const HUNGER_WARNING_COOLDOWN_MS = 30_000;

const getActiveAgentRole = (): AgentRoleDescriptor => lifecycleService.getActiveAgentRole();

const applyAgentRoleUpdate = (roleId: string, source: string, reason?: string): AgentRoleDescriptor =>
  lifecycleService.applyAgentRoleUpdate(roleId, source, reason);

/**
 * Python 側の LangGraph 共有メモリへイベントを伝搬する補助ユーティリティ。
 */
async function emitAgentEvent(event: MultiAgentEventPayload): Promise<void> {
  await lifecycleService.emitAgentEvent(event);
}

/**
 * 直近の座標変化を検知して LangGraph 共有メモリへ送信する。
 */
async function broadcastAgentPosition(targetBot: Bot): Promise<void> {
  const { x, y, z } = targetBot.entity.position;
  const rounded = { x: Math.floor(x), y: Math.floor(y), z: Math.floor(z) };
  const previous = lifecycleService.getLastBroadcastPosition();
  if (previous && previous.x === rounded.x && previous.y === rounded.y && previous.z === rounded.z) {
    return;
  }
  lifecycleService.setLastBroadcastPosition(rounded);

  await emitAgentEvent({
    channel: 'multi-agent',
    event: 'position',
    agentId: PRIMARY_AGENT_ID,
    timestamp: Date.now(),
    payload: {
      ...rounded,
      dimension: targetBot.game.dimension ?? 'unknown',
      roleId: getActiveAgentRole().id,
    },
  });
}

/**
 * 体力や満腹度の更新を LangGraph 側へ通知するためのラッパー。
 * 実際の送信ロジックは runtime/services/telemetryBroadcast.ts へ集約し、
 * Bot ファイル側では依存注入の組み立てに専念する。
 */
async function broadcastAgentStatusEvent(
  targetBot: Bot,
  extraPayload: Record<string, unknown> = {},
): Promise<void> {
  await broadcastAgentStatus({
    targetBot,
    agentBridge,
    primaryAgentId: PRIMARY_AGENT_ID,
    getActiveAgentRole,
    extraPayload,
  });
}

async function broadcastAgentPerceptionEvent(targetBot: Bot, options: { force?: boolean } = {}): Promise<void> {
  await broadcastAgentPerception({
    targetBot,
    agentBridge,
    primaryAgentId: PRIMARY_AGENT_ID,
    buildPerceptionSnapshotSafe,
    state: perceptionBroadcastState,
    perceptionBroadcastIntervalMs,
    force: options.force,
  });
}

/**
 * Bot ごとに必要なイベントハンドラを登録し、切断時には再接続をスケジュールする。
 */
function registerBotEventHandlers(targetBot: Bot): void {
  const chatMessenger = new BotChatMessenger(() => targetBot);
  const sustainabilityService = new SustainabilityService(
    {
      starvationFoodLevel: STARVATION_FOOD_LEVEL,
      hungerWarningCooldownMs: HUNGER_WARNING_COOLDOWN_MS,
    },
    { chatMessenger },
  );
  const chatBridge = new ChatBridge(
    { agentControlWebsocketUrl, currentPositionKeywords: CURRENT_POSITION_KEYWORDS },
    { chatMessenger },
  );

  const client = targetBot._client;
  const originalWrite: typeof client.write = client.write.bind(client);
  // 1.21.1 以降の Paper で属性名が `minecraft:generic.movement_speed` に統一されたため、
  // 旧来の `minecraft:movement_speed` を送信するとサーバー側で警告が出る。Mineflayer が
  // まだ古い識別子を用いるケースに備えて、送信前に名称を置き換えて互換性を保つ。
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
    const foodsByName = ((mcData as unknown as { foodsByName?: FoodDictionary }).foodsByName) ?? {};
    sustainabilityService.updateFoodDictionary(foodsByName);

    // 型定義上は第2引数が未定義だが、実実装では mcData を渡すのが推奨されているため、コンストラクタ型を拡張して使用する。
    const MovementsWithData = Movements as unknown as new (bot: Bot, data: ReturnType<typeof minecraftData>) => MovementsClass;
    const digFriendlyMovements = new MovementsWithData(targetBot, mcData);
    navigationController.configureMovementProfile(digFriendlyMovements, true);

    const cautiousMovementProfile = new MovementsWithData(targetBot, mcData);
    navigationController.configureMovementProfile(cautiousMovementProfile, false);
    navigationController.setMovementProfiles(cautiousMovementProfile, digFriendlyMovements);

    // Paper 1.21.x ではパルクールやダッシュを多用すると "moved wrongly" 警告が増えるが、
    // 危険地帯での生存性を優先して俊敏な動きを維持したいので、敢えて高機動モードを維持する。
    targetBot.pathfinder.setMovements(cautiousMovementProfile);
    console.log('[Bot] movement profiles initialized (cautious default / digging fallback).');
    targetBot.chat('起動しました。（Mineflayer）');
    void broadcastAgentStatusEvent(targetBot, { lifecycle: 'spawn' });
    void broadcastAgentPosition(targetBot);
    void broadcastAgentPerceptionEvent(targetBot, { force: true });
  });

  targetBot.on('health', () => {
    void sustainabilityService.monitorCriticalHunger(targetBot);
    void broadcastAgentStatusEvent(targetBot);
    void broadcastAgentPerceptionEvent(targetBot);
  });

  targetBot.on('move', () => {
    void broadcastAgentPosition(targetBot);
    void broadcastAgentPerceptionEvent(targetBot);
  });

  // サーバーから強制移動が通知された場合はタイムスタンプを更新し、
  // moveTo コマンド側で直近発生の有無を基準にリトライを判断する。
  targetBot.on('forcedMove', () => {
    const now = Date.now();
    const shouldLog = navigationController.recordForcedMove(now);
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
      // Mineflayer は接続失敗時に error->end の順でイベントが発生するため、早期にリトライを予約する。
      lifecycleService.handleConnectionLoss('connection_error');
    }
  });

  targetBot.once('kicked', (reason) => {
    console.warn(`[Bot] kicked from server: ${reason}. Retrying in ${minecraft.reconnectDelayMs}ms.`);
    lifecycleService.handleConnectionLoss('kicked');
  });

  targetBot.once('end', (reason) => {
    console.warn(`[Bot] disconnected (${String(reason ?? 'unknown reason')}). Retrying in ${minecraft.reconnectDelayMs}ms.`);
    lifecycleService.handleConnectionLoss('ended');
  });
}

// 初回接続を起動
lifecycleService.startBotLifecycle((nextBot) => registerBotEventHandlers(nextBot));

// LangGraph 共有イベント用の WebSocket セッションを先に確立し、初回イベント配送の待ち時間を抑える。
agentBridge.ensureSession('startup');

/**
 * コマンド実行時に利用可能な Bot インスタンスを取得する。未接続の場合は null を返す。
 */
function getActiveBot(): Bot | null {
  return lifecycleService.getActiveBot();
}

// コマンド処理時にもチャット送信を DI し、テストで差し替えられるようにする。
const chatCommandMessenger = new BotChatMessenger(() => getActiveBot());

const {
  handleGatherStatusCommand,
  buildGeneralStatusSnapshot,
  buildHotbarSnapshot,
  computeNavigationHint,
  buildPerceptionSnapshotSafe,
} = createStatusCommandHandlers({
  getActiveBot,
  navigationController,
  agentBridge,
  perceptionBroadcastState,
  perceptionConfig: {
    entityRadius: perceptionEntityRadius,
    blockRadius: perceptionBlockRadius,
    blockHeight: perceptionBlockHeight,
  },
  telemetry: { perceptionSnapshotHistogram, perceptionErrorCounter },
  getActiveAgentRole,
});

const { handleGatherVptObservationCommand, handlePlayVptActionsCommand } = createVptCommandHandlers({
  getActiveBot,
  vptCommandsEnabled,
  vptTickIntervalMs,
  vptMaxSequenceLength,
  buildGeneralStatusSnapshot,
  buildHotbarSnapshot,
  computeNavigationHint,
});

// ---- コマンドハンドラの組み立て ----
// Bot の取得ロジックを共有しつつ、装備操作と skill 系処理を個別モジュールへ委譲する。
const { handleEquipItemCommand } = createEquipItemCommandHandler({ getActiveBot });
const {
  handleRegisterSkillCommand,
  handleInvokeSkillCommand,
  handleSkillExploreCommand,
  ensureSkillHistorySink,
} = createSkillCommandHandlers({ skillHistoryPath, getActiveBot });

if (skillHistoryPath) {
  void ensureSkillHistorySink();
}

startCommandServer({ host: websocket.host, port: websocket.port }, { tracer, executeCommand });

// ---- コマンド実行関数 ----
// 将来的にコマンド種別が増えても見通しよく拡張できるよう、switch 文で分岐させる。
async function executeCommand(payload: CommandPayload): Promise<CommandResponse> {
  const { type, args, meta } = payload;
  const directiveMeta = typeof meta === 'object' && meta !== null ? meta : undefined;

  if (directiveMeta) {
    console.log(`[WS] executing command type=${type} directive=${summarizeArgs(directiveMeta)}`);
  } else {
    console.log(`[WS] executing command type=${type}`);
  }

  return runWithSpan(
    tracer,
    `command.${type}`,
    {
      'command.type': type,
      'command.args.overview': summarizeArgs(args),
      ...(directiveMeta ? { 'command.meta.summary': summarizeArgs(directiveMeta) } : {}),
    },
    async (span) => {
      const startedAt = Date.now();
      let response: CommandResponse | null = null;
      let outcome: 'success' | 'failure' | 'exception' = 'success';

      try {
        if (directiveMeta) {
          const directiveId =
            typeof directiveMeta.directiveId === 'string' && directiveMeta.directiveId.trim().length > 0
              ? directiveMeta.directiveId
              : undefined;
          if (directiveId) {
            span.setAttribute('command.meta.directive_id', directiveId);
          }
          const directiveExecutor =
            typeof directiveMeta.directiveExecutor === 'string' ? directiveMeta.directiveExecutor : undefined;
          if (directiveExecutor) {
            span.setAttribute('command.meta.executor', directiveExecutor);
          }
          const directiveLabel =
            typeof directiveMeta.directiveLabel === 'string' ? directiveMeta.directiveLabel : undefined;
          if (directiveLabel) {
            span.setAttribute('command.meta.label', directiveLabel);
          }
        }

        switch (type) {
          case 'chat':
            response = await handleChatCommand(args);
            break;
          case 'moveTo':
            response = await handleMoveToCommand(args);
            break;
          case 'equipItem':
            response = await handleEquipItemCommand(args);
            break;
          case 'gatherStatus':
            response = await handleGatherStatusCommand(args);
            break;
          case 'gatherVptObservation':
            response = await handleGatherVptObservationCommand(args);
            break;
          case 'mineOre':
            response = await handleMineOreCommand(args);
            break;
          case 'setAgentRole':
            response = await handleSetAgentRoleCommand(args);
            break;
          case 'registerSkill':
            response = handleRegisterSkillCommand(args);
            break;
          case 'invokeSkill':
            response = handleInvokeSkillCommand(args);
            break;
          case 'skillExplore':
            response = handleSkillExploreCommand(args);
            break;
          case 'playVptActions':
            response = await handlePlayVptActionsCommand(args);
            break;
          default: {
            const exhaustiveCheck: never = type;
            void exhaustiveCheck;
            response = { ok: false, error: 'Unknown command type' };
            break;
          }
        }

        if (!response.ok) {
          outcome = 'failure';
          span.setStatus({ code: SpanStatusCode.ERROR, message: response.error ?? 'command returned ok=false' });
        }
        if (directiveMeta) {
          const directiveId =
            typeof directiveMeta.directiveId === 'string' && directiveMeta.directiveId.trim().length > 0
              ? directiveMeta.directiveId
              : 'unknown';
          const directiveExecutor =
            typeof directiveMeta.directiveExecutor === 'string' && directiveMeta.directiveExecutor
              ? directiveMeta.directiveExecutor
              : 'unspecified';
          directiveCounter.add(1, {
            'directive.id': directiveId,
            'directive.executor': directiveExecutor,
            'command.type': type,
            outcome,
          });
        }
        return response;
      } catch (error) {
        outcome = 'exception';
        span.setStatus({ code: SpanStatusCode.ERROR, message: error instanceof Error ? error.message : String(error) });
        throw error;
      } finally {
        const durationMs = Date.now() - startedAt;
        span.setAttribute('mineflayer.response_ms', durationMs);
        commandDurationHistogram.record(durationMs, {
          'command.type': type,
          outcome,
        });
      }
    },
  );
}

// ---- chat コマンド処理 ----
// 指定されたテキストをゲーム内チャットで送信する。
function handleChatCommand(args: Record<string, unknown>): CommandResponse {
  const text = typeof args.text === 'string' ? args.text : '';
  const delivered = chatCommandMessenger.sendChat(text);

  if (!delivered) {
    console.warn('[ChatCommand] rejected because bot is unavailable');
    return { ok: false, error: 'Bot is not connected to the Minecraft server yet' };
  }

  console.log(`[ChatCommand] sent in-game chat: ${text}`);
  return { ok: true };
}

// ---- moveTo コマンド処理 ----
// 指定座標へ pathfinder を使って移動する。
async function handleMoveToCommand(args: Record<string, unknown>): Promise<CommandResponse> {
  return navigationController.handleMoveToCommand(args, { getActiveBot });
}

interface MineResultDetail {
  x: number;
  y: number;
  z: number;
  blockName: string;
}

// ---- 採掘関連の閾値 ----
// スキャン半径や採掘対象数に安全なデフォルトを設け、暴走的な範囲破壊を防ぐ。
const DEFAULT_MINE_SCAN_RADIUS = 12;
const DEFAULT_MINE_MAX_TARGETS = 3;
const MAX_MINE_SCAN_RADIUS = 32;
const MAX_MINE_TARGETS = 8;

/**
 * mineOre コマンドを処理し、指定された種類の鉱石を探索して掘削する。
 */
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

  const activeBot = getActiveBot();

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

    const goal = new goals.GoalNear(position.x, position.y, position.z, MINING_APPROACH_TOLERANCE);
    const movements = navigationController.getDigPermissiveMovements() ?? activeBot.pathfinder.movements;

    try {
      await navigationController.gotoWithForcedMoveRetry(activeBot, goal, movements);
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
      navigationController.recordMoveTarget({ x: position.x, y: position.y, z: position.z });
      results.push({
        x: position.x,
        y: position.y,
        z: position.z,
        blockName: refreshed.name,
      });
      console.log(
        `[MineOreCommand] mined ${refreshed.name} at (${position.x}, ${position.y}, ${position.z}) using tolerance ${MINING_APPROACH_TOLERANCE}`,
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

/**
 * LangGraph からの役割変更要求を受け付け、Node 側の内部状態と共有メモリへ反映する。
 */
async function handleSetAgentRoleCommand(args: Record<string, unknown>): Promise<CommandResponse> {
  const roleIdRaw = typeof args.roleId === 'string' ? args.roleId : '';
  const reasonRaw = typeof args.reason === 'string' ? args.reason : '';
  const descriptor = applyAgentRoleUpdate(roleIdRaw, 'command', reasonRaw);

  await emitAgentEvent({
    channel: 'multi-agent',
    event: 'roleUpdate',
    agentId: PRIMARY_AGENT_ID,
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

