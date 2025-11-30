// 日本語コメント：Mineflayer ボット（WSコマンド受信）
// 役割：Python からの JSON コマンドを実ゲーム操作へ変換する
import { createBot, Bot } from 'mineflayer';
import type { Item } from 'prismarine-item';
import type { Vec3 } from 'vec3';
import { SpanStatusCode } from '@opentelemetry/api';
// mineflayer-pathfinder は CommonJS 形式のため、ESM 環境では一度デフォルトインポートしてから必要要素を取り出す。
// そうしないと Node.js 実行時に named export の解決に失敗するため、本構成では明示的な分割代入を採用する。
import mineflayerPathfinder from 'mineflayer-pathfinder';
import type { Movements as MovementsClass } from 'mineflayer-pathfinder';
import minecraftData from 'minecraft-data';
import { randomUUID } from 'node:crypto';
import { constants as fsConstants } from 'node:fs';
import { access, appendFile, mkdir } from 'node:fs/promises';
import { dirname } from 'node:path';
import { WebSocketServer, WebSocket, RawData } from 'ws';
import { loadBotRuntimeConfig } from './runtime/config.js';
import { CUSTOM_SLOT_PATCH } from './runtime/slotPatch.js';
import {
  AgentRoleDescriptor,
  AgentRoleState,
  createInitialAgentRoleState,
  resolveAgentRole,
} from './runtime/roles.js';
import { initializeTelemetry, runWithSpan, summarizeArgs } from './runtime/telemetry.js';

// 型情報を維持するため、実体の分割代入時にモジュール全体の型定義を参照させる。
const { pathfinder, Movements, goals } = mineflayerPathfinder as typeof import('mineflayer-pathfinder');

// ---- チャット応答用の補助定数 ----
// 「現在値」など位置確認に関する質問を検知するためのキーワード集合。
const CURRENT_POSITION_KEYWORDS = ['現在値', '現在地', '現在位置', '今どこ', 'いまどこ'];

// ---- Minecraft プロトコル差分パッチ ----
// 詳細な Slot 構造体の上書きロジックは runtime/slotPatch.ts に切り出し、複数バージョンへ一括適用する。

// ---- 環境変数・定数設定 ----
const { config: runtimeConfig, warnings: runtimeWarnings } = loadBotRuntimeConfig(process.env);
for (const warning of runtimeWarnings) {
  console.warn(`[Config] ${warning}`);
}

// Mineflayer 制御ループの設定値はログ出力より前に初期化し、未定義参照によるクラッシュを防ぐ。
const CONTROL_MODE = runtimeConfig.control.mode;
const VPT_TICK_INTERVAL_MS = runtimeConfig.control.vpt.tickIntervalMs;
const VPT_MAX_SEQUENCE_LENGTH = runtimeConfig.control.vpt.maxSequenceLength;

console.log(`[Control] mode=${CONTROL_MODE} tick=${VPT_TICK_INTERVAL_MS}ms maxSeq=${VPT_MAX_SEQUENCE_LENGTH}`);

const MC_VERSION = runtimeConfig.minecraft.version;
const MC_HOST = runtimeConfig.minecraft.host;
const MC_PORT = runtimeConfig.minecraft.port;
const BOT_USERNAME = runtimeConfig.minecraft.username;
const AUTH_MODE = runtimeConfig.minecraft.authMode;
const MC_RECONNECT_DELAY_MS = runtimeConfig.minecraft.reconnectDelayMs;
const WS_HOST = runtimeConfig.websocket.host;
const WS_PORT = runtimeConfig.websocket.port;
const AGENT_WS_URL = runtimeConfig.agentBridge.url;
const AGENT_WS_CONNECT_TIMEOUT_MS = runtimeConfig.agentBridge.connectTimeoutMs;
const AGENT_WS_SEND_TIMEOUT_MS = runtimeConfig.agentBridge.sendTimeoutMs;
const AGENT_WS_HEALTHCHECK_INTERVAL_MS = runtimeConfig.agentBridge.healthcheckIntervalMs;
const AGENT_WS_RECONNECT_DELAY_MS = runtimeConfig.agentBridge.reconnectDelayMs;
const AGENT_WS_MAX_RETRIES = runtimeConfig.agentBridge.maxRetries;
const AGENT_EVENT_BATCH_INTERVAL_MS = runtimeConfig.agentBridge.batchFlushIntervalMs;
const AGENT_EVENT_BATCH_MAX_SIZE = runtimeConfig.agentBridge.batchMaxSize;
const AGENT_EVENT_QUEUE_MAX_SIZE = runtimeConfig.agentBridge.queueMaxSize;
const MOVE_GOAL_TOLERANCE = runtimeConfig.moveGoalTolerance.tolerance;
const MINING_APPROACH_TOLERANCE = 1;
const SKILL_HISTORY_PATH = runtimeConfig.skills.historyPath;

// ---- OpenTelemetry 初期化 ----
const telemetry = initializeTelemetry(runtimeConfig.telemetry);
const tracer = telemetry.tracer;
const commandDurationHistogram = telemetry.commandDurationMs;
const agentBridgeEventCounter = telemetry.agentBridgeEventCounter;
const reconnectCounter = telemetry.reconnectCounter;
const directiveCounter = telemetry.directiveCounter;

// ---- 型定義 ----
// 受信するコマンド種別のユニオン。追加実装時はここを拡張する。
type CommandType =
  | 'chat'
  | 'moveTo'
  | 'equipItem'
  | 'gatherStatus'
  | 'gatherVptObservation'
  | 'mineOre'
  | 'setAgentRole'
  | 'registerSkill'
  | 'invokeSkill'
  | 'skillExplore'
  | 'playVptActions';

// WebSocket で受信するメッセージの基本形。
interface CommandPayload {
  type: CommandType;
  args: Record<string, unknown>;
  meta?: Record<string, unknown>;
}

// 成功・失敗を Python 側へ返すためのレスポンス型。
interface CommandResponse {
  ok: boolean;
  error?: string;
  data?: unknown;
}

interface MultiAgentEventPayload {
  channel: 'multi-agent';
  event: 'roleUpdate' | 'position' | 'status';
  agentId: string;
  timestamp: number;
  payload: Record<string, unknown>;
}

