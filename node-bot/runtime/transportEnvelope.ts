import { randomUUID } from 'node:crypto';

import type { CommandPayload } from './types.js';

export const CURRENT_TRANSPORT_VERSION = 'v1';

export type EnvelopeKind = 'command' | 'event' | 'status' | 'error';

export interface TransportEnvelope {
  version: string;
  trace_id: string;
  run_id: string;
  message_id: string;
  timestamp: string;
  source: string;
  kind: EnvelopeKind;
  name: string;
  body: Record<string, unknown>;
  auth?: Record<string, unknown> | null;
}

export function buildEnvelope(params: {
  source: string;
  kind: EnvelopeKind;
  name: string;
  body: Record<string, unknown>;
  traceId?: string;
  runId?: string;
  messageId?: string;
}): TransportEnvelope {
  return {
    version: CURRENT_TRANSPORT_VERSION,
    trace_id: params.traceId ?? randomUUID(),
    run_id: params.runId ?? randomUUID(),
    message_id: params.messageId ?? randomUUID(),
    timestamp: new Date().toISOString(),
    source: params.source,
    kind: params.kind,
    name: params.name,
    body: params.body,
  };
}

export function validateEnvelope(input: unknown): TransportEnvelope | null {
  if (!input || typeof input !== 'object') {
    return null;
  }

  const maybe = input as Record<string, unknown>;
  const kind = maybe.kind;
  if (
    maybe.version !== CURRENT_TRANSPORT_VERSION ||
    typeof maybe.trace_id !== 'string' ||
    typeof maybe.run_id !== 'string' ||
    typeof maybe.message_id !== 'string' ||
    typeof maybe.timestamp !== 'string' ||
    typeof maybe.source !== 'string' ||
    (kind !== 'command' && kind !== 'event' && kind !== 'status' && kind !== 'error') ||
    typeof maybe.name !== 'string' ||
    !maybe.body ||
    typeof maybe.body !== 'object'
  ) {
    return null;
  }

  return maybe as unknown as TransportEnvelope;
}

export function adaptLegacyCommandPayload(input: unknown): TransportEnvelope | null {
  if (!input || typeof input !== 'object') {
    return null;
  }

  const payload = input as Partial<CommandPayload> & Record<string, unknown>;
  if (typeof payload.type !== 'string' || !payload.args || typeof payload.args !== 'object') {
    return null;
  }

  return buildEnvelope({
    source: 'legacy-python-agent',
    kind: 'command',
    name: payload.type,
    body: {
      type: payload.type,
      args: payload.args as Record<string, unknown>,
      ...(payload.meta && typeof payload.meta === 'object' ? { meta: payload.meta as Record<string, unknown> } : {}),
    },
  });
}
