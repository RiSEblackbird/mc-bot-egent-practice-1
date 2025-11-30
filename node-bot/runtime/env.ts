// 日本語コメント：環境変数や実行環境に依存する処理を集約したユーティリティ
// 役割：bot.ts から切り離し、単体テストでも検証できるようにする
import { existsSync as defaultExistsSync, readFileSync as defaultReadFileSync } from 'node:fs';

// 移動許容値の上下限は複数箇所で利用するため定数化して明示する。
const DEFAULT_MOVE_GOAL_TOLERANCE = 3;
const MIN_MOVE_GOAL_TOLERANCE = 1;
const MAX_MOVE_GOAL_TOLERANCE = 30;

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

const DEFAULT_OTEL_ENDPOINT = 'http://localhost:4318';
const DEFAULT_OTEL_SERVICE_NAME = 'mc-node-bot';
const DEFAULT_OTEL_ENVIRONMENT = 'development';

const DEFAULT_CONTROL_MODE = 'command';
const SUPPORTED_CONTROL_MODES = new Set(['command', 'vpt']);
const DEFAULT_VPT_TICK_INTERVAL_MS = 50;
const MIN_VPT_TICK_INTERVAL_MS = 10;
const MAX_VPT_TICK_INTERVAL_MS = 250;
const DEFAULT_VPT_MAX_SEQUENCE_LENGTH = 240;
const MIN_VPT_MAX_SEQUENCE_LENGTH = 1;
const MAX_VPT_MAX_SEQUENCE_LENGTH = 2000;

const DEFAULT_PERCEPTION_ENTITY_RADIUS = 12;
const MIN_PERCEPTION_ENTITY_RADIUS = 1;
const MAX_PERCEPTION_ENTITY_RADIUS = 64;
const DEFAULT_PERCEPTION_BLOCK_RADIUS = 4;
const MIN_PERCEPTION_BLOCK_RADIUS = 1;
const MAX_PERCEPTION_BLOCK_RADIUS = 16;
const DEFAULT_PERCEPTION_BLOCK_HEIGHT = 2;
const MIN_PERCEPTION_BLOCK_HEIGHT = 1;
const MAX_PERCEPTION_BLOCK_HEIGHT = 12;
const DEFAULT_PERCEPTION_BROADCAST_INTERVAL_MS = 1_500;
const MIN_PERCEPTION_BROADCAST_INTERVAL_MS = 250;
const MAX_PERCEPTION_BROADCAST_INTERVAL_MS = 30_000;

/**
 * Docker 実行環境を判定する際に利用する依存関係のインターフェース。
 * 単体テストでは疑似的なファイルシステムを差し替え、条件分岐を細かく検証する。
 */
export interface DockerDetectionDeps {
  existsSync(path: string): boolean;
  readFileSync(path: string): string;
}

const defaultDockerDetectionDeps: DockerDetectionDeps = {
  existsSync: defaultExistsSync,
  readFileSync: (path: string) => defaultReadFileSync(path, 'utf8'),
};

/**
 * Docker コンテナ内で実行されているかを判定する。
 * `/.dockerenv` の存在と `cgroup` の内容を調べることで、幅広い環境に対応する。
 */
export function detectDockerRuntime(deps: DockerDetectionDeps = defaultDockerDetectionDeps): boolean {
  if (deps.existsSync('/.dockerenv')) {
    return true;
  }

  try {
    const cgroupInfo = deps.readFileSync('/proc/1/cgroup');
    return cgroupInfo.includes('docker') || cgroupInfo.includes('kubepods');
  } catch {
    return false;
  }
}

/**
 * `MC_HOST` の決定過程で必要な情報をまとめた構造体。
 * どのようなフォールバックが起きたかを把握するための補助情報も含める。
 */
export interface HostResolutionResult {
  host: string;
  originalValue: string;
  usedDockerFallback: boolean;
  usedDefaultHost: boolean;
}

/**
 * moveTo コマンドの GoalNear 許容範囲に関する解析結果。
 *
 * warnings には入力値の補正が必要だった理由を蓄積し、呼び出し元で
 * ログ出力できるようにする。テストからも観測しやすくなるため、
 * 数値以外の入力や極端な値を検出した際にメッセージを残す設計にした。
 */
export interface MoveGoalToleranceResolution {
  tolerance: number;
  warnings: string[];
}

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

