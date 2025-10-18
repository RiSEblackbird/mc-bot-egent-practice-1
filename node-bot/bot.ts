// 日本語コメント：Mineflayer ボット（WSコマンド受信）
// 役割：Python からの JSON コマンドを実ゲーム操作へ変換する
import { createBot, Bot } from 'mineflayer';
import type { Item } from 'prismarine-item';
// mineflayer-pathfinder は CommonJS 形式のため、ESM 環境では一度デフォルトインポートしてから必要要素を取り出す。
// そうしないと Node.js 実行時に named export の解決に失敗するため、本構成では明示的な分割代入を採用する。
import mineflayerPathfinder from 'mineflayer-pathfinder';
import type { Movements as MovementsClass } from 'mineflayer-pathfinder';
import minecraftData from 'minecraft-data';
import { randomUUID } from 'node:crypto';
import { WebSocketServer, WebSocket, RawData } from 'ws';
import {
  detectDockerRuntime,
  parseEnvInt,
  resolveMinecraftHostValue,
  resolveMoveGoalTolerance,
} from './runtime/env.js';
import { CUSTOM_SLOT_PATCH } from './runtime/slotPatch.js';

// 型情報を維持するため、実体の分割代入時にモジュール全体の型定義を参照させる。
const { pathfinder, Movements, goals } = mineflayerPathfinder as typeof import('mineflayer-pathfinder');

// ---- Minecraft プロトコル差分パッチ ----
// 詳細な Slot 構造体の上書きロジックは runtime/slotPatch.ts に切り出し、複数バージョンへ一括適用する。

// ---- プロトコルバージョン制御 ----
// Paper サーバーと Mineflayer の既定バージョン整合性を保つため、ここで既定値を一元管理する。
const DEFAULT_MC_VERSION = '1.21.1';
const SUPPORTED_MINECRAFT_VERSIONS = new Set(
  minecraftData.versions.pc.map((version) => version.minecraftVersion),
);

interface MinecraftVersionResolution {
  version: string | undefined;
  warnings: string[];
}

/**
 * Mineflayer が接続時に利用するプロトコルバージョンを決定する。
 * サーバーとの不整合で PartialReadError が発生しないよう、minecraft-data が認識するラベルへ正規化する。
 */
function resolveMinecraftVersionLabel(requestedVersionRaw: string | undefined): MinecraftVersionResolution {
  const warnings: string[] = [];
  const sanitized = (requestedVersionRaw ?? '').trim();

  if (sanitized.length === 0) {
    if (SUPPORTED_MINECRAFT_VERSIONS.has(DEFAULT_MC_VERSION)) {
      warnings.push(
        `環境変数 MC_VERSION が未設定のため、既定プロトコル ${DEFAULT_MC_VERSION} を利用します。`,
      );
      return { version: DEFAULT_MC_VERSION, warnings };
    }

    warnings.push(
      `環境変数 MC_VERSION が未設定ですが、既定プロトコル ${DEFAULT_MC_VERSION} が minecraft-data へ登録されていないため Mineflayer の自動判別に委ねます。`,
    );
    return { version: undefined, warnings };
  }

  if (SUPPORTED_MINECRAFT_VERSIONS.has(sanitized)) {
    return { version: sanitized, warnings };
  }

  if (SUPPORTED_MINECRAFT_VERSIONS.has(DEFAULT_MC_VERSION)) {
    warnings.push(
      `MC_VERSION='${sanitized}' は minecraft-data の対応一覧に存在しないため ${DEFAULT_MC_VERSION} へフォールバックします。`,
    );
    return { version: DEFAULT_MC_VERSION, warnings };
  }

  warnings.push(
    `MC_VERSION='${sanitized}' は minecraft-data の対応一覧に存在せず、既定プロトコル ${DEFAULT_MC_VERSION} も見つからないため Mineflayer の自動判別にフォールバックします。`,
  );
  return { version: undefined, warnings };
}

// ---- 型定義 ----
// 受信するコマンド種別のユニオン。追加実装時はここを拡張する。
type CommandType = 'chat' | 'moveTo';

// WebSocket で受信するメッセージの基本形。
interface CommandPayload {
  type: CommandType;
  args: Record<string, unknown>;
}

// 成功・失敗を Python 側へ返すためのレスポンス型。
interface CommandResponse {
  ok: boolean;
  error?: string;
}

interface FoodInfo {
  // minecraft-data 側の構造体では foodPoints / saturation 等が格納されている。
  // 本エージェントでは存在確認のみ行うため、詳細なフィールド定義は必須ではない。
  foodPoints?: number;
  saturation?: number;
}

type FoodDictionary = Record<string, FoodInfo>;

// ---- 環境変数・定数設定 ----
const versionResolution = resolveMinecraftVersionLabel(process.env.MC_VERSION);
for (const warning of versionResolution.warnings) {
  console.warn(`[Bot] ${warning}`);
}

const MC_VERSION = versionResolution.version;

const dockerDetected = detectDockerRuntime();
const hostResolution = resolveMinecraftHostValue(process.env.MC_HOST, dockerDetected);

