// 日本語コメント：テレメトリー関連の既定値とバリデーションを集約する
// 役割：OpenTelemetry 出力のエンドポイントやサンプリング率の補正を一箇所で管理する

export interface TelemetryResolution {
  endpoint: string;
  serviceName: string;
  environment: string;
  samplerRatio: number;
  warnings: string[];
}

const DEFAULT_OTEL_ENDPOINT = 'http://localhost:4318';
const DEFAULT_OTEL_SERVICE_NAME = 'mc-node-bot';
const DEFAULT_OTEL_ENVIRONMENT = 'development';

/**
 * OpenTelemetry のエクスポート先やサービス名を正規化する純粋関数。
 * ランタイムごとの差異でエンドポイント指定が欠けていても最低限の可観測性が有効になるよう、既定値を丸める。
 */
export function resolveTelemetryConfig(
  rawEndpoint: string | undefined,
  rawServiceName: string | undefined,
  rawEnvironment: string | undefined,
  rawSamplerRatio: string | undefined,
): TelemetryResolution {
  const warnings: string[] = [];
  const endpoint = (rawEndpoint ?? '').trim().length > 0 ? rawEndpoint!.trim() : DEFAULT_OTEL_ENDPOINT;
  const serviceName = (rawServiceName ?? '').trim().length > 0
    ? rawServiceName!.trim()
    : DEFAULT_OTEL_SERVICE_NAME;
  const environment = (rawEnvironment ?? '').trim().length > 0
    ? rawEnvironment!.trim()
    : DEFAULT_OTEL_ENVIRONMENT;

  const trimmedEndpoint = endpoint.endsWith('/') ? endpoint.slice(0, -1) : endpoint;

  const samplerRatioRaw = (rawSamplerRatio ?? '').trim();
  const samplerRatio = Number.parseFloat(samplerRatioRaw);
  const ratioIsValid = Number.isFinite(samplerRatio) && samplerRatio >= 0 && samplerRatio <= 1;

  if (!ratioIsValid) {
    if (samplerRatioRaw.length > 0) {
      warnings.push(
        `OTEL_TRACES_SAMPLER_RATIO='${rawSamplerRatio}' は 0.0～1.0 の範囲で解釈できないため 1.0 へフォールバックします。`,
      );
    }
  }

  return {
    endpoint: trimmedEndpoint,
    serviceName,
    environment,
    samplerRatio: ratioIsValid ? samplerRatio : 1.0,
    warnings,
  };
}