export interface ControlModeResolution {
  mode: 'command' | 'vpt';
  warnings: string[];
}

export interface VptPlaybackResolution {
  tickIntervalMs: number;
  maxSequenceLength: number;
  warnings: string[];
}

export interface PerceptionResolution {
  entityRadius: number;
  blockRadius: number;
  blockHeight: number;
  broadcastIntervalMs: number;
  warnings: string[];
}

export interface TelemetryResolution {
  endpoint: string;
  serviceName: string;
  environment: string;
  samplerRatio: number;
  warnings: string[];
}

/**
 * Minecraft 接続先ホスト名を決定するロジックの中核。
 * Docker 環境で `localhost` / `127.0.0.1` が指定された際は `host.docker.internal` へ安全に差し替える。
 */
export function resolveMinecraftHostValue(
  envHostRaw: string | undefined,
  dockerDetected: boolean,
): HostResolutionResult {
  const trimmed = (envHostRaw ?? '').trim();

  if (trimmed.length > 0) {
    const needsDockerAlias =
      dockerDetected && (trimmed === 'localhost' || trimmed === '127.0.0.1');

    return {
      host: needsDockerAlias ? 'host.docker.internal' : trimmed,
      originalValue: trimmed,
      usedDockerFallback: needsDockerAlias,
      usedDefaultHost: false,
    };
  }

  const fallbackHost = dockerDetected ? 'host.docker.internal' : '127.0.0.1';

  return {
    host: fallbackHost,
    originalValue: '',
    usedDockerFallback: dockerDetected,
    usedDefaultHost: true,
  };
}

/**
 * MOVE_GOAL_TOLERANCE の入力を安全に正規化する。
 *
 * - 未設定時は README で説明している既定値 (3 ブロック) を利用
 * - 数値化できない入力は既定値にフォールバックし、警告を追加
 * - 1 未満や 30 を超える値は上下限へ丸め、実行時に予期せぬ巨大値で
 *   pathfinder が暴走しないよう防御する
 */
export function resolveMoveGoalTolerance(rawValue: string | undefined): MoveGoalToleranceResolution {
  const warnings: string[] = [];
  const sanitized = (rawValue ?? '').trim();

  if (sanitized.length === 0) {
    return { tolerance: DEFAULT_MOVE_GOAL_TOLERANCE, warnings };
  }

  const parsed = Number.parseInt(sanitized, 10);
  if (!Number.isFinite(parsed)) {
    warnings.push(
      `MOVE_GOAL_TOLERANCE='${rawValue}' は数値として解釈できないため ${DEFAULT_MOVE_GOAL_TOLERANCE} へフォールバックします。`,
    );
    return { tolerance: DEFAULT_MOVE_GOAL_TOLERANCE, warnings };
  }

  if (parsed < MIN_MOVE_GOAL_TOLERANCE) {
    warnings.push(
      `MOVE_GOAL_TOLERANCE=${parsed} は下限 ${MIN_MOVE_GOAL_TOLERANCE} 未満のため ${MIN_MOVE_GOAL_TOLERANCE} へ丸めます。`,
    );
    return { tolerance: MIN_MOVE_GOAL_TOLERANCE, warnings };
  }

  if (parsed > MAX_MOVE_GOAL_TOLERANCE) {
    warnings.push(
      `MOVE_GOAL_TOLERANCE=${parsed} は上限 ${MAX_MOVE_GOAL_TOLERANCE} を超えているため ${MAX_MOVE_GOAL_TOLERANCE} へ丸めます。`,
    );
    return { tolerance: MAX_MOVE_GOAL_TOLERANCE, warnings };
  }

  return { tolerance: parsed, warnings };
}

