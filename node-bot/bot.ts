// 日本語コメント：Mineflayer ボット（WSコマンド受信）
// 役割：Python からの JSON コマンドを実ゲーム操作へ変換する
import type { Bot } from 'mineflayer';
import type { Item } from 'prismarine-item';
import Vec3, { Vec3 as Vec3Type } from 'vec3';
import { SpanStatusCode } from '@opentelemetry/api';
// mineflayer-pathfinder は CommonJS 形式のため、ESM 環境では一度デフォルトインポートしてから必要要素を取り出す。
// そうしないと Node.js 実行時に named export の解決に失敗するため、本構成では明示的な分割代入を採用する。
import mineflayerPathfinder from 'mineflayer-pathfinder';
import type { Movements as MovementsClass } from 'mineflayer-pathfinder';
import minecraftData from 'minecraft-data';
import { WebSocket } from 'ws';
import { bootstrapRuntime } from './runtime/bootstrap.js';
import { CUSTOM_SLOT_PATCH } from './runtime/slotPatch.js';
import {
  AgentRoleDescriptor,
} from './runtime/roles.js';
import { startCommandServer } from './runtime/server.js';
import { runWithSpan, summarizeArgs } from './runtime/telemetryRuntime.js';
import { NavigationController } from './runtime/navigationController.js';
import { createEquipItemCommandHandler, EQUIP_TOOL_MATCHERS } from './runtime/commands/equipItemCommand.js';
import { createSkillCommandHandlers } from './runtime/commands/skillCommands.js';
import {
  broadcastAgentPerception,
  broadcastAgentStatus,
  createPerceptionBroadcastState,
  type PerceptionBroadcastState,
} from './runtime/services/telemetryBroadcast.js';
import { BotLifecycleService } from './runtime/services/lifecycleService.js';
import type {
  EnvironmentSnapshot,
  FoodDictionary,
  FoodInfo,
  GatherStatusKind,
  GeneralStatusSnapshot,
  DigPermissionSnapshot,
  HazardSummary,
  InventoryItemSnapshot,
  InventorySnapshot,
  LightingSummary,
  NullableDurabilityValue,
  NearbyEntitySummary,
  PerceptionSnapshot,
  PositionReference,
  PositionSnapshot,
  VptAction,
  VptControlAction,
  VptControlName,
  VptLookAction,
  VptNavigationHint,
  VptObservationHotbarSlot,
  VptObservationSnapshot,
  VptWaitAction,
  WeatherSummary,
} from './runtime/snapshots.js';
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

// ---- Mineflayer ボット本体のライフサイクル管理 ----
let cachedFoodsByName: FoodDictionary = {};
let isConsumingFood = false;
let lastHungerWarningAt = 0;
const PRIMARY_AGENT_ID = 'primary';
let isVptPlaybackActive = false;
// 知覚ブロードキャストのキャッシュを保持し、サービス間で共有する。
const perceptionBroadcastState: PerceptionBroadcastState = createPerceptionBroadcastState();

// MovementsClass を拡張して mineflayer-pathfinder の内部プロパティへアクセスできるようにする補助型。
type MutableMovements = MovementsClass & {
  canDig?: boolean;
  digCost?: number;
};

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
  const client = targetBot._client;
  const originalWrite: typeof client.write = client.write.bind(client);
  // 1.21.1 以降の Paper では属性名が `minecraft:generic.movement_speed` に統一されたため、
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
    cachedFoodsByName = ((mcData as unknown as { foodsByName?: FoodDictionary }).foodsByName) ?? {};
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
    void monitorCriticalHunger(targetBot);
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
    // 受信したチャット内容を詳細ログへ出力し、
    // 「チャットは届いているが自動処理は未実装」である点を開発者へ明示する。
    console.info(`[Chat] <${username}> ${message}`);
    if (shouldReportCurrentPosition(message)) {
      reportCurrentPosition(targetBot);
    }
    void forwardChatToAgent(username, message);
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
  const activeBot = getActiveBot();

  if (!activeBot) {
    console.warn('[ChatCommand] rejected because bot is unavailable');
    return { ok: false, error: 'Bot is not connected to the Minecraft server yet' };
  }

  activeBot.chat(text);
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

