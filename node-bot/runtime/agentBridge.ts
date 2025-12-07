import { SpanStatusCode, type Counter, type Tracer } from '@opentelemetry/api';
import { WebSocket } from 'ws';

import { runWithSpan } from './telemetryRuntime.js';
import type { AgentBridgeSessionState, AgentEventEnvelope, MultiAgentEventPayload } from './types.js';

export type StructuredLogLevel = 'info' | 'warn' | 'error';

/**
 * AgentBridge との永続接続とイベント配送を担う専用サービス。
 * WebSocket やバッチ間隔などの設定値を DI で受け取り、Mineflayer 本体からはシンプルなメソッド呼び出しだけで利用できる。
 */
export class AgentBridge {
  private readonly config: AgentBridgeConfig;

  private readonly tracer: Tracer;

  private readonly eventCounter: Counter;

  private readonly logger: AgentBridgeLogger;

  private readonly createWebSocket: (url: string) => WebSocket;

  private state: AgentBridgeSessionState = 'disconnected';

  private socket: WebSocket | null = null;

  private reconnectTimer: NodeJS.Timeout | null = null;

  private batchTimer: NodeJS.Timeout | null = null;

  private healthcheckTimer: NodeJS.Timeout | null = null;

  private flushInFlight: Promise<void> | null = null;

  private lastPongAt = 0;

  private readonly eventQueue: MultiAgentEventPayload[] = [];

  constructor(config: AgentBridgeConfig, dependencies: AgentBridgeDependencies) {
    this.config = config;
    this.tracer = dependencies.tracer;
    this.eventCounter = dependencies.eventCounter;
    this.logger = dependencies.logger ?? createDefaultAgentBridgeLogger();
    this.createWebSocket = dependencies.createWebSocket ?? ((url: string) => new WebSocket(url));
  }

  /**
   * 外部から接続を明示的に確保したい場合に利用する。既に接続済みなら何もしない。
   */
  ensureSession(reason: string): void {
    if (this.state !== 'disconnected') {
      return;
    }

    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }

    this.state = 'connecting';
    this.logger('info', 'agent-bridge.connect.start', {
      url: this.config.url,
      reason,
      connectTimeoutMs: this.config.connectTimeoutMs,
      queueSize: this.getQueueSize(),
      state: this.state,
    });
    const session = this.createWebSocket(this.config.url);
    this.socket = session;

    const connectTimeout = setTimeout(() => {
      this.logger('warn', 'agent-bridge.connect.timeout', {
        timeoutMs: this.config.connectTimeoutMs,
        url: this.config.url,
        queueSize: this.getQueueSize(),
      });
      session.terminate();
    }, this.config.connectTimeoutMs);

    session.once('open', () => {
      clearTimeout(connectTimeout);
      this.state = 'connected';
      this.lastPongAt = Date.now();
      this.logger('info', 'agent-bridge.connected', { url: this.config.url, reason });
      this.startHealthcheck(session);
    });

    session.on('pong', () => {
      this.lastPongAt = Date.now();
    });

    session.once('close', (code, rawReason) => {
      clearTimeout(connectTimeout);
      this.cleanupSession(session);
      this.logger('warn', 'agent-bridge.closed', {
        code,
        reason: rawReason?.toString() ?? 'unknown',
        url: this.config.url,
        queueSize: this.getQueueSize(),
        lastPongAt: this.lastPongAt,
      });
      this.scheduleReconnect('closed');
    });