interface AgentEventEnvelope {
  type: 'agentEvent';
  args: { event?: MultiAgentEventPayload; events?: MultiAgentEventPayload[] };
}

type AgentBridgeSessionState = 'disconnected' | 'connecting' | 'connected';

type GatherStatusKind = 'position' | 'inventory' | 'general';

interface PositionSnapshot {
  kind: 'position';
  position: { x: number; y: number; z: number };
  dimension: string;
  formatted: string;
}

type NullableDurabilityValue = number | null;

interface InventoryItemSnapshot {
  slot: number;
  name: string;
  displayName: string;
  count: number;
  enchantments: string[];
  maxDurability: NullableDurabilityValue;
  durabilityUsed: NullableDurabilityValue;
  durability: NullableDurabilityValue;
}

interface InventorySnapshot {
  kind: 'inventory';
  occupiedSlots: number;
  totalSlots: number;
  items: InventoryItemSnapshot[];
  pickaxes: InventoryItemSnapshot[];
  formatted: string;
}

interface DigPermissionSnapshot {
  allowed: boolean;
  gameMode: string;
  fallbackMovementInitialized: boolean;
  reason: string;
}

interface GeneralStatusSnapshot {
  kind: 'general';
  health: number;
  maxHealth: number;
  food: number;
  saturation: number;
  oxygenLevel: number;
  digPermission: DigPermissionSnapshot;
  agentRole: AgentRoleDescriptor;
  formatted: string;
}

type VptControlName =
  | 'forward'
  | 'back'
  | 'left'
  | 'right'
  | 'jump'
  | 'sprint'
  | 'sneak'
  | 'attack'
  | 'use';

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

interface VptControlAction {
  kind: 'control';
  control: VptControlName;
  state: boolean;
  durationTicks: number;
}

interface VptLookAction {
  kind: 'look';
  yaw: number;
  pitch: number;
  relative?: boolean;
  durationTicks?: number;
}

interface VptWaitAction {
  kind: 'wait';
  durationTicks: number;
}

type VptAction = VptControlAction | VptLookAction | VptWaitAction;

interface VptNavigationHint {
  targetYawDegrees: number;
  horizontalDistance: number;
  verticalOffset: number;
}

interface VptObservationHotbarSlot {
  slot: number;
  name: string;
  displayName: string;
  count: number;
}

interface VptObservationSnapshot {
  position: { x: number; y: number; z: number };
  velocity: { x: number; y: number; z: number };
  orientation: { yawDegrees: number; pitchDegrees: number };
  status: { health: number; food: number; saturation: number };
  onGround: boolean;
  hotbar: VptObservationHotbarSlot[];
  heldItem: string | null;
  navigationHint: VptNavigationHint | null;
  timestamp: number;
  tickAge: number;
  dimension: string;
}

interface RegisteredSkill {
  id: string;
  title: string;
  description: string;
  steps: string[];
  tags: string[];
  createdAt: number;
}

interface FoodInfo {
  // minecraft-data 側の構造体では foodPoints / saturation 等が格納されている。
  // 本エージェントでは存在確認のみ行うため、詳細なフィールド定義は必須ではない。
  foodPoints?: number;
  saturation?: number;
}

type FoodDictionary = Record<string, FoodInfo>;

// ---- Mineflayer ボット本体のライフサイクル管理 ----
// 接続失敗時にリトライするため、Bot インスタンスは都度生成し直す。
let bot: Bot | null = null;
let reconnectTimer: NodeJS.Timeout | null = null;
let cachedFoodsByName: FoodDictionary = {};
let isConsumingFood = false;
let lastHungerWarningAt = 0;
let lastMoveTarget: { x: number; y: number; z: number } | null = null;
let lastForcedMoveAt = 0;
let lastForcedMoveLoggedAt = 0;
let cautiousMovements: MovementsClass | null = null;
let digPermissiveMovements: MovementsClass | null = null;
const agentRoleState: AgentRoleState = createInitialAgentRoleState();
const PRIMARY_AGENT_ID = 'primary';
const skillRegistry = new Map<string, RegisteredSkill>();
let skillHistoryInitialized = false;
let isVptPlaybackActive = false;
const agentEventQueue: MultiAgentEventPayload[] = [];
let agentBridgeSocket: WebSocket | null = null;
let agentBridgeState: AgentBridgeSessionState = 'disconnected';
let agentBridgeReconnectTimer: NodeJS.Timeout | null = null;
let agentBridgeBatchTimer: NodeJS.Timeout | null = null;
let agentBridgeHealthcheckTimer: NodeJS.Timeout | null = null;
let agentBridgeFlushInFlight: Promise<void> | null = null;
let lastAgentBridgePongAt = 0;

// forcedMove によるサーバー補正後の再探索挙動を調整するための閾値群。
const FORCED_MOVE_RETRY_WINDOW_MS = 2_000;
const FORCED_MOVE_MAX_RETRIES = 2;
const FORCED_MOVE_RETRY_DELAY_MS = 300;

// ブロック破壊を避けたルート探索を優先させるためのコスト設定。
const DIGGING_DISABLED_COST = 96;
const DIGGING_ENABLED_COST = 1;

// MovementsClass を拡張して mineflayer-pathfinder の内部プロパティへアクセスできるようにする補助型。
type MutableMovements = MovementsClass & {
  canDig?: boolean;
  digCost?: number;
};

const STARVATION_FOOD_LEVEL = 0;
const HUNGER_WARNING_COOLDOWN_MS = 30_000;

/**
 * Minecraft サーバーへの接続を確立し、Mineflayer Bot を初期化する。
 * 失敗した場合でも再試行を継続して開発者の手戻りを防ぐ。
 */