/**
 * gatherStatus コマンドを処理し、移動前に必要な情報を即時収集する。
 *
 * Mineflayer から取得可能なステータスを種類ごとに切り分け、Python 側が
 * プレイヤーへ質問せずとも意思決定できるように集約する。
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
  const velocity = entity.velocity ?? ({ x: 0, y: 0, z: 0 } as Vec3Type);
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

const ENCHANT_NAME_MAP: Record<string, string> = {
  efficiency: '効率強化',
  unbreaking: '耐久力',
  fortune: '幸運',
  silk_touch: 'シルクタッチ',
  mending: '修繕',
};

const ROMAN_NUMERALS = ['I', 'II', 'III', 'IV', 'V', 'VI', 'VII', 'VIII', 'IX', 'X'];

interface EnchantmentInfo {
  id: string;
  level: number;
}

function buildPositionSnapshot(targetBot: Bot): PositionSnapshot {
  const { x, y, z } = targetBot.entity.position;
  const rounded = { x: Math.floor(x), y: Math.floor(y), z: Math.floor(z) };
  const dimension = targetBot.game.dimension ?? 'unknown';
  const formatted = `現在位置は X=${rounded.x} / Y=${rounded.y} / Z=${rounded.z}（ディメンション: ${dimension}）です。`;
  return { kind: 'position', position: rounded, dimension, formatted };
}

/**
 * Mineflayer が公開する耐久値を含めてインベントリのスナップショットを生成する。
 *
 * 単純なマッピングであっても、ツルハシの選定ロジックが Python 側で依存しているため、
 * 耐久関連フィールドは欠損時に null を明示することで後段の推論が扱いやすくなる。
 */
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

/**
 * Mineflayer の Item から耐久関連フィールドを抽出し、欠損時は null を設定する。
 *
 * null を統一的に用いることで、Python 側では "値が来ていない" 状態を簡単に検知できる。
 */
