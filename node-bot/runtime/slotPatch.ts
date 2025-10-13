// 日本語コメント：1.21 系プロトコルにおける Slot 定義差分を吸収する
// 役割：mineflayer がまだ対応していない追加フィールドを customPackets 経由で上書きする

/**
 * 追加フィールドを考慮した Slot 定義を生成する。
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
                    { name: 'tailCustomData', type: ['option', 'anonymousNbt'] },
                    { name: 'tailItemData', type: ['option', 'anonymousNbt'] },
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

// 1.21.4 以降は同じプロトコル番号を共有しているため、既知のバリアントをすべて列挙しておく。
export const SLOT_PROTOCOL_VERSIONS = [
  '1.21',
  '1.21.1',
  '1.21.2',
  '1.21.3',
  '1.21.4',
  '1.21.4-rc1',
  '1.21.4-rc2',
] as const;

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