if (hostResolution.usedDockerFallback && hostResolution.originalValue.length > 0) {
  console.warn(
    '[Bot] MC_HOST points to localhost inside Docker. Falling back to host.docker.internal so the Paper server is reachable.',
  );
}

const MC_HOST = hostResolution.host;
const MC_PORT = parseEnvInt(process.env.MC_PORT, 25565);
const BOT_USERNAME = process.env.BOT_USERNAME ?? 'HelperBot';
const AUTH_MODE = (process.env.AUTH_MODE ?? 'offline') as 'offline' | 'microsoft';
// WebSocket 接続に関する構成。Docker ネットワーク上でも受信できるよう 0.0.0.0 を既定にする。
const WS_HOST = process.env.WS_HOST ?? '0.0.0.0';
const WS_PORT = parseEnvInt(process.env.WS_PORT, 8765);
const MC_RECONNECT_DELAY_MS = parseEnvInt(process.env.MC_RECONNECT_DELAY_MS, 5000);

// Python 側のエージェント WebSocket サーバーへチャットを転送するための接続設定。
const rawAgentUrl = (process.env.AGENT_WS_URL ?? '').trim();
const rawAgentHost = (process.env.AGENT_WS_HOST ?? '').trim();
const rawAgentPort = (process.env.AGENT_WS_PORT ?? '').trim();
const agentPort = parseEnvInt(rawAgentPort, 9000);
const defaultAgentHost = rawAgentHost && rawAgentHost !== '0.0.0.0'
  ? rawAgentHost
  : dockerDetected
    ? 'python-agent'
    : '127.0.0.1';
const AGENT_WS_URL = rawAgentUrl.length > 0 ? rawAgentUrl : `ws://${defaultAgentHost}:${agentPort}`;

// GoalNear の許容距離は LLM の挙動やステージ規模に合わせて調整できるよう環境変数化する。
const moveGoalToleranceResolution = resolveMoveGoalTolerance(process.env.MOVE_GOAL_TOLERANCE);
for (const warning of moveGoalToleranceResolution.warnings) {
  console.warn(`[Bot] ${warning}`);
}
const MOVE_GOAL_TOLERANCE = moveGoalToleranceResolution.tolerance;

// ---- Mineflayer ボット本体のライフサイクル管理 ----
// 接続失敗時にリトライするため、Bot インスタンスは都度生成し直す。
let bot: Bot | null = null;
let reconnectTimer: NodeJS.Timeout | null = null;
let cachedFoodsByName: FoodDictionary = {};
let isConsumingFood = false;
let lastHungerWarningAt = 0;

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
 * Bot ごとに必要なイベントハンドラを登録し、切断時には再接続をスケジュールする。
 */
function registerBotEventHandlers(targetBot: Bot): void {
  targetBot.once('spawn', () => {
    const mcData = minecraftData(targetBot.version);
    cachedFoodsByName = ((mcData as unknown as { foodsByName?: FoodDictionary }).foodsByName) ?? {};
    // 型定義上は第2引数が未定義だが、実実装では mcData を渡すのが推奨されているため、コンストラクタ型を拡張して使用する。
    const MovementsWithData = Movements as unknown as new (bot: Bot, data: ReturnType<typeof minecraftData>) => MovementsClass;
    const defaultMove = new MovementsWithData(targetBot, mcData);
    targetBot.pathfinder.setMovements(defaultMove);
    targetBot.chat('起動しました。（Mineflayer）');
  });

  targetBot.on('health', () => {
    void monitorCriticalHunger(targetBot);
  });

  targetBot.on('chat', (username: string, message: string) => {
    if (username === targetBot.username) return;
    // 受信したチャット内容を詳細ログへ出力し、
    // 「チャットは届いているが自動処理は未実装」である点を開発者へ明示する。
    console.info(`[Chat] <${username}> ${message}`);
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
 * moveTo コマンドで利用する到達許容距離（ブロック数）。
 *
 * Mineflayer の GoalBlock は指定ブロックへ完全一致しないと完了扱いにならず、
 * ブロックの段差や水流の影響で「目的地に着いたのに失敗扱い」になるケースが多い。
 * GoalNear を用いることで ±MOVE_GOAL_TOLERANCE ブロックの範囲を許容し、
 * 柔軟に到着完了判定を行う。環境変数で調整可能にしたため、用途に応じて
 * 1 ～ 30 の範囲でしきい値をカスタマイズできる。
 */
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

  const goal = new goals.GoalNear(x, y, z, MOVE_GOAL_TOLERANCE);
  try {
    await activeBot.pathfinder.goto(goal);
    const { position } = activeBot.entity;
    console.log(
      `[MoveToCommand] pathfinder completed near (${x}, ${y}, ${z}) actual=(${position.x.toFixed(
        2,
      )}, ${position.y.toFixed(2)}, ${position.z.toFixed(2)}) tolerance=${MOVE_GOAL_TOLERANCE}`,
    );
    return { ok: true };
  } catch (error) {
    console.error('[Pathfinder] failed to move', error);
    return { ok: false, error: 'Pathfinding failed' };
  }
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