function createInventoryItemSnapshot(item: Item): InventoryItemSnapshot {
  const maxDurability = resolveDurabilityValue((item as Record<string, unknown>).maxDurability);
  const durabilityUsed = resolveDurabilityValue((item as Record<string, unknown>).durabilityUsed);
  const directDurability = resolveDurabilityValue((item as Record<string, unknown>).durability);
  const durability =
    directDurability ??
    (maxDurability !== null && durabilityUsed !== null
      ? Math.max(0, maxDurability - durabilityUsed)
      : null);

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

/**
 * Mineflayer から渡される値は number 以外になることもあるため、有限数のみを許可する。
 */
function resolveDurabilityValue(value: unknown): NullableDurabilityValue {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
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
  const warnings = [
    ...hazards.warnings,
    ...resolveLightingWarnings(lighting),
    ...resolveEntityWarnings(nearbyEntities),
  ];
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
    if (!Number.isFinite(distance) || distance > perceptionEntityRadius) {
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
  const radius = perceptionBlockRadius;
  const height = perceptionBlockHeight;
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

function degToRad(value: number): number {
  return (value * Math.PI) / 180;
}

function radToDeg(value: number): number {
  return (value * 180) / Math.PI;
}

function clampPitch(radians: number): number {
  const minPitch = -Math.PI / 2;
  const maxPitch = Math.PI / 2;
  return Math.max(minPitch, Math.min(maxPitch, radians));
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

/**
 * 空腹が限界に達した際の自動対応を実行する。
 *
 * - 食料が存在しない場合はプレイヤーへチャットで不足を通知
 * - 食料が存在する場合は手元へ装備して摂取し、スタミナ低下を抑制
 */
async function monitorCriticalHunger(targetBot: Bot): Promise<void> {
  if (targetBot.food > STARVATION_FOOD_LEVEL) {
    return;
  }

  if (isConsumingFood) {
    return;
  }

  const edible = findEdibleItem(targetBot);

  if (!edible) {
    const now = Date.now();
    if (now - lastHungerWarningAt >= HUNGER_WARNING_COOLDOWN_MS) {
      targetBot.chat('空腹ですが食料を所持していません。補給をお願いします。');
      lastHungerWarningAt = now;
    }
    return;
  }

  isConsumingFood = true;
  try {
    await targetBot.equip(edible, 'hand');
    await targetBot.consume();
    targetBot.chat('空腹のため手持ちの食料を食べました。');
  } catch (error) {
    console.error('[Hunger] failed to consume food', error);
  } finally {
    isConsumingFood = false;
  }
}

/**
 * インベントリ内から食料アイテムを探索し、最初に見つかったアイテムを返す。
 */
function findEdibleItem(targetBot: Bot): Item | undefined {
  return targetBot
    .inventory
    .items()
    .find((item) => Boolean(cachedFoodsByName[item.name]));
}

/**
 * プレイヤーのチャットが現在位置照会かどうかを判定する。
 *
 * 余分な空白や大文字小文字を除去して検索し、誤検出を防ぎながら柔軟にマッチングする。
 */
function shouldReportCurrentPosition(message: string): boolean {
  const normalized = message.replace(/\s+/g, '').toLowerCase();
  return CURRENT_POSITION_KEYWORDS.some((keyword) => normalized.includes(keyword));
}

/**
 * Bot の現在位置を日本語でチャットへ報告する。
 *
 * Mineflayer の entity 情報が未初期化の場合は警告を残し、誤情報を送らないようにする。
 */
function reportCurrentPosition(targetBot: Bot): void {
  if (!targetBot.entity) {
    console.warn('[Chat] position requested but bot entity is not ready yet.');
    targetBot.chat('まだワールドに完全に参加していません。しばらくお待ちください。');
    return;
  }

  const { x, y, z } = targetBot.entity.position;
  const formatted = `現在位置は X=${Math.floor(x)} / Y=${Math.floor(y)} / Z=${Math.floor(z)} です。`;
  targetBot.chat(formatted);
  console.info(`[Chat] reported current position ${formatted}`);
}

/**
 * Python エージェントへチャットを転送し、処理キューへ積ませる補助関数。
 * 接続失敗時にはエラーログを残しつつボットのメインループを継続する。
 */
async function forwardChatToAgent(username: string, message: string): Promise<void> {
  return new Promise((resolve) => {
    const payload = {
      type: 'chat',
      args: { username, message },
    } satisfies CommandPayload;

    const ws = new WebSocket(agentControlWebsocketUrl);
    const timeout = setTimeout(() => {
      console.warn('[ChatBridge] agent did not respond within 10s');
      ws.terminate();
      resolve();
    }, 10_000);

    const cleanup = () => {
      clearTimeout(timeout);
      ws.removeAllListeners();
      resolve();
    };

    ws.once('open', () => {
      ws.send(JSON.stringify(payload));
    });

    ws.once('message', (data) => {
      const text = data.toString();
      console.info(`[ChatBridge] agent response: ${text}`);
      try {
        const parsed = JSON.parse(text) as CommandResponse;
        if (!parsed.ok) {
          console.warn('[ChatBridge] agent reported failure', parsed);
        }
      } catch (error) {
        console.warn('[ChatBridge] failed to parse agent response', error);
      }
      ws.close();
      cleanup();
    });

    ws.once('close', () => {
      cleanup();
    });

    ws.once('error', (error) => {
      console.error('[ChatBridge] failed to reach agent', error);
      cleanup();
    });
  }).catch((error) => {
    console.error('[ChatBridge] unexpected error', error);
  });
}
