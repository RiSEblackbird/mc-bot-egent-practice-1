// 日本語コメント：環境関連ユーティリティの回帰テスト
// 役割：Docker 判定やホスト名解決が期待どおり動くかを確認する
import { describe, expect, it } from 'vitest';
import {
  detectDockerRuntime,
  parseEnvInt,
  resolveMinecraftHostValue,
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
