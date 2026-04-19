import type { Bot } from 'mineflayer';
import { WebSocket } from 'ws';

import { buildEnvelope } from '../transportEnvelope.js';
import type { CommandResponse } from '../types.js';

export type ChatBridgeLogger = (level: 'info' | 'warn' | 'error', message: string, context?: Record<string, unknown>) => void;

export type WebSocketFactory = (url: string) => MinimalWebSocket;

export interface MinimalWebSocket {
  once(event: 'open', listener: () => void): this;
  once(event: 'message', listener: (data: unknown) => void): this;
  once(event: 'close', listener: () => void): this;
  once(event: 'error', listener: (error: unknown) => void): this;
  send(data: string): void;
  terminate(): void;
  removeAllListeners(): void;
  close(): void;
}

export interface ChatMessenger {
  sendChat(message: string): boolean;
}

export interface ChatBridgeConfig {
  agentControlWebsocketUrl: string;
  currentPositionKeywords: string[];
  heartbeatIntervalMs?: number;
  reconnectBackoffBaseMs?: number;
  reconnectBackoffMaxMs?: number;
}

function summarizeCommandResponse(response: CommandResponse): Record<string, unknown> {
  return {
    ok: response.ok,
    ...(response.error ? { error: response.error } : {}),
  };
}

/**
 * Mineflayer Bot のチャット入出力を束ね、位置報告や Python エージェントへの転送を担うサービス。
 *
 * チャット送信と WebSocket 生成をインターフェース化し、テストやサンドボックス環境ではモックを注入できる。
 */
export class ChatBridge {
  private readonly config: ChatBridgeConfig;

  private readonly chatMessenger: ChatMessenger;

  private readonly createWebSocket: WebSocketFactory;

  private readonly logger: ChatBridgeLogger;

  private socket: MinimalWebSocket | null = null;

  private connectionPromise: Promise<MinimalWebSocket> | null = null;

  private reconnectAttempts = 0;

  private heartbeatTimer: NodeJS.Timeout | null = null;

  constructor(config: ChatBridgeConfig, dependencies: { chatMessenger: ChatMessenger; createWebSocket?: WebSocketFactory; logger?: ChatBridgeLogger }) {
    this.config = config;
    this.chatMessenger = dependencies.chatMessenger;
    this.createWebSocket = dependencies.createWebSocket ?? ((url) => new WebSocket(url));
    this.logger = dependencies.logger ?? ((level, message, context) => {
      const payload = context ? `${message} ${JSON.stringify(context)}` : message;
      if (level === 'warn') {
        console.warn(payload);
      } else if (level === 'error') {
        console.error(payload);
      } else {
        console.info(payload);
      }
    });
  }

  /**
   * 受信チャットの処理をまとめて行う。必要に応じて現在位置を返答し、その後 Python エージェントへ転送する。
   */
  async handleIncomingChat(targetBot: Bot, username: string, message: string): Promise<void> {
    if (this.shouldReportCurrentPosition(message)) {
      this.reportCurrentPosition(targetBot);
    }

    await this.forwardChatToAgent(username, message);
  }

  private shouldReportCurrentPosition(message: string): boolean {
    const normalized = message.replace(/\s+/g, '').toLowerCase();
    return this.config.currentPositionKeywords.some((keyword) => normalized.includes(keyword));
  }

  private reportCurrentPosition(targetBot: Bot): void {
    if (!targetBot.entity) {
      this.logger('warn', '[Chat] position requested but bot entity is not ready yet.');
      const delivered = this.chatMessenger.sendChat('まだワールドに完全に参加していません。しばらくお待ちください。');
      if (!delivered) {
        this.logger('warn', '[Chat] failed to send position pending notice because bot was unavailable');
      }
      return;
    }

    const { x, y, z } = targetBot.entity.position;
    const formatted = `現在位置は X=${Math.floor(x)} / Y=${Math.floor(y)} / Z=${Math.floor(z)} です。`;
    const delivered = this.chatMessenger.sendChat(formatted);
    if (delivered) {
      this.logger('info', '[Chat] reported current position', { x: Math.floor(x), y: Math.floor(y), z: Math.floor(z) });
    } else {
      this.logger('warn', '[Chat] failed to report current position because chat messenger was unavailable');
    }
  }