    session.once('error', (error) => {
      clearTimeout(connectTimeout);
      this.logger('error', 'agent-bridge.error', {
        message: error instanceof Error ? error.message : String(error),
        url: this.config.url,
        state: this.state,
        queueSize: this.getQueueSize(),
      });
      this.cleanupSession(session);
      this.scheduleReconnect('error');
    });
  }

  /**
   * LangGraph 側へイベントを送信するための公開メソッド。Span 計測とメトリクスを含め、
   * 呼び出し側はイベント構築に専念できるようカプセル化する。
   */
  async emit(event: MultiAgentEventPayload): Promise<void> {
    const summaryAttributes = {
      'agent_event.channel': event.channel,
      'agent_event.type': event.event,
      'agent_event.agent_id': event.agentId,
    };

    return runWithSpan(this.tracer, 'agent.bridge.emit', summaryAttributes, async (span) => {
      this.eventCounter.add(1, { channel: event.channel, event: event.event });
      const startedAt = Date.now();

      try {
        this.enqueue(event);
        this.logger('info', 'agent-bridge.enqueued', {
          channel: event.channel,
          type: event.event,
          queueSize: this.getQueueSize(),
        });
        span.setAttribute('agent_event.queue_size', this.getQueueSize());
      } catch (error) {
        span.setStatus({ code: SpanStatusCode.ERROR, message: error instanceof Error ? error.message : String(error) });
        this.logger('error', 'agent-bridge.enqueue.failed', {
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
   * イベントキューの現在サイズを返す。監視用の補助 API。
   */
  getQueueSize(): number {
    return this.eventQueue.length;
  }

  private enqueue(event: MultiAgentEventPayload): void {
    if (this.eventQueue.length >= this.config.queueMaxSize) {
      this.eventQueue.shift();
      this.logger('warn', 'agent-bridge.queue.trimmed', {
        limit: this.config.queueMaxSize,
        channel: event.channel,
        type: event.event,
      });
    }

    this.eventQueue.push(event);
    this.scheduleEventFlush();
    this.ensureSession('enqueue');
  }

  private scheduleEventFlush(): void {
    if (this.batchTimer) {
      return;
    }

    this.batchTimer = setTimeout(() => {
      this.batchTimer = null;
      void this.flushQueue();
    }, this.config.batchFlushIntervalMs);
  }

  private async flushQueue(): Promise<void> {
    if (this.flushInFlight) {
      return this.flushInFlight;
    }

    if (this.eventQueue.length === 0) {
      return;
    }

    if (!this.socket || this.state !== 'connected' || this.socket.readyState !== WebSocket.OPEN) {
      this.ensureSession('flush-wait');
      this.scheduleEventFlush();
      return;
    }

    const batch = this.eventQueue.splice(0, this.config.batchMaxSize);
    this.flushInFlight = this.sendBatch(batch)
      .catch((error) => {
        this.logger('error', 'agent-bridge.batch.failed', {
          message: error instanceof Error ? error.message : String(error),
          batchSize: batch.length,
        });
        const availableSlots = Math.max(0, this.config.queueMaxSize - this.eventQueue.length);
        if (availableSlots > 0) {
          this.eventQueue.unshift(...batch.slice(0, availableSlots));
        }
        this.scheduleEventFlush();
      })
      .finally(() => {
        this.flushInFlight = null;
        if (this.eventQueue.length > 0) {
          this.scheduleEventFlush();
        }
      });

    return this.flushInFlight;
  }

  private async sendBatch(batch: MultiAgentEventPayload[]): Promise<void> {
    const envelope: AgentEventEnvelope = { type: 'agentEvent', args: { events: batch } };
    const startedAt = Date.now();
    const maxAttempts = Math.max(1, this.config.maxRetries + 1);

    for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
      try {
        await this.sendThroughActiveSession(envelope);
        this.logger('info', 'agent-bridge.batch.sent', {
          batchSize: batch.length,
          attempt,
          durationMs: Date.now() - startedAt,
        });
        return;
      } catch (error) {
        const isLastAttempt = attempt === maxAttempts;
        this.logger(isLastAttempt ? 'error' : 'warn', 'agent-bridge.batch.retry', {
          attempt,
          maxAttempts,
          batchSize: batch.length,
          message: error instanceof Error ? error.message : String(error),
        });
        if (isLastAttempt) {
          throw error;
        }
        this.scheduleReconnect('send-retry');
        await wait(this.config.reconnectDelayMs);
      }
    }
  }

  private sendThroughActiveSession(payload: AgentEventEnvelope): Promise<void> {
    const session = this.socket;
    if (!session || this.state !== 'connected' || session.readyState !== WebSocket.OPEN) {
      return Promise.reject(new Error('agent bridge is not connected'));
    }

    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        session.terminate();
        reject(new Error('agent bridge send timeout'));
      }, this.config.sendTimeoutMs);

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

  private scheduleReconnect(reason: string): void {
    if (this.reconnectTimer) {
      return;
    }

    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.ensureSession(reason);
    }, this.config.reconnectDelayMs);
  }

  private cleanupSession(session?: WebSocket): void {
    if (this.healthcheckTimer) {
      clearInterval(this.healthcheckTimer);
      this.healthcheckTimer = null;
    }

    if (!session || session === this.socket) {
      this.socket?.removeAllListeners();
      this.socket = null;
      this.state = 'disconnected';
    }
  }

  private startHealthcheck(session: WebSocket): void {
    if (this.healthcheckTimer) {
      clearInterval(this.healthcheckTimer);
    }

    this.healthcheckTimer = setInterval(() => {
      if (session !== this.socket || session.readyState !== WebSocket.OPEN) {
        return;
      }

      const now = Date.now();
      if (now - this.lastPongAt > this.config.healthcheckIntervalMs * 2) {
        this.logger('warn', 'agent-bridge.healthcheck.timeout', {
          sinceLastPongMs: now - this.lastPongAt,
          intervalMs: this.config.healthcheckIntervalMs,
        });
        session.terminate();
        return;
      }

      try {
        session.ping();
      } catch (error) {
        this.logger('error', 'agent-bridge.healthcheck.ping_failed', {
          message: error instanceof Error ? error.message : String(error),
        });
        session.terminate();
      }
    }, this.config.healthcheckIntervalMs);
  }
}

export interface AgentBridgeConfig {
  url: string;
  connectTimeoutMs: number;
  sendTimeoutMs: number;
  healthcheckIntervalMs: number;
  reconnectDelayMs: number;
  maxRetries: number;
  batchFlushIntervalMs: number;
  batchMaxSize: number;
  queueMaxSize: number;
}

export interface AgentBridgeDependencies {
  tracer: Tracer;
  eventCounter: Counter;
  logger?: AgentBridgeLogger;
  createWebSocket?: (url: string) => WebSocket;
}

export type AgentBridgeLogger = (
  level: StructuredLogLevel,
  event: string,
  context: Record<string, unknown>,
) => void;

function wait(durationMs: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, durationMs));
}

function createDefaultAgentBridgeLogger(): AgentBridgeLogger {
  return (level: StructuredLogLevel, event: string, context: Record<string, unknown>) => {
    console.log(
      JSON.stringify({
        level,
        event,
        timestamp: new Date().toISOString(),
        context,
      }),
    );
  };
}
