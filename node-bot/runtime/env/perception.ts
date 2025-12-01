// 日本語コメント：知覚（perception）関連の設定値をまとめて正規化する
// 役割：エンティティ/ブロック探索範囲やブロードキャスト間隔の上下限チェックを一箇所に集約する

export interface PerceptionResolution {
  entityRadius: number;
  blockRadius: number;
  blockHeight: number;
  broadcastIntervalMs: number;
  warnings: string[];
}

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

const normalize = (
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

export function resolvePerceptionConfig(
  rawEntityRadius: string | undefined,
  rawBlockRadius: string | undefined,
  rawBlockHeight: string | undefined,
  rawBroadcastInterval: string | undefined,
): PerceptionResolution {
  const warnings: string[] = [];

  const entityRadius = normalize(
    rawEntityRadius,
    DEFAULT_PERCEPTION_ENTITY_RADIUS,
    MIN_PERCEPTION_ENTITY_RADIUS,
    MAX_PERCEPTION_ENTITY_RADIUS,
    'PERCEPTION_ENTITY_RADIUS',
    warnings,
  );
  const blockRadius = normalize(
    rawBlockRadius,
    DEFAULT_PERCEPTION_BLOCK_RADIUS,
    MIN_PERCEPTION_BLOCK_RADIUS,
    MAX_PERCEPTION_BLOCK_RADIUS,
    'PERCEPTION_BLOCK_RADIUS',
    warnings,
  );
  const blockHeight = normalize(
    rawBlockHeight,
    DEFAULT_PERCEPTION_BLOCK_HEIGHT,
    MIN_PERCEPTION_BLOCK_HEIGHT,
    MAX_PERCEPTION_BLOCK_HEIGHT,
    'PERCEPTION_BLOCK_HEIGHT',
    warnings,
  );
  const broadcastIntervalMs = normalize(
    rawBroadcastInterval,
    DEFAULT_PERCEPTION_BROADCAST_INTERVAL_MS,
    MIN_PERCEPTION_BROADCAST_INTERVAL_MS,
    MAX_PERCEPTION_BROADCAST_INTERVAL_MS,
    'PERCEPTION_BROADCAST_INTERVAL_MS',
    warnings,
  );

  return {
    entityRadius,
    blockRadius,
    blockHeight,
    broadcastIntervalMs,
    warnings,
  };
}