/**
 * Python エージェント WebSocket への接続先 URL を正規化する。
 *
 * - URL 指定時はそのまま尊重し、スキームが抜けていれば `ws://` を補完する
 * - ホスト/ポート指定のみの場合は、Docker 環境に応じた既定値へフォールバックする
 * - 0.0.0.0 や範囲外のポートなど、接続に利用できない値は警告を付けつつ丸める
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

  const normalizeNumber = (
    raw: string | undefined,
    fallback: number,
    min: number,
    max: number,
    label: string,
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
  );
  const sendTimeoutMs = normalizeNumber(
    options.rawSendTimeoutMs,
    DEFAULT_AGENT_WS_SEND_TIMEOUT_MS,
    MIN_AGENT_WS_SEND_TIMEOUT_MS,
    MAX_AGENT_WS_SEND_TIMEOUT_MS,
    'AGENT_WS_SEND_TIMEOUT_MS',
  );
  const healthcheckIntervalMs = normalizeNumber(
    options.rawHealthcheckIntervalMs,
    DEFAULT_AGENT_WS_HEALTHCHECK_INTERVAL_MS,
    MIN_AGENT_WS_HEALTHCHECK_INTERVAL_MS,
    MAX_AGENT_WS_HEALTHCHECK_INTERVAL_MS,
    'AGENT_WS_HEALTHCHECK_INTERVAL_MS',
  );
  const reconnectDelayMs = normalizeNumber(
    options.rawReconnectDelayMs,
    DEFAULT_AGENT_WS_RECONNECT_DELAY_MS,
    MIN_AGENT_WS_RECONNECT_DELAY_MS,
    MAX_AGENT_WS_RECONNECT_DELAY_MS,
    'AGENT_WS_RECONNECT_DELAY_MS',
  );
  const maxRetries = normalizeNumber(
    options.rawMaxRetries,
    DEFAULT_AGENT_WS_MAX_RETRIES,
    MIN_AGENT_WS_MAX_RETRIES,
    MAX_AGENT_WS_MAX_RETRIES,
    'AGENT_WS_MAX_RETRIES',
  );
  const batchFlushIntervalMs = normalizeNumber(
    options.rawBatchIntervalMs,
    DEFAULT_AGENT_EVENT_BATCH_INTERVAL_MS,
    MIN_AGENT_EVENT_BATCH_INTERVAL_MS,
    MAX_AGENT_EVENT_BATCH_INTERVAL_MS,
    'AGENT_EVENT_BATCH_INTERVAL_MS',
  );
  const batchMaxSize = normalizeNumber(
    options.rawBatchMaxSize,
    DEFAULT_AGENT_EVENT_BATCH_MAX_SIZE,
    MIN_AGENT_EVENT_BATCH_MAX_SIZE,
    MAX_AGENT_EVENT_BATCH_MAX_SIZE,
    'AGENT_EVENT_BATCH_MAX_SIZE',
  );
  const queueMaxSize = normalizeNumber(
    options.rawQueueMaxSize,
    DEFAULT_AGENT_EVENT_QUEUE_MAX_SIZE,
    MIN_AGENT_EVENT_QUEUE_MAX_SIZE,
    MAX_AGENT_EVENT_QUEUE_MAX_SIZE,
    'AGENT_EVENT_QUEUE_MAX_SIZE',
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

/**
 * OpenTelemetry のエクスポート先やサービス名を正規化する。
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

/**
 * 数値型環境変数を安全に読み込むユーティリティ。
 * 数値化に失敗した場合はフォールバック値を返し、NaN に起因するバグを防ぐ。
 */
