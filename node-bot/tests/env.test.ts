// 日本語コメント：環境関連ユーティリティの回帰テスト
// 役割：Docker 判定やホスト名解決が期待どおり動くかを確認する
import { describe, expect, it } from 'vitest';
import {
  detectDockerRuntime,
  parseEnvInt,
  resolveAgentWebSocketEndpoint,
  resolveControlMode,
  resolveMinecraftHostValue,
  resolveMoveGoalTolerance,
  resolveVptPlaybackConfig,
  type DockerDetectionDeps,
} from '../runtime/env.js';

describe('parseEnvInt', () => {
  it('数値文字列を正しく解釈する', () => {
    expect(parseEnvInt('123', 0)).toBe(123);
  });

  it('NaN になる入力はフォールバック値を返す', () => {
    expect(parseEnvInt('abc', 42)).toBe(42);
    expect(parseEnvInt(undefined, 99)).toBe(99);
  });
});

describe('resolveMinecraftHostValue', () => {
  it('明示的にホスト名が指定されていればそのまま利用する', () => {
    const result = resolveMinecraftHostValue('mc.example.com', false);
    expect(result).toEqual({
      host: 'mc.example.com',
      originalValue: 'mc.example.com',
      usedDockerFallback: false,
      usedDefaultHost: false,
    });
  });

  it('Docker 環境で localhost が指定された場合は host.docker.internal へ差し替える', () => {
    const result = resolveMinecraftHostValue('localhost', true);
    expect(result).toEqual({
      host: 'host.docker.internal',
      originalValue: 'localhost',
      usedDockerFallback: true,
      usedDefaultHost: false,
    });
  });

  it('ホスト未指定時は Docker 有無に応じた既定値を返す', () => {
    const nonDocker = resolveMinecraftHostValue(undefined, false);
    expect(nonDocker).toEqual({
      host: '127.0.0.1',
      originalValue: '',
      usedDockerFallback: false,
      usedDefaultHost: true,
    });

    const docker = resolveMinecraftHostValue('', true);
    expect(docker).toEqual({
      host: 'host.docker.internal',
      originalValue: '',
      usedDockerFallback: true,
      usedDefaultHost: true,
    });
  });
});

describe('detectDockerRuntime', () => {
  it('`/.dockerenv` が存在すれば true を返す', () => {
    const deps: DockerDetectionDeps = {
      existsSync: () => true,
      readFileSync: () => '',
    };
    expect(detectDockerRuntime(deps)).toBe(true);
  });

  it('cgroup に docker が含まれる場合も true を返す', () => {
    const deps: DockerDetectionDeps = {
      existsSync: () => false,
      readFileSync: () => '0::/docker/123',
    };
    expect(detectDockerRuntime(deps)).toBe(true);
  });

  it('情報が得られなかった場合は false を返す', () => {
    const deps: DockerDetectionDeps = {
      existsSync: () => false,
      readFileSync: () => {
        throw new Error('not accessible');
      },
    };
    expect(detectDockerRuntime(deps)).toBe(false);
  });
});

describe('resolveMoveGoalTolerance', () => {
  it('未設定の場合は既定値 3 と警告なしを返す', () => {
    expect(resolveMoveGoalTolerance(undefined)).toEqual({ tolerance: 3, warnings: [] });
  });

  it('有効な数値はそのまま採用し警告を出さない', () => {
    expect(resolveMoveGoalTolerance('5')).toEqual({ tolerance: 5, warnings: [] });
  });

  it('数値化できない入力はフォールバックし警告を追加する', () => {
    const result = resolveMoveGoalTolerance('abc');
    expect(result.tolerance).toBe(3);
    expect(result.warnings).toHaveLength(1);
  });

  it('下限未満の値は 1 へ丸める', () => {
    const result = resolveMoveGoalTolerance('0');
    expect(result.tolerance).toBe(1);
    expect(result.warnings).toHaveLength(1);
  });

  it('上限を超える値は 30 へ丸める', () => {
    const result = resolveMoveGoalTolerance('100');
    expect(result.tolerance).toBe(30);
    expect(result.warnings).toHaveLength(1);
  });
});