function startBotLifecycle(): void {
  tracer.startActiveSpan(
    'mineflayer.lifecycle.start',
    {
      attributes: {
        'minecraft.host': MC_HOST,
        'minecraft.port': MC_PORT,
        'minecraft.protocol': MC_VERSION ?? 'auto',
        'minecraft.username': BOT_USERNAME,
      },
    },
    (span) => {
      try {
        const protocolLabel = MC_VERSION ?? 'auto-detect (mineflayer default)';
        console.log(`[Bot] connecting to ${MC_HOST}:${MC_PORT} with protocol ${protocolLabel} ...`);
        const nextBot = createBot({
          host: MC_HOST,
          port: MC_PORT,
          username: BOT_USERNAME,
          auth: AUTH_MODE,
          // 1.21.4+ の ItemStack 追加フィールドに対応するためのカスタムパケット定義。
          customPackets: CUSTOM_SLOT_PATCH,
          ...(MC_VERSION ? { version: MC_VERSION } : {}),
        });

        bot = nextBot;
        nextBot.loadPlugin(pathfinder);
        registerBotEventHandlers(nextBot);
      } catch (error) {
        span.setStatus({
          code: SpanStatusCode.ERROR,
          message: error instanceof Error ? error.message : String(error),
        });
        console.error('[Bot] failed to start lifecycle', error);
      } finally {
        span.end();
      }
    },
  );
}

/**
 * 現在の役割ステートを読み出すヘルパー。
 */
function getActiveAgentRole(): AgentRoleDescriptor {
  return agentRoleState.activeRole;
}

async function ensureSkillHistorySink(): Promise<void> {
  if (!SKILL_HISTORY_PATH || skillHistoryInitialized) {
    return;
  }
  try {
    await access(SKILL_HISTORY_PATH, fsConstants.F_OK);
    skillHistoryInitialized = true;
    return;
  } catch {
    try {
      await mkdir(dirname(SKILL_HISTORY_PATH), { recursive: true });
      await appendFile(SKILL_HISTORY_PATH, '');
      skillHistoryInitialized = true;
    } catch (error) {
      console.error('[SkillLog] failed to prepare history sink', error);
    }
  }
}

function logSkillEvent(level: 'info' | 'warn' | 'error', event: string, context: Record<string, unknown>): void {
  const payload = {
    level,
    event,
    timestamp: new Date().toISOString(),
    context,
  };
  console.log(JSON.stringify(payload));
  if (!SKILL_HISTORY_PATH) {
    return;
  }
  ensureSkillHistorySink()
    .then(() => appendFile(SKILL_HISTORY_PATH, `${JSON.stringify(payload)}\n`))
    .catch((error) => console.error('[SkillLog] failed to append event', error));
}

type StructuredLogLevel = 'info' | 'warn' | 'error';

/**
 * エージェントブリッジ関連のイベントを構造化ログへ一元出力する。
 * 接続状態や送信結果を JSON で記録し、可観測性と新人メンバーの追跡性を高める。
 */
function logAgentBridgeEvent(level: StructuredLogLevel, event: string, context: Record<string, unknown>): void {
  console.log(
    JSON.stringify({
      level,
      event,
      timestamp: new Date().toISOString(),
      context,
    }),
  );
}

// 接続切断時に連続再接続を避けつつ再試行するためのシンプルなリトライキュー。
function scheduleAgentBridgeReconnect(reason: string): void {
  if (agentBridgeReconnectTimer) {
    return;
  }

  agentBridgeReconnectTimer = setTimeout(() => {
    agentBridgeReconnectTimer = null;
    ensureAgentBridgeSession(reason);
  }, AGENT_WS_RECONNECT_DELAY_MS);
}

function cleanupAgentBridgeSession(session?: WebSocket): void {
  if (agentBridgeHealthcheckTimer) {
    clearInterval(agentBridgeHealthcheckTimer);
    agentBridgeHealthcheckTimer = null;
  }

  if (!session || session === agentBridgeSocket) {
    agentBridgeSocket?.removeAllListeners();
    agentBridgeSocket = null;
    agentBridgeState = 'disconnected';
  }
}

/**
 * 定期的に ping/pong を送り、セッション生存を確認するヘルスチェックタイマー。
 * pong 欠落が続いた場合は terminate して再接続ルートへ回す。
 */
function startAgentBridgeHealthcheck(session: WebSocket): void {
  if (agentBridgeHealthcheckTimer) {
    clearInterval(agentBridgeHealthcheckTimer);
  }

  agentBridgeHealthcheckTimer = setInterval(() => {
    if (session !== agentBridgeSocket || session.readyState !== WebSocket.OPEN) {
      return;
    }

    const now = Date.now();
    if (now - lastAgentBridgePongAt > AGENT_WS_HEALTHCHECK_INTERVAL_MS * 2) {
      logAgentBridgeEvent('warn', 'agent-bridge.healthcheck.timeout', {
        sinceLastPongMs: now - lastAgentBridgePongAt,
        intervalMs: AGENT_WS_HEALTHCHECK_INTERVAL_MS,
      });
      session.terminate();
      return;
    }

    try {
      session.ping();
    } catch (error) {
      logAgentBridgeEvent('error', 'agent-bridge.healthcheck.ping_failed', {
        message: error instanceof Error ? error.message : String(error),
      });
      session.terminate();
    }
  }, AGENT_WS_HEALTHCHECK_INTERVAL_MS);
}

/**
 * Python 側 WebSocket サーバーへの常駐接続を確立する。二重接続を避けつつ、
 * 起動トリガーごとに接続状態を確認して不足時のみ新規セッションを張る。
 */
function ensureAgentBridgeSession(reason: string): void {
  if (agentBridgeState !== 'disconnected') {
    return;
  }

  if (agentBridgeReconnectTimer) {
    clearTimeout(agentBridgeReconnectTimer);
    agentBridgeReconnectTimer = null;
  }

  agentBridgeState = 'connecting';
  const session = new WebSocket(AGENT_WS_URL);
  agentBridgeSocket = session;

  const connectTimeout = setTimeout(() => {
    logAgentBridgeEvent('warn', 'agent-bridge.connect.timeout', {
      timeoutMs: AGENT_WS_CONNECT_TIMEOUT_MS,
      url: AGENT_WS_URL,
    });
    session.terminate();
  }, AGENT_WS_CONNECT_TIMEOUT_MS);

  const finalize = () => {
    clearTimeout(connectTimeout);
  };

  session.once('open', () => {
    finalize();
    agentBridgeState = 'connected';
    lastAgentBridgePongAt = Date.now();
    logAgentBridgeEvent('info', 'agent-bridge.connected', { url: AGENT_WS_URL, reason });
    startAgentBridgeHealthcheck(session);
    void flushAgentEventQueue();
  });

  session.on('pong', () => {
    lastAgentBridgePongAt = Date.now();
  });

  session.once('close', (code, reasonBuffer) => {
    finalize();
    cleanupAgentBridgeSession(session);
    logAgentBridgeEvent('warn', 'agent-bridge.closed', {
      code,
      reason: reasonBuffer.toString() || 'no-reason',
    });
    scheduleAgentBridgeReconnect('closed');
  });

  session.once('error', (error) => {
    finalize();
    logAgentBridgeEvent('error', 'agent-bridge.error', {
      message: error instanceof Error ? error.message : String(error),
    });
    cleanupAgentBridgeSession(session);
    scheduleAgentBridgeReconnect('error');
  });
}