  /**
   * Python 側の LangGraph 共有メモリへチャットを転送する。接続が確立できない場合でも Bot の処理をブロックしない。
   */
  private async forwardChatToAgent(username: string, message: string): Promise<void> {
    const payload = buildEnvelope({
      source: 'node-bot',
      kind: 'command',
      name: 'chat',
      body: {
        type: 'chat',
        args: { username, message },
      },
    });

    try {
      const socket = await this.ensureConnected();
      socket.send(JSON.stringify(payload));
    } catch (error) {
      this.logger('error', '[ChatBridge] failed to forward chat to agent', {
        message: error instanceof Error ? error.message : String(error),
      });
    }
  }

  private async ensureConnected(): Promise<MinimalWebSocket> {
    if (this.socket) {
      return this.socket;
    }
    if (this.connectionPromise) {
      return this.connectionPromise;
    }

    this.connectionPromise = new Promise<MinimalWebSocket>((resolve, reject) => {
      const delayMs = this.computeBackoffDelayMs();
      if (delayMs > 0) {
        this.logger('info', '[ChatBridge] reconnect backoff', { delayMs, attempt: this.reconnectAttempts });
      }
      const connect = (): void => {
        const ws = this.createWebSocket(this.config.agentControlWebsocketUrl);
        const timeout = setTimeout(() => {
          ws.terminate();
          reject(new Error('agent websocket connection timeout'));
        }, 10_000);

        ws.once('open', () => {
          clearTimeout(timeout);
          this.socket = ws;
          this.connectionPromise = null;
          this.reconnectAttempts = 0;
          this.startHeartbeat();
          resolve(ws);
        });

        ws.once('message', (data) => {
          const text = typeof data === 'string' ? data : data?.toString?.() ?? '';
          try {
            const parsed = JSON.parse(text) as CommandResponse;
            if (!parsed.ok) {
              this.logger('warn', '[ChatBridge] agent reported failure', summarizeCommandResponse(parsed));
            }
          } catch {
            // heartbeat ack や非JSONメッセージは読み飛ばす
          }
        });

        const markDisconnected = (reason: string, error?: unknown): void => {
          clearTimeout(timeout);
          if (this.socket === ws) {
            this.socket = null;
          }
          this.connectionPromise = null;
          this.stopHeartbeat();
          this.reconnectAttempts += 1;
          this.logger(error ? 'warn' : 'info', `[ChatBridge] ${reason}`, {
            attempt: this.reconnectAttempts,
            message: error instanceof Error ? error.message : undefined,
          });
        };

        ws.once('close', () => markDisconnected('agent websocket closed'));
        ws.once('error', (error) => {
          markDisconnected('agent websocket error', error);
          reject(error instanceof Error ? error : new Error(String(error)));
        });
      };

      if (delayMs > 0) {
        setTimeout(connect, delayMs);
      } else {
        connect();
      }
    });

    return this.connectionPromise;
  }

  private computeBackoffDelayMs(): number {
    if (this.reconnectAttempts <= 0) {
      return 0;
    }
    const base = this.config.reconnectBackoffBaseMs ?? 500;
    const max = this.config.reconnectBackoffMaxMs ?? 5_000;
    const delay = base * (2 ** Math.max(0, this.reconnectAttempts - 1));
    return Math.min(max, delay);
  }

  private startHeartbeat(): void {
    this.stopHeartbeat();
    const intervalMs = this.config.heartbeatIntervalMs ?? 15_000;
    this.heartbeatTimer = setInterval(() => {
      if (!this.socket) {
        return;
      }
      const heartbeat = buildEnvelope({
        source: 'node-bot',
        kind: 'status',
        name: 'heartbeat',
        body: { type: 'heartbeat', args: { ts: new Date().toISOString() } },
      });
      this.socket.send(JSON.stringify(heartbeat));
    }, intervalMs);
  }

  private stopHeartbeat(): void {
    if (!this.heartbeatTimer) {
      return;
    }
    clearInterval(this.heartbeatTimer);
    this.heartbeatTimer = null;
  }
}

/**
 * Bot.chat をラップし、Bot インスタンスの有無を気にせずチャット送信を呼び出せるようにする。
 *
 * Bot 取得関数を受け取ることで再接続に追従し、テストではダミーの Provider を注入して振る舞いを検証できる。
 */
export class BotChatMessenger implements ChatMessenger {
  private readonly getBot: () => Bot | null;

  constructor(botProvider: Bot | (() => Bot | null)) {
    this.getBot = typeof botProvider === 'function' ? (botProvider as () => Bot | null) : () => botProvider;
  }

  sendChat(message: string): boolean {
    const bot = this.getBot();
    if (!bot) {
      console.warn('[ChatMessenger] bot is unavailable; skip sending chat');
      return false;
    }

    bot.chat(message);
    return true;
  }
}
