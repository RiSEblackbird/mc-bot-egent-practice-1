// 日本語コメント：OpenTelemetry 初期化と計測ヘルパーのランタイム集約
// 役割：Bot 実行時に共通で利用するトレーサー・メトリクスを生成し、
//       span ラッピングユーティリティを提供する。
import {
  metrics,
  trace,
  SpanStatusCode,
  type Counter,
  type Histogram,
  type Span,
  type Tracer,
} from '@opentelemetry/api';
import { OTLPMetricExporter } from '@opentelemetry/exporter-metrics-otlp-http';
import { OTLPTraceExporter } from '@opentelemetry/exporter-trace-otlp-http';
import { resourceFromAttributes } from '@opentelemetry/resources';
import { SemanticResourceAttributes } from '@opentelemetry/semantic-conventions';
import { PeriodicExportingMetricReader } from '@opentelemetry/sdk-metrics';
import { NodeSDK } from '@opentelemetry/sdk-node';
import { ParentBasedSampler, TraceIdRatioBasedSampler } from '@opentelemetry/sdk-trace-base';

import type { TelemetryResolution } from './env.js';

export interface TelemetryContext {
  tracer: Tracer;
  commandDurationMs: Histogram;
  agentBridgeEventCounter: Counter;
  reconnectCounter: Counter;
  directiveCounter: Counter;
  perceptionSnapshotDurationMs: Histogram;
  perceptionErrorCounter: Counter;
  sdk: NodeSDK;
}

function sanitizeEndpoint(endpoint: string): string {
  return endpoint.endsWith('/') ? endpoint.slice(0, -1) : endpoint;
}

/**
 * OpenTelemetry SDK を初期化し、トレースとメトリクスの共通部品を提供する。
 */
export function initializeTelemetry(config: TelemetryResolution): TelemetryContext {
  const baseEndpoint = sanitizeEndpoint(config.endpoint);
  const sdk = new NodeSDK({
    traceExporter: new OTLPTraceExporter({ url: `${baseEndpoint}/v1/traces` }),
    metricReader: new PeriodicExportingMetricReader({
      exporter: new OTLPMetricExporter({ url: `${baseEndpoint}/v1/metrics` }),
    }),
    resource: resourceFromAttributes({
      [SemanticResourceAttributes.SERVICE_NAME]: config.serviceName,
      [SemanticResourceAttributes.DEPLOYMENT_ENVIRONMENT]: config.environment,
      [SemanticResourceAttributes.SERVICE_NAMESPACE]: 'mineflayer-agent',
    }),
    traceSampler: new ParentBasedSampler({ root: new TraceIdRatioBasedSampler(config.samplerRatio) }),
  });

  sdk.start().catch((error) => {
    console.error('[Telemetry] failed to start OpenTelemetry SDK', error);
  });

  const tracer = trace.getTracer('mineflayer-bot');
  const meter = metrics.getMeter('mineflayer-bot');

  const commandDurationMs = meter.createHistogram('mineflayer.command.duration', {
    description: 'Mineflayer コマンド実行時間 (ms)',
    unit: 'ms',
  });
  const agentBridgeEventCounter = meter.createCounter('mineflayer.agent_bridge.events', {
    description: 'AgentBridge 経由で発火したイベント件数',
  });
  const reconnectCounter = meter.createCounter('mineflayer.reconnect.scheduled', {
    description: 'Paper との再接続を予約した回数',
  });
  const directiveCounter = meter.createCounter('mineflayer.directive.received', {
    description: 'Python エージェントから directive メタ付きで受信したコマンド件数',
  });
  const perceptionSnapshotDurationMs = meter.createHistogram('mineflayer.perception.snapshot.duration', {
    description: 'perception スナップショットの生成に要した時間 (ms)',
    unit: 'ms',
  });
  const perceptionErrorCounter = meter.createCounter('mineflayer.perception.snapshot.errors', {
    description: 'perception スナップショット生成で発生したエラー件数',
  });

  const shutdown = async () => {
    try {
      await sdk.shutdown();
      console.log('[Telemetry] OpenTelemetry SDK shutdown completed.');
    } catch (error) {
      console.error('[Telemetry] failed to shutdown OpenTelemetry SDK', error);
    }
  };

  process.once('SIGTERM', shutdown);
  process.once('SIGINT', shutdown);

  return {
    tracer,
    commandDurationMs,
    agentBridgeEventCounter,
    reconnectCounter,
    directiveCounter,
    perceptionSnapshotDurationMs,
    perceptionErrorCounter,
    sdk,
  };
}

/**
 * Mineflayer コマンドやイベントに紐づく属性を安全な形でサマリー化する。
 * フルペイロードを属性へ埋め込むとメトリクス側で扱いづらいため、キーと型の概要に留める。
 */
export function summarizeArgs(args: Record<string, unknown>): string {
  const entries = Object.entries(args ?? {}).map(([key, value]) => `${key}:${typeof value}`);
  return entries.slice(0, 10).join(',');
}

/**
 * span を開始・終了する共通ラッパー。エラー時は status=error を自動で付与する。
 */
export async function runWithSpan<T>(
  tracer: Tracer,
  name: string,
  attributes: Record<string, unknown>,
  handler: (span: Span) => Promise<T> | T,
): Promise<T> {
  return tracer.startActiveSpan(name, async (span) => {
    span.setAttributes(attributes);

    try {
      const result = await handler(span);
      if (typeof result === 'object' && result && 'ok' in (result as Record<string, unknown>)) {
        const okValue = (result as Record<string, unknown>).ok;
        span.setAttribute('result.ok', Boolean(okValue));
      }
      return result;
    } catch (error) {
      span.setStatus({ code: SpanStatusCode.ERROR, message: error instanceof Error ? error.message : String(error) });
      throw error;
    } finally {
      span.end();
    }
  });
}
