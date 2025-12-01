// 日本語コメント：Agent WebSocket 関連の既定値と正規化ロジックを集約する
// 役割：ホスト/ポート/タイムアウトなどの検証を一箇所で行い、URL 組み立ての重複を防ぐ

/**
 * Agent WebSocket 接続設定を解決した結果の詳細。
 * 警告や既定値利用の有無を含めることで、呼び出し側がログやメトリクスに活用しやすくする。
 */
export interface AgentWebSocketResolution {
  url: string;
  host: string;
  port: number;
  connectTimeoutMs: number;
  sendTimeoutMs: number;
  healthcheckIntervalMs: number;
  reconnectDelayMs: number;
  maxRetries: number;
  batchFlushIntervalMs: number;
  batchMaxSize: number;
  queueMaxSize: number;
  warnings: string[];
  usedExplicitUrl: boolean;
  usedDefaultHost: boolean;
  usedDefaultPort: boolean;
}

const DEFAULT_AGENT_WS_PORT = 9000;
const MIN_AGENT_WS_PORT = 1;
const MAX_AGENT_WS_PORT = 65_535;
const DEFAULT_AGENT_WS_HOST_DOCKER = 'python-agent';
const DEFAULT_AGENT_WS_HOST_LOCAL = '127.0.0.1';
const DEFAULT_AGENT_WS_CONNECT_TIMEOUT_MS = 5_000;
const MIN_AGENT_WS_CONNECT_TIMEOUT_MS = 500;
const MAX_AGENT_WS_CONNECT_TIMEOUT_MS = 120_000;
const DEFAULT_AGENT_WS_SEND_TIMEOUT_MS = 5_000;
const MIN_AGENT_WS_SEND_TIMEOUT_MS = 500;
const MAX_AGENT_WS_SEND_TIMEOUT_MS = 120_000;
const DEFAULT_AGENT_WS_HEALTHCHECK_INTERVAL_MS = 15_000;
const MIN_AGENT_WS_HEALTHCHECK_INTERVAL_MS = 1_000;
const MAX_AGENT_WS_HEALTHCHECK_INTERVAL_MS = 300_000;
const DEFAULT_AGENT_WS_RECONNECT_DELAY_MS = 2_000;
const MIN_AGENT_WS_RECONNECT_DELAY_MS = 250;
const MAX_AGENT_WS_RECONNECT_DELAY_MS = 120_000;
const DEFAULT_AGENT_WS_MAX_RETRIES = 3;
const MIN_AGENT_WS_MAX_RETRIES = 0;
const MAX_AGENT_WS_MAX_RETRIES = 10;
const DEFAULT_AGENT_EVENT_BATCH_INTERVAL_MS = 250;
const MIN_AGENT_EVENT_BATCH_INTERVAL_MS = 50;
const MAX_AGENT_EVENT_BATCH_INTERVAL_MS = 10_000;
const DEFAULT_AGENT_EVENT_BATCH_MAX_SIZE = 10;
const MIN_AGENT_EVENT_BATCH_MAX_SIZE = 1;
const MAX_AGENT_EVENT_BATCH_MAX_SIZE = 200;
const DEFAULT_AGENT_EVENT_QUEUE_MAX_SIZE = 200;
const MIN_AGENT_EVENT_QUEUE_MAX_SIZE = 10;
const MAX_AGENT_EVENT_QUEUE_MAX_SIZE = 5_000;

const normalizeNumber = (
  raw: string | undefined,
  fallback: number,
  min: number,
  max: number,
  label: string,
  warnings: string[],
): number => {
  const sanitized = (raw ?? '').trim();
  if (sanitized.length === 0) {
    return fallback;
  }

  const parsed = Number.parseInt(sanitized, 10);
  if (!Number.isFinite(parsed)) {
    warnings.push(`${label}='${raw}' は数値として解釈できないため ${fallback} を利用します。`);
    return fallback;
  }

  if (parsed < min) {
    warnings.push(`${label}=${parsed} は下限 ${min} 未満のため ${min} へ丸めます。`);
    return min;
  }

  if (parsed > max) {
    warnings.push(`${label}=${parsed} は上限 ${max} を超えているため ${max} へ丸めます。`);
    return max;
  }

  return parsed;
};

/**
 * Python エージェント WebSocket への接続先 URL を正規化する純粋関数。
 * - URL 指定時はスキーム補完のみ行いそれ以外は尊重する
 * - ホスト/ポート指定のみの場合は Docker 判定に応じた既定値を利用する
 * - 範囲外のタイムアウトやキューサイズは安全な値へ丸める
 */
