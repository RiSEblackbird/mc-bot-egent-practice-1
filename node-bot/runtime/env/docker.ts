// 日本語コメント：Docker 実行環境の検知ロジックを専用化し、ファイルシステム依存を注入できるようにする
// 役割：/.dockerenv や cgroup の検査を一箇所に集約し、ユニットテストで疑似 FS を差し替え可能にする
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
 * Docker コンテナ内で実行されているかを判定する純粋関数。
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
