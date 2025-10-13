// 日本語コメント：Mineflayer ボット（WSコマンド受信）
// 役割：Python からの JSON コマンドを実ゲーム操作へ変換する
import { createBot, Bot } from 'mineflayer';
// mineflayer-pathfinder は CommonJS 形式のため、ESM 環境では一度デフォルトインポートしてから必要要素を取り出す。
// そうしないと Node.js 実行時に named export の解決に失敗するため、本構成では明示的な分割代入を採用する。
import mineflayerPathfinder from 'mineflayer-pathfinder';
import type { Movements as MovementsClass } from 'mineflayer-pathfinder';
import minecraftData from 'minecraft-data';
import { WebSocketServer, WebSocket, RawData } from 'ws';
import {
  detectDockerRuntime,
  parseEnvInt,
  resolveMinecraftHostValue,
} from './runtime/env.js';
import { CUSTOM_SLOT_PATCH } from './runtime/slotPatch.js';

// 型情報を維持するため、実体の分割代入時にモジュール全体の型定義を参照させる。
const { pathfinder, Movements, goals } = mineflayerPathfinder as typeof import('mineflayer-pathfinder');

// ---- Minecraft プロトコル差分パッチ ----
// 詳細な Slot 構造体の上書きロジックは runtime/slotPatch.ts に切り出し、複数バージョンへ一括適用する。

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

// ---- 環境変数・定数設定 ----
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
const WS_PORT = 8765; // Python から接続
const MC_RECONNECT_DELAY_MS = parseEnvInt(process.env.MC_RECONNECT_DELAY_MS, 5000);

// ---- Mineflayer ボット本体のライフサイクル管理 ----
// 接続失敗時にリトライするため、Bot インスタンスは都度生成し直す。
let bot: Bot | null = null;
let reconnectTimer: NodeJS.Timeout | null = null;

/**
 * Minecraft サーバーへの接続を確立し、Mineflayer Bot を初期化する。
 * 失敗した場合でも再試行を継続して開発者の手戻りを防ぐ。
 */
function startBotLifecycle(): void {
  console.log(`[Bot] connecting to ${MC_HOST}:${MC_PORT} ...`);
  const nextBot = createBot({
    host: MC_HOST,
    port: MC_PORT,
    username: BOT_USERNAME,
    auth: AUTH_MODE,
    // 1.21.4+ の ItemStack 追加フィールドに対応するためのカスタムパケット定義。
    customPackets: CUSTOM_SLOT_PATCH,
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
    // 型定義上は第2引数が未定義だが、実実装では mcData を渡すのが推奨されているため、コンストラクタ型を拡張して使用する。
    const MovementsWithData = Movements as unknown as new (bot: Bot, data: ReturnType<typeof minecraftData>) => MovementsClass;
    const defaultMove = new MovementsWithData(targetBot, mcData);
    targetBot.pathfinder.setMovements(defaultMove);
    targetBot.chat('起動しました。（Mineflayer）');
  });

  targetBot.on('chat', (username: string) => {
    if (username === targetBot.username) return;
    // 生のゲーム内チャット（プレイヤー発話）を Python 側に転送したい場合は、
    // ここで WS 送信する設計にしてもよい（今回は Node 側は受信専用に留める）
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
    return null;
  }

  // entity が未定義の間はまだスポーン完了前なので、チャットや移動を実行しない。
  if (!bot.entity) {
    return null;
  }

  return bot;
}

// ---- WebSocket サーバ（Python -> Node） ----
const wss = new WebSocketServer({ port: WS_PORT });
console.log(`[WS] listening on ws://127.0.0.1:${WS_PORT}`);

// ---- WebSocket コマンド処理 ----
// 1 接続につき 1 コマンドというシンプル設計。必要に応じて永続接続へ拡張予定。
wss.on('connection', (ws: WebSocket) => {
  ws.on('message', async (raw) => {
    const response = await handleIncomingMessage(raw);
    ws.send(JSON.stringify(response));
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
    return { ok: false, error: 'Bot is not connected to the Minecraft server yet' };
  }

  activeBot.chat(text);
  return { ok: true };
}

// ---- moveTo コマンド処理 ----
// 指定座標へ pathfinder を使って移動する。
async function handleMoveToCommand(args: Record<string, unknown>): Promise<CommandResponse> {
  const x = Number(args.x);
  const y = Number(args.y);
  const z = Number(args.z);

  if ([x, y, z].some((value) => Number.isNaN(value))) {
    return { ok: false, error: 'Invalid coordinates' };
  }

  const activeBot = getActiveBot();

  if (!activeBot) {
    return { ok: false, error: 'Bot is not connected to the Minecraft server yet' };
  }

  const goal = new goals.GoalBlock(x, y, z);
  try {
    await activeBot.pathfinder.goto(goal);
    return { ok: true };
  } catch (error) {
    console.error('[Pathfinder] failed to move', error);
    return { ok: false, error: 'Pathfinding failed' };
  }
}
