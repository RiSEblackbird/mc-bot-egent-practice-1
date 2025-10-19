// 日本語コメント：Mineflayer ボット（WSコマンド受信）
// 役割：Python からの JSON コマンドを実ゲーム操作へ変換する
import { createBot, Bot } from 'mineflayer';
import type { Item } from 'prismarine-item';
import type { Vec3 } from 'vec3';
// mineflayer-pathfinder は CommonJS 形式のため、ESM 環境では一度デフォルトインポートしてから必要要素を取り出す。
// そうしないと Node.js 実行時に named export の解決に失敗するため、本構成では明示的な分割代入を採用する。
import mineflayerPathfinder from 'mineflayer-pathfinder';
import type { Movements as MovementsClass } from 'mineflayer-pathfinder';
import minecraftData from 'minecraft-data';
import { randomUUID } from 'node:crypto';
import { WebSocketServer, WebSocket, RawData } from 'ws';
import { loadBotRuntimeConfig } from './runtime/config.js';
import { CUSTOM_SLOT_PATCH } from './runtime/slotPatch.js';
import {
  AgentRoleDescriptor,
  AgentRoleState,
  createInitialAgentRoleState,
  resolveAgentRole,
} from './runtime/roles.js';

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

const MC_VERSION = runtimeConfig.minecraft.version;
const MC_HOST = runtimeConfig.minecraft.host;
const MC_PORT = runtimeConfig.minecraft.port;
const BOT_USERNAME = runtimeConfig.minecraft.username;
const AUTH_MODE = runtimeConfig.minecraft.authMode;
const MC_RECONNECT_DELAY_MS = runtimeConfig.minecraft.reconnectDelayMs;
const WS_HOST = runtimeConfig.websocket.host;
const WS_PORT = runtimeConfig.websocket.port;
const AGENT_WS_URL = runtimeConfig.agentBridge.url;
const MOVE_GOAL_TOLERANCE = runtimeConfig.moveGoalTolerance.tolerance;
const MINING_APPROACH_TOLERANCE = 1;

// ---- 型定義 ----
// 受信するコマンド種別のユニオン。追加実装時はここを拡張する。
type CommandType =
  | 'chat'
  | 'moveTo'
  | 'equipItem'
  | 'gatherStatus'
  | 'mineOre'
  | 'setAgentRole';

// WebSocket で受信するメッセージの基本形。
interface CommandPayload {
  type: CommandType;
  args: Record<string, unknown>;
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
  args: { event: MultiAgentEventPayload };
}

type GatherStatusKind = 'position' | 'inventory' | 'general';

interface PositionSnapshot {
  kind: 'position';
  position: { x: number; y: number; z: number };
  dimension: string;
  formatted: string;
}

interface InventoryItemSnapshot {
  slot: number;
  name: string;
  displayName: string;
  count: number;
  enchantments: string[];
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
}

/**
 * 現在の役割ステートを読み出すヘルパー。
 */
function getActiveAgentRole(): AgentRoleDescriptor {
  return agentRoleState.activeRole;
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
  return new Promise((resolve) => {
    const envelope: AgentEventEnvelope = { type: 'agentEvent', args: { event } };
    const ws = new WebSocket(AGENT_WS_URL);
    const timeout = setTimeout(() => {
      console.warn('[AgentEvent] bridge timeout reached, terminating connection');
      ws.terminate();
      resolve();
    }, 5_000);

    const cleanup = () => {
      clearTimeout(timeout);
      ws.removeAllListeners();
      resolve();
    };

    ws.once('open', () => {
      ws.send(JSON.stringify(envelope));
    });

    ws.once('message', () => {
      ws.close();
      cleanup();
    });

    ws.once('close', () => {
      cleanup();
    });

    ws.once('error', (error) => {
      console.error('[AgentEvent] failed to deliver event', error);
      cleanup();
    });
  }).catch((error) => {
    console.error('[AgentEvent] unexpected error while emitting event', error);
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
      scheduleReconnect();
    }
  });

  targetBot.once('kicked', (reason) => {
    console.warn(`[Bot] kicked from server: ${reason}. Retrying in ${MC_RECONNECT_DELAY_MS}ms.`);
    bot = null;
    scheduleReconnect();
  });

  targetBot.once('end', (reason) => {
    console.warn(`[Bot] disconnected (${String(reason ?? 'unknown reason')}). Retrying in ${MC_RECONNECT_DELAY_MS}ms.`);
    bot = null;
    scheduleReconnect();
  });
}

/**
 * Bot が切断された場合に再接続を予約する。重複予約を防ぐため、既存タイマーを考慮する。
 */
function scheduleReconnect(): void {
  if (reconnectTimer) {
    return;
  }

  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    startBotLifecycle();
  }, MC_RECONNECT_DELAY_MS);
}

// 初回接続を起動
startBotLifecycle();

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
    console.log(`[WS] (${clientId}) received payload: ${rawText}`);
    const response = await handleIncomingMessage(raw);
    console.log(`[WS] (${clientId}) sending response: ${JSON.stringify(response)}`);
    ws.send(JSON.stringify(response));
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
  const { type, args } = payload;

  console.log(`[WS] executing command type=${type}`);

  switch (type) {
    case 'chat':
      return handleChatCommand(args);
    case 'moveTo':
      return handleMoveToCommand(args);
    case 'equipItem':
      return handleEquipItemCommand(args);
    case 'gatherStatus':
      return handleGatherStatusCommand(args);
    case 'mineOre':
      return handleMineOreCommand(args);
    case 'setAgentRole':
      return handleSetAgentRoleCommand(args);
    default: {
      const exhaustiveCheck: never = type;
      void exhaustiveCheck;
      return { ok: false, error: 'Unknown command type' };
    }
  }
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

function buildInventorySnapshot(targetBot: Bot): InventorySnapshot {
  const rawItems = targetBot.inventory.items();
  const totalSlots = targetBot.inventory.slots.length;
  const occupiedSlots = rawItems.length;
  const items = rawItems.map((item) => ({
    slot: item.slot,
    name: item.name,
    displayName: item.displayName,
    count: item.count,
    enchantments: describeEnchantments(item),
  }));
  const pickaxeItems = rawItems.filter((item) => EQUIP_TOOL_MATCHERS.pickaxe(item));
  const pickaxes = pickaxeItems.map((item) => ({
    slot: item.slot,
    name: item.name,
    displayName: item.displayName,
    count: item.count,
    enchantments: describeEnchantments(item),
  }));

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
