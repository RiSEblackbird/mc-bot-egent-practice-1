// 日本語コメント：Bot 設定ローダーのユニットテスト
// 役割：環境変数の正規化や警告集約が期待どおり行われるかを検証する
import { describe, expect, it } from 'vitest';

import {
  loadBotRuntimeConfig,
  resolveMinecraftVersionLabel,
  type ConfigDependencies,
} from '../runtime/config.js';

describe('resolveMinecraftVersionLabel', () => {
  it('サポートされているバージョンは警告なしで採用する', () => {
    const result = resolveMinecraftVersionLabel('1.21.1');
    expect(result.version).toBe('1.21.1');
    expect(result.warnings).toHaveLength(0);
  });

  it('未知のバージョンは既定値へフォールバックし警告を出す', () => {
    const result = resolveMinecraftVersionLabel('9.9.9');
    expect(result.version).toBe('1.21.1');
    expect(result.warnings.length).toBeGreaterThan(0);
  });
});

describe('loadBotRuntimeConfig', () => {
  const fakeDeps: ConfigDependencies = {
    detectDockerRuntime: () => true,
  };

  it('Docker 環境では localhost を host.docker.internal へ置き換える', () => {
    const env = {
      MC_HOST: 'localhost',
      MC_VERSION: '1.21.1',
      WS_HOST: '0.0.0.0',
      MOVE_GOAL_TOLERANCE: '45',
    } as NodeJS.ProcessEnv;

    const { config, warnings } = loadBotRuntimeConfig(env, fakeDeps);

    expect(config.minecraft.host).toBe('host.docker.internal');
    expect(config.websocket.host).toBe('0.0.0.0');
    expect(config.moveGoalTolerance.tolerance).toBe(30);
    expect(warnings).toContain(
      'MC_HOST points to localhost inside Docker. Falling back to host.docker.internal so the Paper server is reachable.',
    );
  });

  it('環境変数が未設定でも安全な既定値を返す', () => {
    const { config, warnings } = loadBotRuntimeConfig({}, fakeDeps);

    expect(config.minecraft.port).toBe(25565);
    expect(config.minecraft.username).toBe('HelperBot');
    expect(config.agentBridge.url).toBe('ws://python-agent:9000');
    expect(warnings).toBeInstanceOf(Array);
  });
});
