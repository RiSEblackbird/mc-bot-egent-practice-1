// 日本語コメント：Slot 差分パッチの健全性テスト
// 役割：対応バージョンとオブジェクト生成が期待通りかを検証する
import { describe, expect, it } from 'vitest';
import {
  buildCustomSlotPatch,
  collectSlotProtocolVersions,
  CUSTOM_SLOT_PATCH,
  SLOT_PROTOCOL_VERSIONS,
} from '../runtime/slotPatch.js';

describe('SLOT_PROTOCOL_VERSIONS', () => {
  it('1.21 系列の既知バージョンを網羅している', () => {
    expect(SLOT_PROTOCOL_VERSIONS.length).toBeGreaterThanOrEqual(5);
    expect(SLOT_PROTOCOL_VERSIONS[0]).toBe('1.21');
    expect(new Set(SLOT_PROTOCOL_VERSIONS).size).toBe(SLOT_PROTOCOL_VERSIONS.length);
    expect(SLOT_PROTOCOL_VERSIONS.every((version) => version.startsWith('1.21'))).toBe(true);
    expect(SLOT_PROTOCOL_VERSIONS).toContain('1.21.8');
  });
});

describe('buildCustomSlotPatch', () => {
  it('各バージョンに Slot 定義が割り当てられる', () => {
    const patch = buildCustomSlotPatch();
    for (const version of SLOT_PROTOCOL_VERSIONS) {
      expect(patch).toHaveProperty([version, 'types', 'Slot']);
    }
  });

  it('各バージョンのオーバーライドは独立したオブジェクトを返す', () => {
    const patch = buildCustomSlotPatch(['1.21', '1.21.1']);
    expect(patch['1.21']).not.toBe(patch['1.21.1']);
  });
});

describe('CUSTOM_SLOT_PATCH', () => {
  it('1.21.4 を含む全バージョンで使用可能', () => {
    expect(CUSTOM_SLOT_PATCH).toHaveProperty(['1.21.4', 'types', 'Slot']);
    expect(Object.keys(CUSTOM_SLOT_PATCH)).toEqual(expect.arrayContaining([...SLOT_PROTOCOL_VERSIONS]));
  });

  it('collectSlotProtocolVersions はメジャーバージョン指定で拡張可能', () => {
    const mockEntries = [
      { minecraftVersion: '1.21' },
      { minecraftVersion: '1.21.1' },
      { minecraftVersion: '1.22' },
    ];
    const detected = collectSlotProtocolVersions(['1.22'], mockEntries);
    expect(detected).toEqual(['1.22']);
  });
});
