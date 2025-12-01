// 日本語コメント：MOVE/VPT/制御モードの正規化を集約し、テストしやすい純粋関数として提供する
// 役割：移動許容値や VPT 再生設定の上下限チェックを一箇所で実行し、bot.ts から切り離す

export interface MoveGoalToleranceResolution {
  tolerance: number;
  warnings: string[];
}

export interface ControlModeResolution {
  mode: 'command' | 'vpt' | 'hybrid';
  warnings: string[];
}

export interface VptPlaybackResolution {
  tickIntervalMs: number;
  maxSequenceLength: number;
  warnings: string[];
}

const DEFAULT_MOVE_GOAL_TOLERANCE = 3;
const MIN_MOVE_GOAL_TOLERANCE = 1;
const MAX_MOVE_GOAL_TOLERANCE = 30;

const DEFAULT_CONTROL_MODE = 'command';
const SUPPORTED_CONTROL_MODES = new Set(['command', 'vpt', 'hybrid']);
const DEFAULT_VPT_TICK_INTERVAL_MS = 50;
const MIN_VPT_TICK_INTERVAL_MS = 10;
const MAX_VPT_TICK_INTERVAL_MS = 250;
const DEFAULT_VPT_MAX_SEQUENCE_LENGTH = 240;
const MIN_VPT_MAX_SEQUENCE_LENGTH = 1;
const MAX_VPT_MAX_SEQUENCE_LENGTH = 2000;

/**
 * MOVE_GOAL_TOLERANCE の入力を安全に正規化する。
 * - 未設定時は README で説明している既定値 (3 ブロック) を利用
 * - 数値化できない入力は既定値にフォールバックし、警告を追加
 * - 1 未満や 30 を超える値は上下限へ丸め、pathfinder の暴走を抑止する
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

export function resolveControlMode(rawMode: string | undefined): ControlModeResolution {
  const warnings: string[] = [];
  const sanitized = (rawMode ?? '').trim().toLowerCase();

  if (sanitized.length === 0) {
    return { mode: DEFAULT_CONTROL_MODE, warnings };
  }

  if (SUPPORTED_CONTROL_MODES.has(sanitized)) {
    return { mode: sanitized as 'command' | 'vpt' | 'hybrid', warnings };
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
      const clamped = Math.min(Math.max(parsed, MIN_VPT_MAX_SEQUENCE_LENGTH), MAX_VPT_MAX_SEQUENCE_LENGTH);
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