/**
 * 受信イベントをバッファへ積み、短い間隔でバッチ配送を行う。
 * 混雑時は最古のイベントから破棄して最新状態の伝搬を優先する。
 */
function enqueueAgentEvent(event: MultiAgentEventPayload): void {
  if (agentEventQueue.length >= AGENT_EVENT_QUEUE_MAX_SIZE) {
    agentEventQueue.shift();
    logAgentBridgeEvent('warn', 'agent-bridge.queue.trimmed', {
      limit: AGENT_EVENT_QUEUE_MAX_SIZE,
      channel: event.channel,
      type: event.event,
    });
  }

  agentEventQueue.push(event);
  scheduleAgentEventFlush();
  ensureAgentBridgeSession('enqueue');
}

function scheduleAgentEventFlush(): void {
  if (agentBridgeBatchTimer) {
    return;
  }

  agentBridgeBatchTimer = setTimeout(() => {
    agentBridgeBatchTimer = null;
    void flushAgentEventQueue();
  }, AGENT_EVENT_BATCH_INTERVAL_MS);
}

/**
 * バッファに溜まったイベントをまとめて送信する。接続未確立の場合は再接続を促し、
 * 送信失敗時はキュー上限を守りながら再キューイングする。
 */
async function flushAgentEventQueue(): Promise<void> {
  if (agentBridgeFlushInFlight) {
    return agentBridgeFlushInFlight;
  }

  if (agentEventQueue.length === 0) {
    return;
  }

  if (!agentBridgeSocket || agentBridgeState !== 'connected' || agentBridgeSocket.readyState !== WebSocket.OPEN) {
    ensureAgentBridgeSession('flush-wait');
    scheduleAgentEventFlush();
    return;
  }

  const batch = agentEventQueue.splice(0, AGENT_EVENT_BATCH_MAX_SIZE);
  agentBridgeFlushInFlight = sendAgentEventBatch(batch)
    .catch((error) => {
      logAgentBridgeEvent('error', 'agent-bridge.batch.failed', {
        message: error instanceof Error ? error.message : String(error),
        batchSize: batch.length,
      });
      const availableSlots = Math.max(0, AGENT_EVENT_QUEUE_MAX_SIZE - agentEventQueue.length);
      if (availableSlots > 0) {
        agentEventQueue.unshift(...batch.slice(0, availableSlots));
      }
      scheduleAgentEventFlush();
    })
    .finally(() => {
      agentBridgeFlushInFlight = null;
      if (agentEventQueue.length > 0) {
        scheduleAgentEventFlush();
      }
    });

  return agentBridgeFlushInFlight;
}

/**
 * 送信タイムアウトとリトライ回数を考慮しながら、まとめてエンベロープを送る。
 * 成功すれば構造化ログで詳細を残し、失敗時は上位で再キューイングする。
 */
async function sendAgentEventBatch(batch: MultiAgentEventPayload[]): Promise<void> {
  const envelope: AgentEventEnvelope = { type: 'agentEvent', args: { events: batch } };
  const startedAt = Date.now();
  const maxAttempts = Math.max(1, AGENT_WS_MAX_RETRIES + 1);

  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    try {
      await sendThroughActiveSession(envelope);
      logAgentBridgeEvent('info', 'agent-bridge.batch.sent', {
        batchSize: batch.length,
        attempt,
        durationMs: Date.now() - startedAt,
      });
      return;
    } catch (error) {
      const isLastAttempt = attempt === maxAttempts;
      logAgentBridgeEvent(isLastAttempt ? 'error' : 'warn', 'agent-bridge.batch.retry', {
        attempt,
        maxAttempts,
        batchSize: batch.length,
        message: error instanceof Error ? error.message : String(error),
      });
      if (isLastAttempt) {
        throw error;
      }
      scheduleAgentBridgeReconnect('send-retry');
      await wait(AGENT_WS_RECONNECT_DELAY_MS);
    }
  }
}

function sendThroughActiveSession(payload: AgentEventEnvelope): Promise<void> {
  const session = agentBridgeSocket;
  if (!session || agentBridgeState !== 'connected' || session.readyState !== WebSocket.OPEN) {
    return Promise.reject(new Error('agent bridge is not connected'));
  }

  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      session.terminate();
      reject(new Error('agent bridge send timeout'));
    }, AGENT_WS_SEND_TIMEOUT_MS);

    session.send(JSON.stringify(payload), (error) => {
      clearTimeout(timeout);
      if (error) {
        reject(error);
        return;
      }
      resolve();
    });
  });
}

function wait(durationMs: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, durationMs));
}

/**
 * 役割変更要求を適用し、共有イベント向けにメタ情報を更新する。
 */
function applyAgentRoleUpdate(roleId: string, source: string, reason?: string): AgentRoleDescriptor {
  const descriptor = resolveAgentRole(roleId);
  agentRoleState.activeRole = descriptor;
  agentRoleState.lastEventId = randomUUID();
  agentRoleState.lastUpdatedAt = Date.now();
  console.log('[Role] switched agent role', {
    roleId: descriptor.id,
    label: descriptor.label,
    source,
    reason: reason ?? 'unspecified',
  });
  return descriptor;
}

/**
 * Python 側の LangGraph 共有メモリへイベントを伝搬する補助ユーティリティ。
 */
