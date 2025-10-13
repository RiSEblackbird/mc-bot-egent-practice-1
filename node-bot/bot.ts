// 日本語コメント：Mineflayer ボット（WSコマンド受信）
// 役割：Python からの JSON コマンドを実ゲーム操作へ変換する
import { createBot, Bot } from 'mineflayer';
import { pathfinder, Movements, goals } from 'mineflayer-pathfinder';
import minecraftData from 'minecraft-data';
import { WebSocketServer, WebSocket, RawData } from 'ws';

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
const MC_HOST = process.env.MC_HOST ?? '127.0.0.1';
const MC_PORT = Number.parseInt(process.env.MC_PORT ?? '25565', 10);
const BOT_USERNAME = process.env.BOT_USERNAME ?? 'HelperBot';
const AUTH_MODE = (process.env.AUTH_MODE ?? 'offline') as 'offline' | 'microsoft';
const WS_PORT = 8765; // Python から接続

// ---- Mineflayer ボット本体の初期化 ----
// Bot インスタンスを生成し、後続で Pathfinding 等の機能を付与する。
const bot: Bot = createBot({
  host: MC_HOST,
  port: MC_PORT,
  username: BOT_USERNAME,
  auth: AUTH_MODE,
});

bot.loadPlugin(pathfinder);

// ---- スポーン時の初期化 ----
// スポーンしたら移動ロジックの初期化と起動メッセージ送信を行う。
bot.once('spawn', () => {
  const mcData = minecraftData(bot.version);
  // 型定義上は第2引数が未定義だが、実装的には mcData を渡すのが推奨されているためコンストラクタを拡張キャストする。
  const MovementsWithData = Movements as unknown as new (bot: Bot, data: ReturnType<typeof minecraftData>) => Movements;
  const defaultMove = new MovementsWithData(bot, mcData);
  bot.pathfinder.setMovements(defaultMove);
  bot.chat('起動しました。（Mineflayer）');
});

// ---- ゲーム内チャット受信 ----
// 現状は Python 側への転送を行っていないため、プレイヤーの発話は無視する。
bot.on('chat', (username: string) => {
  if (username === bot.username) return;
  // 生のゲーム内チャット（プレイヤー発話）を Python 側に転送したい場合は、
  // ここで WS 送信する設計にしてもよい（今回は Node 側は受信専用に留める）
});

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
  bot.chat(text);
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

  const goal = new goals.GoalBlock(x, y, z);
  try {
    await bot.pathfinder.goto(goal);
    return { ok: true };
  } catch (error) {
    console.error('[Pathfinder] failed to move', error);
    return { ok: false, error: 'Pathfinding failed' };
  }
}