describe('resolveAgentWebSocketEndpoint', () => {
  it('URL が明示されていればそのまま利用する', () => {
    const result = resolveAgentWebSocketEndpoint('wss://example.local/ws', undefined, undefined, false);
    expect(result).toMatchObject({
      url: 'wss://example.local/ws',
      host: '127.0.0.1',
      port: 9000,
      usedExplicitUrl: true,
      usedDefaultHost: true,
      usedDefaultPort: true,
    });
    expect(result.warnings).toHaveLength(0);
  });

  it('Docker 環境では既定で python-agent:9000 を指す', () => {
    const result = resolveAgentWebSocketEndpoint(undefined, undefined, undefined, true);
    expect(result).toMatchObject({
      url: 'ws://python-agent:9000',
      host: 'python-agent',
      port: 9000,
      usedExplicitUrl: false,
      usedDefaultHost: true,
      usedDefaultPort: true,
    });
  });

  it('ホストとポートの明示指定を尊重する', () => {
    const result = resolveAgentWebSocketEndpoint(undefined, 'agent.example.com', '9100', false);
    expect(result).toMatchObject({
      url: 'ws://agent.example.com:9100',
      host: 'agent.example.com',
      port: 9100,
      usedExplicitUrl: false,
      usedDefaultHost: false,
      usedDefaultPort: false,
    });
    expect(result.warnings).toHaveLength(0);
  });

  it('ポートが数値化できない場合は既定値へフォールバックする', () => {
    const result = resolveAgentWebSocketEndpoint(undefined, 'agent', 'abc', false);
    expect(result.port).toBe(9000);
    expect(result.usedDefaultPort).toBe(true);
    expect(result.warnings).toHaveLength(1);
  });

  it('URL にスキームが含まれていなければ ws:// を補完する', () => {
    const result = resolveAgentWebSocketEndpoint('python-agent:9100', undefined, undefined, false);
    expect(result.url).toBe('ws://python-agent:9100');
    expect(result.usedExplicitUrl).toBe(true);
    expect(result.warnings).toContain(
      "AGENT_WS_URL='python-agent:9100' にスキームが含まれていないため ws:// を補完しました。",
    );
  });

  it('0.0.0.0 が指定された場合は警告付きで既定ホストへフォールバックする', () => {
    const result = resolveAgentWebSocketEndpoint(undefined, '0.0.0.0', undefined, false);
    expect(result.host).toBe('127.0.0.1');
    expect(result.url).toBe('ws://127.0.0.1:9000');
    expect(result.usedDefaultHost).toBe(true);
    expect(result.warnings).toContain(
      "AGENT_WS_HOST='0.0.0.0' は接続先として利用できないため 127.0.0.1 へフォールバックします。",
    );
  });

  it('接続ヘルスチェックやバッチ設定を環境変数で上書きできる', () => {
    const result = resolveAgentWebSocketEndpoint(undefined, undefined, undefined, false, {
      rawConnectTimeoutMs: '7000',
      rawSendTimeoutMs: '8000',
      rawHealthcheckIntervalMs: '60000',
      rawReconnectDelayMs: '1500',
      rawMaxRetries: '5',
      rawBatchIntervalMs: '500',
      rawBatchMaxSize: '20',
      rawQueueMaxSize: '400',
    });

    expect(result.connectTimeoutMs).toBe(7000);
    expect(result.sendTimeoutMs).toBe(8000);
    expect(result.healthcheckIntervalMs).toBe(60000);
    expect(result.reconnectDelayMs).toBe(1500);
    expect(result.maxRetries).toBe(5);
    expect(result.batchFlushIntervalMs).toBe(500);
    expect(result.batchMaxSize).toBe(20);
    expect(result.queueMaxSize).toBe(400);
  });

  it('範囲外の値は安全な最小・最大へ丸められる', () => {
    const result = resolveAgentWebSocketEndpoint(undefined, undefined, undefined, false, {
      rawConnectTimeoutMs: '100',
      rawSendTimeoutMs: '999999',
      rawHealthcheckIntervalMs: '10',
      rawReconnectDelayMs: '-1',
      rawMaxRetries: '99',
      rawBatchIntervalMs: '1',
      rawBatchMaxSize: '0',
      rawQueueMaxSize: '2',
    });

    expect(result.connectTimeoutMs).toBe(500);
    expect(result.sendTimeoutMs).toBe(120000);
    expect(result.healthcheckIntervalMs).toBe(1000);
    expect(result.reconnectDelayMs).toBe(250);
    expect(result.maxRetries).toBe(10);
    expect(result.batchFlushIntervalMs).toBe(50);
    expect(result.batchMaxSize).toBe(1);
    expect(result.queueMaxSize).toBe(10);
    expect(result.warnings.length).toBeGreaterThan(0);
  });
});

describe('resolveControlMode', () => {
  it('未設定時は command を返す', () => {
    expect(resolveControlMode(undefined)).toEqual({ mode: 'command', warnings: [] });
  });

  it('有効なモードはそのまま採用する', () => {
    expect(resolveControlMode('vpt')).toEqual({ mode: 'vpt', warnings: [] });
  });

  it('hybrid モードも受け付ける', () => {
    expect(resolveControlMode('hybrid')).toEqual({ mode: 'hybrid', warnings: [] });
  });

  it('未知のモードは警告付きで command へ戻す', () => {
    const result = resolveControlMode('invalid');
    expect(result.mode).toBe('command');
    expect(result.warnings).toHaveLength(1);
  });
});

describe('resolveVptPlaybackConfig', () => {
  it('未設定時は既定値を返す', () => {
    expect(resolveVptPlaybackConfig(undefined, undefined)).toEqual({
      tickIntervalMs: 50,
      maxSequenceLength: 240,
      warnings: [],
    });
  });

  it('有効な数値はそのまま採用する', () => {
    expect(resolveVptPlaybackConfig('70', '400')).toEqual({
      tickIntervalMs: 70,
      maxSequenceLength: 400,
      warnings: [],
    });
  });

  it('異常値は丸めと警告を出す', () => {
    const result = resolveVptPlaybackConfig('5', '99999');
    expect(result.tickIntervalMs).toBeGreaterThanOrEqual(10);
    expect(result.maxSequenceLength).toBeLessThanOrEqual(2000);
    expect(result.warnings.length).toBeGreaterThanOrEqual(1);
  });
});