async function emitAgentEvent(event: MultiAgentEventPayload): Promise<void> {
  const summaryAttributes = {
    'agent_event.channel': event.channel,
    'agent_event.type': event.event,
    'agent_event.agent_id': event.agentId,
  };

  return runWithSpan(tracer, 'agent.bridge.emit', summaryAttributes, async (span) => {
    agentBridgeEventCounter.add(1, { channel: event.channel, event: event.event });
    const startedAt = Date.now();

    try {
      enqueueAgentEvent(event);
      logAgentBridgeEvent('info', 'agent-bridge.enqueued', {
        channel: event.channel,
        type: event.event,
        queueSize: agentEventQueue.length,
      });
      span.setAttribute('agent_event.queue_size', agentEventQueue.length);
    } catch (error) {
      span.setStatus({ code: SpanStatusCode.ERROR, message: error instanceof Error ? error.message : String(error) });
      logAgentBridgeEvent('error', 'agent-bridge.enqueue.failed', {
        message: error instanceof Error ? error.message : String(error),
        channel: event.channel,
        type: event.event,
      });
    } finally {
      const durationMs = Date.now() - startedAt;
      span.setAttribute('agent_event.latency_ms', durationMs);
    }
  });
}

/**
 * 直近の座標変化を検知して LangGraph 共有メモリへ送信する。
 */
async function broadcastAgentPosition(targetBot: Bot): Promise<void> {
  const { x, y, z } = targetBot.entity.position;
  const rounded = { x: Math.floor(x), y: Math.floor(y), z: Math.floor(z) };
  const previous = agentRoleState.lastBroadcastPosition;
  if (previous && previous.x === rounded.x && previous.y === rounded.y && previous.z === rounded.z) {
    return;
  }
  agentRoleState.lastBroadcastPosition = rounded;

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
 * 体力や満腹度の更新を LangGraph 側へ通知する。
 */
async function broadcastAgentStatus(targetBot: Bot, extraPayload: Record<string, unknown> = {}): Promise<void> {
  const health = Math.round(targetBot.health);
  const rawMaxHealth = Number((targetBot as Record<string, unknown>).maxHealth ?? 20);
  const maxHealth = Number.isFinite(rawMaxHealth) ? rawMaxHealth : 20;
  const food = Math.round(targetBot.food);
  const saturation = Number.isFinite(targetBot.foodSaturation)
    ? Math.round((targetBot.foodSaturation ?? 0) * 10) / 10
    : 0;

  await emitAgentEvent({
    channel: 'multi-agent',
    event: 'status',
    agentId: PRIMARY_AGENT_ID,
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
    configureMovementProfile(digFriendlyMovements, true);
    digPermissiveMovements = digFriendlyMovements;

    const cautiousMovementProfile = new MovementsWithData(targetBot, mcData);
    configureMovementProfile(cautiousMovementProfile, false);
    cautiousMovements = cautiousMovementProfile;

    // Paper 1.21.x ではパルクールやダッシュを多用すると "moved wrongly" 警告が増えるが、
    // 危険地帯での生存性を優先して俊敏な動きを維持したいので、敢えて高機動モードを維持する。
    targetBot.pathfinder.setMovements(cautiousMovementProfile);
    console.log('[Bot] movement profiles initialized (cautious default / digging fallback).');
    targetBot.chat('起動しました。（Mineflayer）');
    void broadcastAgentStatus(targetBot, { lifecycle: 'spawn' });
    void broadcastAgentPosition(targetBot);
  });

  targetBot.on('health', () => {
    void monitorCriticalHunger(targetBot);
    void broadcastAgentStatus(targetBot);
  });

  targetBot.on('move', () => {
    void broadcastAgentPosition(targetBot);
  });

  // サーバーから強制移動が通知された場合はタイムスタンプを更新し、
  // moveTo コマンド側で直近発生の有無を基準にリトライを判断する。
  targetBot.on('forcedMove', () => {
    const now = Date.now();
    lastForcedMoveAt = now;

    if (now - lastForcedMoveLoggedAt >= 1_000) {
      console.warn('[Bot] server corrected our position (forcedMove). Monitoring for retries.');
      lastForcedMoveLoggedAt = now;
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
      bot = null;
      // Mineflayer は接続失敗時に error->end の順でイベントが発生するため、早期にリトライを予約する。
      scheduleReconnect('connection_error');
    }
  });

  targetBot.once('kicked', (reason) => {
    console.warn(`[Bot] kicked from server: ${reason}. Retrying in ${MC_RECONNECT_DELAY_MS}ms.`);
    bot = null;
    scheduleReconnect('kicked');
  });

  targetBot.once('end', (reason) => {
    console.warn(`[Bot] disconnected (${String(reason ?? 'unknown reason')}). Retrying in ${MC_RECONNECT_DELAY_MS}ms.`);
    bot = null;
    scheduleReconnect('ended');
  });
}

/**
 * Bot が切断された場合に再接続を予約する。重複予約を防ぐため、既存タイマーを考慮する。
 */
function scheduleReconnect(reason: string = 'unknown'): void {
  if (reconnectTimer) {
    return;
  }

  reconnectCounter.add(1, { reason });

  tracer.startActiveSpan(
    'mineflayer.reconnect.schedule',
    { attributes: { 'reconnect.delay_ms': MC_RECONNECT_DELAY_MS, 'reconnect.reason': reason } },
    (span) => {
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        startBotLifecycle();
      }, MC_RECONNECT_DELAY_MS);
      span.end();
    },
  );
}

// 初回接続を起動
startBotLifecycle();

// LangGraph 共有イベント用の WebSocket セッションを先に確立し、初回イベント配送の待ち時間を抑える。
ensureAgentBridgeSession('startup');

if (SKILL_HISTORY_PATH) {
  void ensureSkillHistorySink();
}

/**
 * コマンド実行時に利用可能な Bot インスタンスを取得する。未接続の場合は null を返す。
 */
function getActiveBot(): Bot | null {
  if (!bot) {
    console.warn('[Bot] command requested but bot instance is not ready yet.');
    return null;
  }

  // entity が未定義の間はまだスポーン完了前なので、チャットや移動を実行しない。
  if (!bot.entity) {
    console.warn('[Bot] command requested but spawn sequence has not completed.');
    return null;
  }

  return bot;
}