export function parseEnvInt(rawValue: string | undefined, fallback: number): number {
  const parsed = Number.parseInt(rawValue ?? '', 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

export function resolveControlMode(rawMode: string | undefined): ControlModeResolution {
  const warnings: string[] = [];
  const sanitized = (rawMode ?? '').trim().toLowerCase();

  if (sanitized.length === 0) {
    return { mode: DEFAULT_CONTROL_MODE, warnings };
  }

  if (SUPPORTED_CONTROL_MODES.has(sanitized)) {
    return { mode: sanitized as 'command' | 'vpt', warnings };
  }

  warnings.push(
    `CONTROL_MODE='${rawMode}' はサポート外のため ${DEFAULT_CONTROL_MODE} へフォールバックします。`,
  );
  return { mode: DEFAULT_CONTROL_MODE, warnings };
}

export function resolveVptPlaybackConfig(
  rawTickInterval: string | undefined,
  rawMaxSequence: string | undefined,
): VptPlaybackResolution {
  const warnings: string[] = [];

  let tickIntervalMs = DEFAULT_VPT_TICK_INTERVAL_MS;
  const sanitizedTick = (rawTickInterval ?? '').trim();
  if (sanitizedTick.length > 0) {
    const parsed = Number.parseInt(sanitizedTick, 10);
    if (!Number.isFinite(parsed)) {
      warnings.push(
        `VPT_TICK_INTERVAL_MS='${rawTickInterval}' は数値として解釈できないため ${DEFAULT_VPT_TICK_INTERVAL_MS} を利用します。`,
      );
    } else if (parsed < MIN_VPT_TICK_INTERVAL_MS || parsed > MAX_VPT_TICK_INTERVAL_MS) {
      const clamped = Math.min(Math.max(parsed, MIN_VPT_TICK_INTERVAL_MS), MAX_VPT_TICK_INTERVAL_MS);
      warnings.push(
        `VPT_TICK_INTERVAL_MS=${parsed} は許容範囲 ${MIN_VPT_TICK_INTERVAL_MS}～${MAX_VPT_TICK_INTERVAL_MS} を外れているため ${clamped} へ丸めます。`,
      );
      tickIntervalMs = clamped;
    } else {
      tickIntervalMs = parsed;
    }
  }

  let maxSequenceLength = DEFAULT_VPT_MAX_SEQUENCE_LENGTH;
  const sanitizedSeq = (rawMaxSequence ?? '').trim();
  if (sanitizedSeq.length > 0) {
    const parsed = Number.parseInt(sanitizedSeq, 10);
    if (!Number.isFinite(parsed)) {
      warnings.push(
        `VPT_MAX_SEQUENCE_LENGTH='${rawMaxSequence}' は数値として解釈できないため ${DEFAULT_VPT_MAX_SEQUENCE_LENGTH} を利用します。`,
      );
    } else if (parsed < MIN_VPT_MAX_SEQUENCE_LENGTH || parsed > MAX_VPT_MAX_SEQUENCE_LENGTH) {
      const clamped = Math.min(
        Math.max(parsed, MIN_VPT_MAX_SEQUENCE_LENGTH),
        MAX_VPT_MAX_SEQUENCE_LENGTH,
      );
      warnings.push(
        `VPT_MAX_SEQUENCE_LENGTH=${parsed} は許容範囲 ${MIN_VPT_MAX_SEQUENCE_LENGTH}～${MAX_VPT_MAX_SEQUENCE_LENGTH} を外れているため ${clamped} へ丸めます。`,
      );
      maxSequenceLength = clamped;
    } else {
      maxSequenceLength = parsed;
    }
  }

  return { tickIntervalMs, maxSequenceLength, warnings };
}

export function resolvePerceptionConfig(
  rawEntityRadius: string | undefined,
  rawBlockRadius: string | undefined,
  rawBlockHeight: string | undefined,
  rawBroadcastInterval: string | undefined,
): PerceptionResolution {
  const warnings: string[] = [];

  const normalize = (
    raw: string | undefined,
    fallback: number,
    min: number,
    max: number,
    label: string,
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

  const entityRadius = normalize(
    rawEntityRadius,
    DEFAULT_PERCEPTION_ENTITY_RADIUS,
    MIN_PERCEPTION_ENTITY_RADIUS,
    MAX_PERCEPTION_ENTITY_RADIUS,
    'PERCEPTION_ENTITY_RADIUS',
  );
  const blockRadius = normalize(
    rawBlockRadius,
    DEFAULT_PERCEPTION_BLOCK_RADIUS,
    MIN_PERCEPTION_BLOCK_RADIUS,
    MAX_PERCEPTION_BLOCK_RADIUS,
    'PERCEPTION_BLOCK_RADIUS',
  );
  const blockHeight = normalize(
    rawBlockHeight,
    DEFAULT_PERCEPTION_BLOCK_HEIGHT,
    MIN_PERCEPTION_BLOCK_HEIGHT,
    MAX_PERCEPTION_BLOCK_HEIGHT,
    'PERCEPTION_BLOCK_HEIGHT',
  );
  const broadcastIntervalMs = normalize(
    rawBroadcastInterval,
    DEFAULT_PERCEPTION_BROADCAST_INTERVAL_MS,
    MIN_PERCEPTION_BROADCAST_INTERVAL_MS,
    MAX_PERCEPTION_BROADCAST_INTERVAL_MS,
    'PERCEPTION_BROADCAST_INTERVAL_MS',
  );

  return {
    entityRadius,
    blockRadius,
    blockHeight,
    broadcastIntervalMs,
    warnings,
  };
}
