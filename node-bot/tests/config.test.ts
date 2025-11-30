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
    expect(config.telemetry.endpoint).toBe('http://localhost:4318');
    expect(config.perception.entityRadius).toBeGreaterThan(0);
    expect(warnings).toContain(
      'MC_HOST points to localhost inside Docker. Falling back to host.docker.internal so the Paper server is reachable.',
    );
  });

  it('環境変数が未設定でも安全な既定値を返す', () => {
    const { config, warnings } = loadBotRuntimeConfig({}, fakeDeps);

    expect(config.minecraft.port).toBe(25565);
    expect(config.minecraft.username).toBe('HelperBot');
    expect(config.agentBridge.url).toBe('ws://python-agent:9000');
    expect(config.control.mode).toBe('command');
    expect(config.control.vpt.tickIntervalMs).toBeGreaterThan(0);
    expect(config.telemetry.serviceName).toBe('mc-node-bot');
    expect(config.perception.entityRadius).toBe(12);
    expect(config.perception.blockRadius).toBe(4);
    expect(config.perception.broadcastIntervalMs).toBe(1500);
    expect(warnings).toBeInstanceOf(Array);
  });

  it('VPT 関連の環境変数を解釈して警告を集約する', () => {
    const env = {
      CONTROL_MODE: 'vpt',
      VPT_TICK_INTERVAL_MS: '15',
      VPT_MAX_SEQUENCE_LENGTH: '500',
    } as NodeJS.ProcessEnv;

    const { config, warnings } = loadBotRuntimeConfig(env, fakeDeps);

    expect(config.control.mode).toBe('vpt');
    expect(config.control.vpt.tickIntervalMs).toBe(15);
    expect(config.control.vpt.maxSequenceLength).toBe(500);
    expect(warnings).toBeInstanceOf(Array);
  });

  it('hybrid モードを有効化できる', () => {
    const env = {
      CONTROL_MODE: 'hybrid',
    } as NodeJS.ProcessEnv;

    const { config } = loadBotRuntimeConfig(env, fakeDeps);
    expect(config.control.mode).toBe('hybrid');
  });

  it('OTEL_TRACES_SAMPLER_RATIO が無効な場合は警告して 1.0 へ丸める', () => {
    const env = {
      OTEL_TRACES_SAMPLER_RATIO: 'invalid',
    } as NodeJS.ProcessEnv;

    const { config, warnings } = loadBotRuntimeConfig(env, fakeDeps);

    expect(config.telemetry.samplerRatio).toBe(1);
    expect(warnings.some((warning) => warning.includes('OTEL_TRACES_SAMPLER_RATIO'))).toBe(true);
  });

  it('perception 設定が範囲外の場合は丸めて警告する', () => {
    const env = {
      PERCEPTION_ENTITY_RADIUS: '0',
      PERCEPTION_BLOCK_RADIUS: '99',
      PERCEPTION_BLOCK_HEIGHT: '-1',
      PERCEPTION_BROADCAST_INTERVAL_MS: 'foo',
    } as NodeJS.ProcessEnv;

    const { config, warnings } = loadBotRuntimeConfig(env, fakeDeps);

    expect(config.perception.entityRadius).toBeGreaterThanOrEqual(1);
    expect(config.perception.blockRadius).toBeLessThanOrEqual(16);
    expect(config.perception.blockHeight).toBeGreaterThanOrEqual(1);
    expect(config.perception.broadcastIntervalMs).toBe(1_500);
    expect(warnings.some((warning) => warning.includes('PERCEPTION_ENTITY_RADIUS'))).toBe(true);
    expect(warnings.some((warning) => warning.includes('PERCEPTION_BLOCK_RADIUS'))).toBe(true);
    expect(warnings.some((warning) => warning.includes('PERCEPTION_BLOCK_HEIGHT'))).toBe(true);
    expect(warnings.some((warning) => warning.includes('PERCEPTION_BROADCAST_INTERVAL_MS'))).toBe(true);
  });
});