// ---- WebSocket サーバ（Python -> Node） ----
// Docker ブリッジ越しの Python エージェントが接続できるよう、host/port を明示的に指定する。
const wss = new WebSocketServer({ host: WS_HOST, port: WS_PORT });
console.log(`[WS] listening on ws://${WS_HOST}:${WS_PORT}`);

// ---- WebSocket コマンド処理 ----
// 1 接続につき 1 コマンドというシンプル設計。必要に応じて永続接続へ拡張予定。
wss.on('connection', (ws: WebSocket, request) => {
  const clientId = randomUUID();
  const remoteAddress = `${request.socket.remoteAddress ?? 'unknown'}:${request.socket.remotePort ?? 'unknown'}`;

  console.log(`[WS] connection opened id=${clientId} from ${remoteAddress}`);

  ws.on('message', async (raw) => {
    const rawText = raw.toString();
    // OpenTelemetry: 受信した単発コマンドの処理時間と結果を 1 span に集約して計測する。
    try {
      await runWithSpan(
        tracer,
        'websocket.message',
        {
          'ws.client_id': clientId,
          'ws.remote_address': remoteAddress,
          'ws.payload_length': rawText.length,
        },
        async (span) => {
          console.log(`[WS] (${clientId}) received payload: ${rawText}`);
          const response = await handleIncomingMessage(raw);
          span.setAttribute('ws.response_ok', response.ok);
          if (!response.ok) {
            span.setStatus({ code: SpanStatusCode.ERROR, message: response.error ?? 'WS command failed' });
          }
          console.log(`[WS] (${clientId}) sending response: ${JSON.stringify(response)}`);
          ws.send(JSON.stringify(response));
        },
      );
    } catch (error) {
      console.error('[WS] failed to process message span', error);
    }
  });

  ws.on('close', (code, reason) => {
    const readableReason = reason.toString() || 'no reason';
    console.log(`[WS] connection closed id=${clientId} code=${code} reason=${readableReason}`);
  });

  ws.on('error', (error) => {
    console.error(`[WS] connection error id=${clientId}`, error);
  });
});

// ---- コマンド処理関数 ----
// 受信したデータをバリデーションし、対応するアクションを実行する。
async function handleIncomingMessage(raw: RawData): Promise<CommandResponse> {
  try {
    const payload = JSON.parse(raw.toString()) as CommandPayload;
    return await executeCommand(payload);
  } catch (error) {
    console.error('[WS] invalid payload', error);
    return { ok: false, error: 'Invalid payload format' };
  }
}

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

// ---- skill コマンド処理 ----
function handleRegisterSkillCommand(args: Record<string, unknown>): CommandResponse {
  const skillId = typeof args.skillId === 'string' ? args.skillId.trim() : '';
  const title = typeof args.title === 'string' ? args.title.trim() : '';
  const description = typeof args.description === 'string' ? args.description.trim() : '';
  const stepsRaw = Array.isArray(args.steps) ? args.steps : [];
  const steps: string[] = stepsRaw
    .filter((step): step is string => typeof step === 'string' && step.trim().length > 0)
    .map((step) => step.trim());
  const tagsRaw = Array.isArray(args.tags) ? args.tags : [];
  const tags: string[] = tagsRaw
    .filter((tag): tag is string => typeof tag === 'string' && tag.trim().length > 0)
    .map((tag) => tag.trim());

  if (!skillId || !title || !description || steps.length === 0) {
    return { ok: false, error: 'Invalid skill registration payload' };
  }

  const record: RegisteredSkill = {
    id: skillId,
    title,
    description,
    steps,
    tags,
    createdAt: Date.now(),
  };

  skillRegistry.set(skillId, record);
  logSkillEvent('info', 'skill.registered', {
    skillId,
    title,
    stepCount: steps.length,
    tags,
  });

  return { ok: true, data: { registered: true } };
}

function handleInvokeSkillCommand(args: Record<string, unknown>): CommandResponse {
  const skillId = typeof args.skillId === 'string' ? args.skillId.trim() : '';
  const context = typeof args.context === 'string' ? args.context : '';

  if (!skillId) {
    return { ok: false, error: 'skillId is required' };
  }

  const record = skillRegistry.get(skillId);
  if (!record) {
    logSkillEvent('warn', 'skill.invoke.missing', { skillId, context });
    return { ok: false, error: `Skill ${skillId} is not registered` };
  }

  logSkillEvent('info', 'skill.invoke', {
    skillId,
    title: record.title,
    context,
    stepCount: record.steps.length,
  });

  const activeBot = getActiveBot();
  if (activeBot) {
    activeBot.chat(
      `[Skill] ${record.title} を再生します。登録ステップ数: ${record.steps.length}`,
    );
  }

  return { ok: true, data: { steps: record.steps } };
}

