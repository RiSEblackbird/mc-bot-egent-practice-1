// 日本語コメント：Mineflayer ボット（WSコマンド受信）
// 役割：Python からの JSON コマンドを実ゲーム操作へ変換する
import type { Bot } from 'mineflayer';
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
import { createCoreCommandHandlers } from './runtime/commands/coreCommands.js';
import { createBotEventHandlers } from './runtime/botEvents.js';
import {
  createPerceptionBroadcastState,
  type PerceptionBroadcastState,
} from './runtime/services/telemetryBroadcast.js';
import { BotLifecycleService } from './runtime/services/lifecycleService.js';
import { BotChatMessenger } from './runtime/services/chatBridge.js';
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

let perceptionSnapshotBuilder: (() => Promise<unknown>) | null = null;

const { registerBotEventHandlers } = createBotEventHandlers({
  agentControlWebsocketUrl,
  currentPositionKeywords: CURRENT_POSITION_KEYWORDS,
  primaryAgentId: PRIMARY_AGENT_ID,
  lifecycleService,
  navigationController,
  perceptionBroadcastState,
  perceptionBroadcastIntervalMs,
  starvationFoodLevel: STARVATION_FOOD_LEVEL,
  hungerWarningCooldownMs: HUNGER_WARNING_COOLDOWN_MS,
  movementConstructor: Movements as unknown as new (bot: Bot, data: ReturnType<typeof minecraftData>) => MovementsClass,
  minecraftReconnectDelayMs: minecraft.reconnectDelayMs,
  getActiveAgentRole,
  getPerceptionSnapshot: () => perceptionSnapshotBuilder,
});

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

perceptionSnapshotBuilder = buildPerceptionSnapshotSafe;

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

const {
  handleChatCommand,
  handleMoveToCommand,
  handleMineOreCommand,
  handleSetAgentRoleCommand,
} = createCoreCommandHandlers({
  navigationController,
  chatCommandMessenger,
  primaryAgentId: PRIMARY_AGENT_ID,
  miningApproachTolerance: MINING_APPROACH_TOLERANCE,
  goals,
  applyAgentRoleUpdate,
  emitAgentEvent,
  getActiveBot,
});

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

