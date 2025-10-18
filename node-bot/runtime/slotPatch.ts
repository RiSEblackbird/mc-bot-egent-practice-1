// 日本語コメント：1.21 系プロトコルにおける Slot 定義差分を吸収する
// 役割：mineflayer がまだ対応していない追加フィールドを customPackets 経由で上書きする
import { versions as minecraftProtocolVersions } from 'minecraft-data';

// Mineflayer / minecraft-data が提供するプロトコル情報の型（最小限のフィールドのみ利用）。
type PcProtocolVersionEntry = { minecraftVersion: string };

// 1.21 系（1.21, 1.21.1, ... 1.21.8 など）を一括検出するためのメジャーバージョン指定。
const SLOT_PATCH_MAJOR_VERSION_PREFIXES = ['1.21'] as const;

/**
 * minecraft-data のプロトコル定義から、Slot パッチの適用対象となるバージョン一覧を抽出する。
 * `1.21` 系列はリリースのたびにサフィックス（.5, .8 など）が増えるため、
 * 手動メンテではなく自動抽出に切り替えて保守負担と見落としリスクを下げる。
 */
export function collectSlotProtocolVersions(
  majorVersionPrefixes: readonly string[] = SLOT_PATCH_MAJOR_VERSION_PREFIXES,
  entries: readonly PcProtocolVersionEntry[] = minecraftProtocolVersions.pc as PcProtocolVersionEntry[],
): readonly string[] {
  const detected: string[] = [];
  const seen = new Set<string>();

  for (const { minecraftVersion } of entries) {
    const matchesTargetMajor = majorVersionPrefixes.some(
      (prefix) => minecraftVersion === prefix || minecraftVersion.startsWith(`${prefix}.`),
    );

    if (!matchesTargetMajor || seen.has(minecraftVersion)) {
      continue;
    }

    detected.push(minecraftVersion);
    seen.add(minecraftVersion);
  }

  return detected;
}

/**
 * 追加フィールドを考慮した Slot 定義を生成する。
 *
 * 注意点：Paper 1.21.1 では tailCustomData/tailItemData のような末尾フィールドは送信されない。
 * これらを誤って追加すると、Mineflayer 側で itemCount 読み取り時に巨大な配列長を解釈して
 * `array size is abnormally large` エラーが発生するため、公式プロトコル定義と同じ構造を厳守する。
 * オブジェクトは Mineflayer 側で書き換えられる可能性があるため、都度新しい参照を返す。
 */
function createSlotOverride(): { types: Record<string, unknown> } {
  return {
    types: {
      Slot: [
        'container',
        [
          { name: 'itemCount', type: 'varint' },
          {
            anon: true,
            type: [
              'switch',
              {
                compareTo: 'itemCount',
                fields: { '0': 'void' },
                default: [
                  'container',
                  [
                    { name: 'itemId', type: 'varint' },
                    { name: 'addedComponentCount', type: 'varint' },
                    { name: 'removedComponentCount', type: 'varint' },
                    {
                      name: 'components',
                      type: ['array', { count: 'addedComponentCount', type: 'SlotComponent' }],
                    },
                    {
                      name: 'removeComponents',
                      type: [
                        'array',
                        {
                          count: 'removedComponentCount',
                          type: ['container', [{ name: 'type', type: 'SlotComponentType' }]],
                        },
                      ],
                    },
                  ],
                ],
              },
            ],
          },
        ],
      ],
    },
  };
}

// minecraft-data から自動抽出した 1.21 系バージョンに対して Slot パッチを適用する。
export const SLOT_PROTOCOL_VERSIONS = collectSlotProtocolVersions();

/**
 * Mineflayer の `customPackets` オプションへ渡すための Slot 差分パッチ。
 * 将来的にサポートバージョンが増えた際も、この関数の引数を拡張するだけで対応可能。
 */
export function buildCustomSlotPatch(
  versions: readonly string[] = SLOT_PROTOCOL_VERSIONS,
): Record<string, { types: Record<string, unknown> }> {
  return Object.fromEntries(versions.map((version) => [version, createSlotOverride()])) as Record<
    string,
    { types: Record<string, unknown> }
  >;
}

export const CUSTOM_SLOT_PATCH = buildCustomSlotPatch();
