import type { Bot } from 'mineflayer';
import { WebSocket } from 'ws';

import type { CommandPayload, CommandResponse } from '../types.js';

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
    return new Promise((resolve) => {
      const payload = {
        type: 'chat',
        args: { username, message },
      } satisfies CommandPayload;

      const ws = this.createWebSocket(this.config.agentControlWebsocketUrl);
      const timeout = setTimeout(() => {
        this.logger('warn', '[ChatBridge] agent did not respond within 10s');
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
        const text = typeof data === 'string' ? data : data?.toString?.() ?? '';
        this.logger('info', '[ChatBridge] agent response received', { raw: text });
        try {
          const parsed = JSON.parse(text) as CommandResponse;
          if (!parsed.ok) {
            this.logger('warn', '[ChatBridge] agent reported failure', parsed);
          }
        } catch (error) {
          this.logger('warn', '[ChatBridge] failed to parse agent response', { message: error instanceof Error ? error.message : String(error) });
        }
        ws.close();
        cleanup();
      });

      ws.once('close', () => {
        cleanup();
      });

      ws.once('error', (error) => {
        this.logger('error', '[ChatBridge] failed to reach agent', { message: error instanceof Error ? error.message : String(error) });
        cleanup();
      });
    }).catch((error) => {
      this.logger('error', '[ChatBridge] unexpected error', { message: error instanceof Error ? error.message : String(error) });
    });
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
