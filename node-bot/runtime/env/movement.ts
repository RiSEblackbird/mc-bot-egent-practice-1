// 日本語コメント：移動系設定の正規化ロジックを集約し、環境変数の揺れを吸収する
// 役割：pathfinder の挙動や強制移動リトライ閾値を 1 か所で整形し、bot.ts から定数を排除する

export interface MovementResolution {
  pathfinder: {
    allowParkour: boolean;
    allowSprinting: boolean;
    digCost: {
      enabled: number;
      disabled: number;
    };
  };
  forcedMove: {
    retryWindowMs: number;
    maxRetries: number;
    retryDelayMs: number;
  };
  warnings: string[];
}

const DEFAULT_ALLOW_PARKOUR = true;
const DEFAULT_ALLOW_SPRINTING = true;
const DEFAULT_DIG_COST_ENABLED = 1;
const DEFAULT_DIG_COST_DISABLED = 96;
const DEFAULT_FORCED_MOVE_RETRY_WINDOW_MS = 2_000;
const DEFAULT_FORCED_MOVE_MAX_RETRIES = 2;
const DEFAULT_FORCED_MOVE_RETRY_DELAY_MS = 300;

const MIN_DIG_COST = 1;
const MAX_DIG_COST = 10_000;
const MIN_FORCED_MOVE_WINDOW_MS = 0;
const MAX_FORCED_MOVE_WINDOW_MS = 30_000;
const MIN_FORCED_MOVE_RETRY_DELAY_MS = 0;
const MAX_FORCED_MOVE_RETRY_DELAY_MS = 10_000;

const TRUE_VALUES = new Set(['1', 'true', 'yes', 'on']);
const FALSE_VALUES = new Set(['0', 'false', 'no', 'off']);

function parseBoolean(raw: string | undefined, fallback: boolean, label: string, warnings: string[]): boolean {
  const sanitized = (raw ?? '').trim().toLowerCase();

  if (sanitized.length === 0) {
    return fallback;
  }

  if (TRUE_VALUES.has(sanitized)) {
    return true;
  }

  if (FALSE_VALUES.has(sanitized)) {
    return false;
  }

  warnings.push(`${label}='${raw}' は true/false として解釈できないため ${fallback} を利用します。`);
  return fallback;
}

function parseNumberWithinRange(
  raw: string | undefined,
  fallback: number,
  min: number,
  max: number,
  label: string,
  warnings: string[],
): number {
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
}

export function resolveMovementConfig(
  rawAllowParkour: string | undefined,
  rawAllowSprinting: string | undefined,
  rawDigCostEnabled: string | undefined,
  rawDigCostDisabled: string | undefined,
  rawForcedMoveWindowMs: string | undefined,
  rawForcedMoveMaxRetries: string | undefined,
  rawForcedMoveRetryDelayMs: string | undefined,
): MovementResolution {
  const warnings: string[] = [];

  const allowParkour = parseBoolean(
    rawAllowParkour,
    DEFAULT_ALLOW_PARKOUR,
    'PATHFINDER_ALLOW_PARKOUR',
    warnings,
  );
  const allowSprinting = parseBoolean(
    rawAllowSprinting,
    DEFAULT_ALLOW_SPRINTING,
    'PATHFINDER_ALLOW_SPRINTING',
    warnings,
  );

  const digCostEnabled = parseNumberWithinRange(
    rawDigCostEnabled,
    DEFAULT_DIG_COST_ENABLED,
    MIN_DIG_COST,
    MAX_DIG_COST,
    'PATHFINDER_DIG_COST_ENABLED',
    warnings,
  );
  const digCostDisabled = parseNumberWithinRange(
    rawDigCostDisabled,
    DEFAULT_DIG_COST_DISABLED,
    MIN_DIG_COST,
    MAX_DIG_COST,
    'PATHFINDER_DIG_COST_DISABLED',
    warnings,
  );

  const retryWindowMs = parseNumberWithinRange(
    rawForcedMoveWindowMs,
    DEFAULT_FORCED_MOVE_RETRY_WINDOW_MS,
    MIN_FORCED_MOVE_WINDOW_MS,
    MAX_FORCED_MOVE_WINDOW_MS,
    'FORCED_MOVE_RETRY_WINDOW_MS',
    warnings,
  );
  const maxRetries = parseNumberWithinRange(
    rawForcedMoveMaxRetries,
    DEFAULT_FORCED_MOVE_MAX_RETRIES,
    0,
    10,
    'FORCED_MOVE_MAX_RETRIES',
    warnings,
  );
  const retryDelayMs = parseNumberWithinRange(
    rawForcedMoveRetryDelayMs,
    DEFAULT_FORCED_MOVE_RETRY_DELAY_MS,
    MIN_FORCED_MOVE_RETRY_DELAY_MS,
    MAX_FORCED_MOVE_RETRY_DELAY_MS,
    'FORCED_MOVE_RETRY_DELAY_MS',
    warnings,
  );

  return {
    pathfinder: {
      allowParkour,
      allowSprinting,
      digCost: {
        enabled: digCostEnabled,
        disabled: digCostDisabled,
      },
    },
    forcedMove: {
      retryWindowMs,
      maxRetries,
      retryDelayMs,
    },
    warnings,
  };
}
