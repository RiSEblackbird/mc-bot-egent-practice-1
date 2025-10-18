// 日本語コメント：環境変数や実行環境に依存する処理を集約したユーティリティ
// 役割：bot.ts から切り離し、単体テストでも検証できるようにする
import { existsSync as defaultExistsSync, readFileSync as defaultReadFileSync } from 'node:fs';

// 移動許容値の上下限は複数箇所で利用するため定数化して明示する。
const DEFAULT_MOVE_GOAL_TOLERANCE = 3;
const MIN_MOVE_GOAL_TOLERANCE = 1;
const MAX_MOVE_GOAL_TOLERANCE = 30;

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
 * 数値型環境変数を安全に読み込むユーティリティ。
 * 数値化に失敗した場合はフォールバック値を返し、NaN に起因するバグを防ぐ。
 */
export function parseEnvInt(rawValue: string | undefined, fallback: number): number {
  const parsed = Number.parseInt(rawValue ?? '', 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}