function handleSkillExploreCommand(args: Record<string, unknown>): CommandResponse {
  const skillId = typeof args.skillId === 'string' ? args.skillId.trim() : '';
  const description = typeof args.description === 'string' ? args.description.trim() : '';
  const context = typeof args.context === 'string' ? args.context : '';

  if (!skillId || !description) {
    return { ok: false, error: 'Invalid exploration payload' };
  }

  logSkillEvent('info', 'skill.explore', {
    skillId,
    description,
    context,
  });

  const activeBot = getActiveBot();
  if (activeBot) {
    activeBot.chat(`[Skill] ${skillId} の探索を開始します。ヒント: ${description}`);
  }

  return { ok: true, data: { exploring: skillId } };
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

/**
 * 指定時間だけ待機して非同期処理のタイミングを調整する汎用ユーティリティ。
 *
 * Mineflayer の pathfinder は連続した再探索を短時間で要求すると負荷が高くなるため、
 * リトライ前に短い休止を挟んでサーバーの位置補正完了を待つ目的で利用する。
 */
function delay(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

/**
 * Mineflayer の経路探索に用いる Movements のパラメータを統一的に調整する。
 *
 * ここで掘削可否や移動コストを明示的に設定しておくことで、
 * 既存の pathfinder.goto 呼び出し側が余計な知識を持たずに済む。
 */
function configureMovementProfile(movements: MovementsClass, allowDigging: boolean): void {
  const mutable = movements as MutableMovements;
  mutable.allowParkour = true;
  mutable.allowSprinting = true;
  mutable.canDig = allowDigging;

  if (allowDigging) {
    mutable.digCost = DIGGING_ENABLED_COST;
    return;
  }

  // 掘削不可の状態では掘る場合のコストを大きく設定し、AI が安易に壁を壊す選択を避ける。
  const currentCost = mutable.digCost ?? DIGGING_ENABLED_COST;
  mutable.digCost = Math.max(currentCost, DIGGING_DISABLED_COST);
}

/**
 * moveTo コマンドで利用する到達許容距離（ブロック数）。
 *
 * Mineflayer の GoalBlock は指定ブロックへ完全一致しないと完了扱いにならず、
 * ブロックの段差や水流の影響で「目的地に着いたのに失敗扱い」になるケースが多い。
 * GoalNear を用いることで ±3 ブロックの範囲を許容し、柔軟に到着完了判定を行う。
 */

/**
 * GoalNear の許容距離を状況に応じて補正する。
 *
 * 梯子やツタを上る際に y 軸方向の差が 2 以上残っている段階で完了扱いになると、
 * bot が入力を解除して落下してしまう。そのため縦方向の移動量が大きい場合は
 * 許容範囲を 1 ブロックへ絞り、登り切るまで入力を維持させる。
 */
function resolveGoalNearTolerance(targetBot: Bot, target: { x: number; y: number; z: number }): number {
  const entity = targetBot.entity;

  if (!entity) {
    return MOVE_GOAL_TOLERANCE;
  }

  const verticalGap = Math.abs(target.y - entity.position.y);

  if (verticalGap >= 2) {
    const tightenedTolerance = Math.min(MOVE_GOAL_TOLERANCE, 1);
    return Math.max(1, tightenedTolerance);
  }

  return MOVE_GOAL_TOLERANCE;
}

/**
 * forcedMove 発生直後に GoalChanged 例外が出た場合は再試行可能と判断するヘルパー。
 *
 * GoalChanged は pathfinder.goto 実行中に別のゴール設定が入ったときにも出るため、
 * 強制移動が直近で起きたかどうかをタイムスタンプで確認し誤検出を防ぐ。
 */
function shouldRetryDueToForcedMove(error: unknown): boolean {
  if (Date.now() - lastForcedMoveAt > FORCED_MOVE_RETRY_WINDOW_MS) {
    return false;
  }

  const message = error instanceof Error ? error.message : String(error);
  return message.includes('GoalChanged');
}

/**
 * mineflayer-pathfinder が到達経路を見つけられなかった際の例外かどうかを判別する。
 *
 * 表記ゆれ（"No path"・"No path to goal" 等）を包含するため、小文字化した部分一致で判定する。
 */
function isNoPathError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error);
  return message.toLowerCase().includes('no path');
}

/**
 * 指定した Movements プロファイルを適用した状態で pathfinder.goto を実行し、
 * forcedMove に伴う GoalChanged エラーが発生した場合は所定回数リトライする。
 */
async function gotoWithForcedMoveRetry(
  targetBot: Bot,
  goal: InstanceType<typeof goals.GoalNear>,
  movements: MovementsClass,
): Promise<void> {
  const { pathfinder: activePathfinder } = targetBot;
  const previousMovements = activePathfinder.movements;
  const shouldRestoreMovements = previousMovements !== movements;

  if (shouldRestoreMovements) {
    activePathfinder.setMovements(movements);
  }

  try {
    for (let attempt = 0; attempt <= FORCED_MOVE_MAX_RETRIES; attempt++) {
      try {
        await activePathfinder.goto(goal);
        return;
      } catch (error) {
        if (shouldRetryDueToForcedMove(error) && attempt < FORCED_MOVE_MAX_RETRIES) {
          console.warn(
            `[MoveToCommand] retrying due to forcedMove correction (attempt ${attempt + 1}/${FORCED_MOVE_MAX_RETRIES})`,
          );
          await delay(FORCED_MOVE_RETRY_DELAY_MS);
          continue;
        }

        throw error;
      }
    }
  } finally {
    if (shouldRestoreMovements) {
      activePathfinder.setMovements(previousMovements);
    }
  }

  throw new Error('Pathfinding failed after forcedMove retries');
}

