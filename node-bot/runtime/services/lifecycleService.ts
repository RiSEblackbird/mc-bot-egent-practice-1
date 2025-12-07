import { SpanStatusCode, type Counter, type Tracer } from '@opentelemetry/api';
import { createBot, type Bot } from 'mineflayer';
import { randomUUID } from 'node:crypto';

import type { AgentBridge } from '../agentBridge.js';
import {
  type AgentRoleDescriptor,
  type AgentRoleState,
  createInitialAgentRoleState,
  resolveAgentRole,
} from '../roles.js';
import type { MultiAgentEventPayload } from '../types.js';

export interface LifecycleServiceConfig {
  tracer: Tracer;
  reconnectCounter: Counter;
  minecraft: {
    host: string;
    port: number;
    username: string;
    authMode: string;
    version?: string;
    reconnectDelayMs: number;
  };
  customSlotPatch: Record<string, unknown>;
  pathfinderPlugin: typeof import('mineflayer-pathfinder')['pathfinder'];
  agentBridge: AgentBridge;
}

/**
 * Mineflayer Bot のライフサイクルと役割ステートを集中管理するサービス。
 * Bot インスタンス、再接続タイマー、役割のメタ情報をひとまとめにし、
 * 呼び出し側は公開メソッド経由で状態アクセスとイベント送信を行う。
 */
export class BotLifecycleService {
  private readonly tracer: Tracer;

  private readonly reconnectCounter: Counter;

  private readonly minecraft: LifecycleServiceConfig['minecraft'];

  private readonly customSlotPatch: Record<string, unknown>;

  private readonly pathfinderPlugin: typeof import('mineflayer-pathfinder')['pathfinder'];

  private readonly agentBridge: AgentBridge;

  private bot: Bot | null = null;

  private reconnectTimer: NodeJS.Timeout | null = null;

  private readonly agentRoleState: AgentRoleState = createInitialAgentRoleState();

  private registerHandlers: ((bot: Bot) => void) | null = null;

  constructor(config: LifecycleServiceConfig) {
    this.tracer = config.tracer;
    this.reconnectCounter = config.reconnectCounter;
    this.minecraft = config.minecraft;
    this.customSlotPatch = config.customSlotPatch;
    this.pathfinderPlugin = config.pathfinderPlugin;
    this.agentBridge = config.agentBridge;
  }

  /**
   * Bot 接続を開始し、依存するイベントハンドラを登録する。
   * registerBotEventHandlers は初回呼び出し時に保存し、リトライ時も再利用する。
   */
  startBotLifecycle(registerBotEventHandlers?: (bot: Bot) => void): void {
    if (registerBotEventHandlers) {
      this.registerHandlers = registerBotEventHandlers;
    }

    if (!this.registerHandlers) {
      console.error('[LifecycleService] registerBotEventHandlers is not provided.');
      return;
    }

    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }

    this.tracer.startActiveSpan(
      'mineflayer.lifecycle.start',
      {
        attributes: {
          'minecraft.host': this.minecraft.host,
          'minecraft.port': this.minecraft.port,
          'minecraft.protocol': this.minecraft.version ?? 'auto',
          'minecraft.username': this.minecraft.username,
        },
      },
      (span) => {
        try {
          const protocolLabel = this.minecraft.version ?? 'auto-detect (mineflayer default)';
          console.log(
            `[Bot] connecting to ${this.minecraft.host}:${this.minecraft.port} with protocol ${protocolLabel} ...`,
          );
          const nextBot = createBot({
            host: this.minecraft.host,
            port: this.minecraft.port,
            username: this.minecraft.username,
            auth: this.minecraft.authMode,
            customPackets: this.customSlotPatch,
            ...(this.minecraft.version ? { version: this.minecraft.version } : {}),
          });

          this.bot = nextBot;
          nextBot.loadPlugin(this.pathfinderPlugin);
          this.registerHandlers(nextBot);
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
   * Bot が利用可能かつ spawn 完了済みのときに返却する。未準備の場合は null を返す。
   */
  getActiveBot(): Bot | null {
    if (!this.bot) {
      console.warn('[Bot] command requested but bot instance is not ready yet.');
      return null;
    }

    if (!this.bot.entity) {
      console.warn('[Bot] command requested but spawn sequence has not completed.');
      return null;
    }

    return this.bot;
  }

  /**
   * AgentBridge へのアクセサ。イベント配送・ブロードキャストで共有する。
   */
  getAgentBridge(): AgentBridge {
    return this.agentBridge;
  }

  /**
   * 再接続を予約し、同一のタイマーが重複しないようガードする。
   */
  scheduleReconnect(reason: string = 'unknown'): void {
    if (this.reconnectTimer) {
      return;
    }

    this.reconnectCounter.add(1, { reason });

    this.tracer.startActiveSpan(
      'mineflayer.reconnect.schedule',
      { attributes: { 'reconnect.delay_ms': this.minecraft.reconnectDelayMs, 'reconnect.reason': reason } },
      (span) => {
        this.reconnectTimer = setTimeout(() => {
          this.reconnectTimer = null;
          this.startBotLifecycle();
        }, this.minecraft.reconnectDelayMs);
        span.end();
      },
    );
  }

  /**
   * 切断イベント発生時の共通処理。Bot を破棄しつつ再接続予約を行う。
   */
  handleConnectionLoss(reason: string): void {
    this.bot = null;
    this.scheduleReconnect(reason);
  }

  /**
   * 現在アクティブな役割を返す。サービス外部から直接 state を触らずに参照できるようにするためのアクセサ。
   */
  getActiveAgentRole(): AgentRoleDescriptor {
    return this.agentRoleState.activeRole;
  }

  /**
   * 役割切り替えリクエストを適用し、メタ情報も合わせて更新する。
   */
  applyAgentRoleUpdate(roleId: string, source: string, reason?: string): AgentRoleDescriptor {
    const descriptor = resolveAgentRole(roleId);
    this.agentRoleState.activeRole = descriptor;
    this.agentRoleState.lastEventId = randomUUID();
    this.agentRoleState.lastUpdatedAt = Date.now();
    console.log('[Role] switched agent role', {
      roleId: descriptor.id,
      label: descriptor.label,
      source,
      reason: reason ?? 'unspecified',
    });
    return descriptor;
  }

  /**
   * 直近の座標ブロードキャスト情報を取得するためのヘルパー。
   */
  getLastBroadcastPosition(): AgentRoleState['lastBroadcastPosition'] | undefined {
    return this.agentRoleState.lastBroadcastPosition;
  }

  /**
   * 座標ブロードキャストの更新をサービス内に反映する。
   */
  setLastBroadcastPosition(position: AgentRoleState['lastBroadcastPosition']): void {
    this.agentRoleState.lastBroadcastPosition = position;
  }

  /**
   * LangGraph 共有メモリにイベントを伝搬する公開 API。
   */
  async emitAgentEvent(event: MultiAgentEventPayload): Promise<void> {
    await this.agentBridge.emit(event);
  }
}
