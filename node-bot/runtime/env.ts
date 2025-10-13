// 日本語コメント：環境変数や実行環境に依存する処理を集約したユーティリティ
// 役割：bot.ts から切り離し、単体テストでも検証できるようにする
import { existsSync as defaultExistsSync, readFileSync as defaultReadFileSync } from 'node:fs';

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
 * 数値型環境変数を安全に読み込むユーティリティ。
 * 数値化に失敗した場合はフォールバック値を返し、NaN に起因するバグを防ぐ。
 */
export function parseEnvInt(rawValue: string | undefined, fallback: number): number {
  const parsed = Number.parseInt(rawValue ?? '', 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}