async function handleMoveToCommand(args: Record<string, unknown>): Promise<CommandResponse> {
  const x = Number(args.x);
  const y = Number(args.y);
  const z = Number(args.z);

  if ([x, y, z].some((value) => Number.isNaN(value))) {
    console.warn('[MoveToCommand] invalid coordinate(s) detected', { x, y, z });
    return { ok: false, error: 'Invalid coordinates' };
  }

  const activeBot = getActiveBot();

  if (!activeBot) {
    console.warn('[MoveToCommand] rejected because bot is unavailable');
    return { ok: false, error: 'Bot is not connected to the Minecraft server yet' };
  }

  lastMoveTarget = { x, y, z };
  const tolerance = resolveGoalNearTolerance(activeBot, { x, y, z });
  const goal = new goals.GoalNear(x, y, z, tolerance);
  const preferredMovements = cautiousMovements ?? activeBot.pathfinder.movements;
  const fallbackMovements = digPermissiveMovements;

  try {
    await gotoWithForcedMoveRetry(activeBot, goal, preferredMovements);
    const { position } = activeBot.entity;
    console.log(
      `[MoveToCommand] pathfinder completed near (${x}, ${y}, ${z}) actual=(${position.x.toFixed(2)}, ${position.y.toFixed(2)}, ${position.z.toFixed(2)}) tolerance=${tolerance} profile=cautious`,
    );
    return { ok: true };
  } catch (primaryError) {
    if (isNoPathError(primaryError) && fallbackMovements) {
      console.warn(
        '[MoveToCommand] no walkable route found without digging. Retrying with digging-enabled fallback profile.',
      );

      try {
        await gotoWithForcedMoveRetry(activeBot, goal, fallbackMovements);
        const { position } = activeBot.entity;
        console.log(
          `[MoveToCommand] fallback pathfinder completed near (${x}, ${y}, ${z}) actual=(${position.x.toFixed(2)}, ${position.y.toFixed(2)}, ${position.z.toFixed(2)}) tolerance=${tolerance} profile=dig-enabled`,
        );
        return { ok: true };
      } catch (fallbackError) {
        console.error('[Pathfinder] dig-enabled fallback also failed', fallbackError);
        return { ok: false, error: 'Pathfinding failed' };
      }
    }

    console.error('[Pathfinder] failed to move', primaryError);
    return { ok: false, error: 'Pathfinding failed' };
  }
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

  const foundPositions: Vec3[] = activeBot.findBlocks({
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
    const movements = digPermissiveMovements ?? activeBot.pathfinder.movements;

    try {
      await gotoWithForcedMoveRetry(activeBot, goal, movements);
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
      lastMoveTarget = { x: position.x, y: position.y, z: position.z };
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

type EquipDestination = 'hand' | 'off-hand';

const EQUIP_TOOL_MATCHERS: Record<string, (item: Item) => boolean> = {
  pickaxe: (item) => item.name.endsWith('_pickaxe'),
  sword: (item) => item.name.endsWith('_sword'),
  axe: (item) => item.name.endsWith('_axe') && !item.name.endsWith('_pickaxe'),
  shovel: (item) => item.name.endsWith('_shovel') || item.name.endsWith('_spade'),
  hoe: (item) => item.name.endsWith('_hoe'),
  shield: (item) => item.name === 'shield',
  torch: (item) => item.name === 'torch',
};

/**
 * equipItem コマンドで渡された語を Mineflayer のアイテム名と整合する形式へ正規化する。
 */
function normalizeEquipToken(value: string): string {
  const trimmed = value.trim().toLowerCase();
  const withUnderscore = trimmed.replace(/\s+/g, '_');
  return withUnderscore.replace(/[^a-z0-9_]/g, '');
}

/**
 * ツール種別からインベントリ内の一致するアイテムを探索する。
 */
function findInventoryItemByToolType(targetBot: Bot, toolTypeRaw: string): Item | null {
  const matcher = EQUIP_TOOL_MATCHERS[toolTypeRaw.toLowerCase()];

  if (!matcher) {
    return null;
  }

  return targetBot.inventory.items().find((item) => matcher(item)) ?? null;
}

/**
 * 任意のアイテム名から対応するインベントリアイテムを推測する。
 */
function findInventoryItemByName(targetBot: Bot, itemNameRaw: string): Item | null {
  const normalized = normalizeEquipToken(itemNameRaw);
  const items = targetBot.inventory.items();

  const byName = items.find((item) => normalizeEquipToken(item.name) === normalized);
  if (byName) {
    return byName;
  }

  const byDisplay = items.find((item) => normalizeEquipToken(item.displayName) === normalized);
  if (byDisplay) {
    return byDisplay;
  }

  return (
    items.find((item) => normalizeEquipToken(item.name).includes(normalized)) ??
    items.find((item) => normalizeEquipToken(item.displayName).includes(normalized)) ??
    null
  );
}

/**
 * equipItem コマンドを処理し、指定された装備を右手または左手へ持ち替える。
 */
async function handleEquipItemCommand(args: Record<string, unknown>): Promise<CommandResponse> {
  const toolTypeRaw = typeof args.toolType === 'string' ? args.toolType : undefined;
  const itemNameRaw = typeof args.itemName === 'string' ? args.itemName : undefined;
  const destinationRaw = typeof args.destination === 'string' ? args.destination : 'hand';
  const destination: EquipDestination = destinationRaw === 'off-hand' ? 'off-hand' : 'hand';

  if (!toolTypeRaw && !itemNameRaw) {
    return { ok: false, error: 'Either toolType or itemName must be provided' };
  }

  const activeBot = getActiveBot();

  if (!activeBot) {
    console.warn('[EquipItemCommand] rejected because bot is unavailable');
    return { ok: false, error: 'Bot is not connected to the Minecraft server yet' };
  }

  let targetItem: Item | null = null;

  if (itemNameRaw) {
    targetItem = findInventoryItemByName(activeBot, itemNameRaw);
  }

  if (!targetItem && toolTypeRaw) {
    targetItem = findInventoryItemByToolType(activeBot, toolTypeRaw);
  }

  if (!targetItem) {
    console.warn('[EquipItemCommand] requested item not found', { toolTypeRaw, itemNameRaw });
    return { ok: false, error: 'Requested item is not available in inventory' };
  }

  try {
    await activeBot.equip(targetItem, destination);
    console.log(
      `[EquipItemCommand] equipped ${targetItem.displayName ?? targetItem.name} to ${destination}`,
    );
    return { ok: true };
  } catch (error) {
    console.error('[EquipItemCommand] failed to equip item', error);
    return { ok: false, error: 'Failed to equip item' };
  }
}

/**
 * gatherStatus コマンドを処理し、移動前に必要な情報を即時収集する。
 *
 * Mineflayer から取得可能なステータスを種類ごとに切り分け、Python 側が
 * プレイヤーへ質問せずとも意思決定できるように集約する。
 */
async function handleGatherStatusCommand(args: Record<string, unknown>): Promise<CommandResponse> {
  const kindRaw = typeof args.kind === 'string' ? args.kind.trim().toLowerCase() : '';
  const supportedKinds: GatherStatusKind[] = ['position', 'inventory', 'general'];
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
  const velocity = entity.velocity ?? ({ x: 0, y: 0, z: 0 } as Vec3);
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
  if (CONTROL_MODE !== 'vpt') {
    return { ok: false, error: 'CONTROL_MODE=vpt ではないため VPT 再生は無効化されています。' };
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

  if (sanitized.length > VPT_MAX_SEQUENCE_LENGTH) {
    return {
      ok: false,
      error: `actions length exceeds limit (${sanitized.length} > ${VPT_MAX_SEQUENCE_LENGTH})`,
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
  return delay(clamped * VPT_TICK_INTERVAL_MS);
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
  };
}

function evaluateDigPermission(targetBot: Bot): DigPermissionSnapshot {
  const gameMode = targetBot.game.gameMode ?? 'survival';
  const fallbackMovements = digPermissiveMovements as MutableMovements | null;
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

    const ws = new WebSocket(AGENT_WS_URL);
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
