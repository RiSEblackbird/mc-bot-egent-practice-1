// 日本語コメント：Minecraft 接続先の解決ロジックをまとめたユーティリティ
// 役割：Docker 環境に応じたホスト名補正を専用化し、再利用しやすい形で提供する

export interface HostResolutionResult {
  host: string;
  originalValue: string;
  usedDockerFallback: boolean;
  usedDefaultHost: boolean;
}

const DOCKER_LOCAL_ALIAS = 'host.docker.internal';
const LOCALHOST = '127.0.0.1';

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
    const needsDockerAlias = dockerDetected && (trimmed === 'localhost' || trimmed === LOCALHOST);

    return {
      host: needsDockerAlias ? DOCKER_LOCAL_ALIAS : trimmed,
      originalValue: trimmed,
      usedDockerFallback: needsDockerAlias,
      usedDefaultHost: false,
    };
  }

  const fallbackHost = dockerDetected ? DOCKER_LOCAL_ALIAS : LOCALHOST;

  return {
    host: fallbackHost,
    originalValue: '',
    usedDockerFallback: dockerDetected,
    usedDefaultHost: true,
  };
}
