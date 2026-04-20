// 日本語コメント：WebSocket サーバーの起動と接続管理、コマンド処理キューの共通実装
// 役割：bot.ts から切り離し、接続ごとのトレーシングと安全なキュー処理を集中管理する。
import { randomUUID } from 'node:crypto';
import { WebSocketServer, WebSocket, type RawData } from 'ws';
import { SpanStatusCode, type Tracer } from '@opentelemetry/api';

import { runWithSpan } from './telemetryRuntime.js';
import { adaptLegacyCommandPayload, validateEnvelope } from './transportEnvelope.js';
import type { CommandPayload, CommandResponse } from './types.js';

export interface CommandServerConfig {
  host: string;
  port: number;
}

export interface CommandServerDependencies {
  tracer: Tracer;
  executeCommand(payload: CommandPayload): Promise<CommandResponse>;
}

const SUPPORTED_COMMAND_TYPES: ReadonlySet<CommandPayload['type']> = new Set([
  'chat',
  'moveTo',
  'equipItem',
  'gatherStatus',
  'gatherVptObservation',
  'mineOre',
  'setAgentRole',
  'registerSkill',
  'invokeSkill',
  'skillExplore',
  'playVptActions',
]);

function isCommandPayload(input: unknown): input is CommandPayload {
  if (!input || typeof input !== 'object') {
    return false;
  }
  const candidate = input as Record<string, unknown>;
  return (
    typeof candidate.type === 'string' &&
    SUPPORTED_COMMAND_TYPES.has(candidate.type as CommandPayload['type']) &&
    !!candidate.args &&
    typeof candidate.args === 'object' &&
    (candidate.meta === undefined || (candidate.meta !== null && typeof candidate.meta === 'object'))
  );
}

function parseCommand(raw: RawData): CommandPayload | null {
  try {
    const parsed = JSON.parse(raw.toString()) as unknown;
    const envelope = validateEnvelope(parsed);
    if (envelope) {
      if (envelope.kind !== 'command') {
        console.warn('[WS] unsupported envelope kind', { kind: envelope.kind, name: envelope.name });
        return null;
      }
      return isCommandPayload(envelope.body) ? envelope.body : null;
    }

    const legacy = adaptLegacyCommandPayload(parsed);
    if (legacy) {
      console.warn('[WS] legacy payload detected; wrap into transport envelope', { name: legacy.name });
      return isCommandPayload(legacy.body) ? legacy.body : null;
    }

    return null;
  } catch (error) {
    console.error('[WS] invalid payload', error);
    return null;
  }
}

/**
 * WebSocket 経由で受信したコマンドを処理するサーバーを起動する。
 *
 * @returns 起動済み WebSocketServer インスタンス
 */
export function startCommandServer(
  config: CommandServerConfig,
  deps: CommandServerDependencies,
): WebSocketServer {
  const wss = new WebSocketServer({ host: config.host, port: config.port });
  console.log(`[WS] listening on ws://${config.host}:${config.port}`);

  wss.on('connection', (ws: WebSocket, request) => {
    const clientId = randomUUID();
    const remoteAddress = `${request.socket.remoteAddress ?? 'unknown'}:${request.socket.remotePort ?? 'unknown'}`;

    console.log(`[WS] connection opened id=${clientId} from ${remoteAddress}`);

    ws.on('message', async (raw) => {
      const payload = parseCommand(raw);
      const rawText = raw.toString();

      if (!payload) {
        const invalidResponse: CommandResponse = { ok: false, error: 'Invalid payload format' };
        ws.send(JSON.stringify(invalidResponse));
        return;
      }

      try {
        await runWithSpan(
          deps.tracer,
          'websocket.message',
          {
            'ws.client_id': clientId,
            'ws.remote_address': remoteAddress,
            'ws.payload_length': rawText.length,
          },
          async (span) => {
            console.log(`[WS] (${clientId}) received payload: ${rawText}`);
            const response = await deps.executeCommand(payload);
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

  return wss;
}
