// 日本語コメント：設定値ヘルパーのユニットテスト
// 役割：派生済み設定の生成とログ出力委譲が期待通りかを検証する
import { describe, expect, it } from 'vitest';

import { type ConfigDependencies } from '../runtime/config.js';
import { loadConfigValues } from '../runtime/configValues.js';

describe('loadConfigValues', () => {
  it('環境変数から派生値を構築し、ログをカスタムロガーへ委譲する', () => {
    const env = {
      MC_HOST: 'localhost',
      MC_PORT: '25566',
      CONTROL_MODE: 'hybrid',
      VPT_TICK_INTERVAL_MS: '25',
      VPT_MAX_SEQUENCE_LENGTH: '15',
      MC_VERSION: '9.9.9',
      OTEL_TRACES_SAMPLER_RATIO: 'invalid',
    } as NodeJS.ProcessEnv;

    const infoLogs: string[] = [];
    const warningLogs: string[] = [];
    const logger = {
      info: (message: string) => infoLogs.push(message),
      warn: (message: string) => warningLogs.push(message),
    };
    const deps: ConfigDependencies = { detectDockerRuntime: () => false };

    const values = loadConfigValues(env, logger, deps);

    expect(values.minecraft.host).toBe('localhost');
    expect(values.minecraft.port).toBe(25566);
    expect(values.control.mode).toBe('hybrid');
    expect(values.control.vptCommandsEnabled).toBe(true);
    expect(values.control.tickIntervalMs).toBe(25);
    expect(values.control.maxSequenceLength).toBe(15);
    expect(values.telemetry.samplerRatio).toBe(1);
    expect(warningLogs.length).toBeGreaterThan(0);
    expect(infoLogs.some((message) => message.includes('mode=hybrid'))).toBe(true);
  });
});