export function resolveAgentWebSocketEndpoint(
  rawUrl: string | undefined,
  rawHost: string | undefined,
  rawPort: string | undefined,
  dockerDetected: boolean,
  options: {
    rawConnectTimeoutMs?: string;
    rawSendTimeoutMs?: string;
    rawHealthcheckIntervalMs?: string;
    rawReconnectDelayMs?: string;
    rawMaxRetries?: string;
    rawBatchIntervalMs?: string;
    rawBatchMaxSize?: string;
    rawQueueMaxSize?: string;
  } = {},
): AgentWebSocketResolution {
  const warnings: string[] = [];
  const trimmedUrl = (rawUrl ?? '').trim();
  const trimmedHost = (rawHost ?? '').trim();
  const trimmedPort = (rawPort ?? '').trim();

  const defaultHost = dockerDetected ? DEFAULT_AGENT_WS_HOST_DOCKER : DEFAULT_AGENT_WS_HOST_LOCAL;

  let host = defaultHost;
  let usedDefaultHost = true;

  if (trimmedHost.length > 0) {
    if (trimmedHost === '0.0.0.0') {
      warnings.push(
        `AGENT_WS_HOST='${trimmedHost}' は接続先として利用できないため ${defaultHost} へフォールバックします。`,
      );
    } else {
      host = trimmedHost;
      usedDefaultHost = false;
    }
  }

  let port = DEFAULT_AGENT_WS_PORT;
  let usedDefaultPort = true;

  if (trimmedPort.length > 0) {
    const parsedPort = Number.parseInt(trimmedPort, 10);

    if (!Number.isFinite(parsedPort)) {
      warnings.push(
        `AGENT_WS_PORT='${rawPort}' は数値として解釈できないため ${DEFAULT_AGENT_WS_PORT} を利用します。`,
      );
    } else if (parsedPort < MIN_AGENT_WS_PORT || parsedPort > MAX_AGENT_WS_PORT) {
      warnings.push(
        `AGENT_WS_PORT=${parsedPort} は許容範囲 ${MIN_AGENT_WS_PORT}～${MAX_AGENT_WS_PORT} を外れているため ${DEFAULT_AGENT_WS_PORT} へフォールバックします。`,
      );
    } else {
      port = parsedPort;
      usedDefaultPort = false;
    }
  }

  let url = `ws://${host}:${port}`;
  let usedExplicitUrl = false;

  if (trimmedUrl.length > 0) {
    usedExplicitUrl = true;

    if (!trimmedUrl.includes('://')) {
      warnings.push(`AGENT_WS_URL='${trimmedUrl}' にスキームが含まれていないため ws:// を補完しました。`);
      const hasPortInUrl = trimmedUrl.includes(':');
      url = hasPortInUrl ? `ws://${trimmedUrl}` : `ws://${trimmedUrl}:${port}`;
    } else {
      url = trimmedUrl;
    }
  }

  const connectTimeoutMs = normalizeNumber(
    options.rawConnectTimeoutMs,
    DEFAULT_AGENT_WS_CONNECT_TIMEOUT_MS,
    MIN_AGENT_WS_CONNECT_TIMEOUT_MS,
    MAX_AGENT_WS_CONNECT_TIMEOUT_MS,
    'AGENT_WS_CONNECT_TIMEOUT_MS',
    warnings,
  );
  const sendTimeoutMs = normalizeNumber(
    options.rawSendTimeoutMs,
    DEFAULT_AGENT_WS_SEND_TIMEOUT_MS,
    MIN_AGENT_WS_SEND_TIMEOUT_MS,
    MAX_AGENT_WS_SEND_TIMEOUT_MS,
    'AGENT_WS_SEND_TIMEOUT_MS',
    warnings,
  );
  const healthcheckIntervalMs = normalizeNumber(
    options.rawHealthcheckIntervalMs,
    DEFAULT_AGENT_WS_HEALTHCHECK_INTERVAL_MS,
    MIN_AGENT_WS_HEALTHCHECK_INTERVAL_MS,
    MAX_AGENT_WS_HEALTHCHECK_INTERVAL_MS,
    'AGENT_WS_HEALTHCHECK_INTERVAL_MS',
    warnings,
  );
  const reconnectDelayMs = normalizeNumber(
    options.rawReconnectDelayMs,
    DEFAULT_AGENT_WS_RECONNECT_DELAY_MS,
    MIN_AGENT_WS_RECONNECT_DELAY_MS,
    MAX_AGENT_WS_RECONNECT_DELAY_MS,
    'AGENT_WS_RECONNECT_DELAY_MS',
    warnings,
  );
  const maxRetries = normalizeNumber(
    options.rawMaxRetries,
    DEFAULT_AGENT_WS_MAX_RETRIES,
    MIN_AGENT_WS_MAX_RETRIES,
    MAX_AGENT_WS_MAX_RETRIES,
    'AGENT_WS_MAX_RETRIES',
    warnings,
  );
  const batchFlushIntervalMs = normalizeNumber(
    options.rawBatchIntervalMs,
    DEFAULT_AGENT_EVENT_BATCH_INTERVAL_MS,
    MIN_AGENT_EVENT_BATCH_INTERVAL_MS,
    MAX_AGENT_EVENT_BATCH_INTERVAL_MS,
    'AGENT_EVENT_BATCH_INTERVAL_MS',
    warnings,
  );
  const batchMaxSize = normalizeNumber(
    options.rawBatchMaxSize,
    DEFAULT_AGENT_EVENT_BATCH_MAX_SIZE,
    MIN_AGENT_EVENT_BATCH_MAX_SIZE,
    MAX_AGENT_EVENT_BATCH_MAX_SIZE,
    'AGENT_EVENT_BATCH_MAX_SIZE',
    warnings,
  );
  const queueMaxSize = normalizeNumber(
    options.rawQueueMaxSize,
    DEFAULT_AGENT_EVENT_QUEUE_MAX_SIZE,
    MIN_AGENT_EVENT_QUEUE_MAX_SIZE,
    MAX_AGENT_EVENT_QUEUE_MAX_SIZE,
    'AGENT_EVENT_QUEUE_MAX_SIZE',
    warnings,
  );

  return {
    url,
    host,
    port,
    connectTimeoutMs,
    sendTimeoutMs,
    healthcheckIntervalMs,
    reconnectDelayMs,
    maxRetries,
    batchFlushIntervalMs,
    batchMaxSize,
    queueMaxSize,
    warnings,
    usedExplicitUrl,
    usedDefaultHost,
    usedDefaultPort,
  };
}
