import type { Bot } from 'mineflayer';
import type { Item } from 'prismarine-item';
import type { CommandResponse } from '../types.js';

/**
 * 装備変更コマンドで Bot への参照を安全に扱うためのコンテキスト。
 * Bot が未接続のケースを呼び出し側で逐一気にせずに済むよう、取得関数を受け取る。
 */
export interface EquipItemCommandContext {
  getActiveBot: () => Bot | null;
}

type EquipDestination = 'hand' | 'off-hand';

export const EQUIP_TOOL_MATCHERS: Record<string, (item: Item) => boolean> = {
  pickaxe: (item) => item.name.endsWith('_pickaxe'),
  sword: (item) => item.name.endsWith('_sword'),
  axe: (item) => item.name.endsWith('_axe') && !item.name.endsWith('_pickaxe'),
  shovel: (item) => item.name.endsWith('_shovel') || item.name.endsWith('_spade'),
  hoe: (item) => item.name.endsWith('_hoe'),
  shield: (item) => item.name === 'shield',
  torch: (item) => item.name === 'torch',
};

/**
 * equipItem コマンドを処理するハンドラを生成する。
 * 引数の正規化と装備判定をまとめて行い、Bot を安全に操作する。
 */
export function createEquipItemCommandHandler(context: EquipItemCommandContext) {
  const { getActiveBot } = context;

  /**
   * equipItem コマンドで渡された語を Mineflayer のアイテム名と整合する形式へ正規化する。
   */
  function normalizeEquipToken(value: string): string {
    const trimmed = value.trim().toLowerCase();
    const withUnderscore = trimmed.replace(/\s+/g, '_');
    return withUnderscore.replace(/[^a-z0-9_]/g, '');
  }

  /**
   * ツール種別からインベントリ内の一致するアイテムを探索する。
   */
  function findInventoryItemByToolType(targetBot: Bot, toolTypeRaw: string): Item | null {
    const matcher = EQUIP_TOOL_MATCHERS[toolTypeRaw.toLowerCase()];

    if (!matcher) {
      return null;
    }

    return targetBot.inventory.items().find((item) => matcher(item)) ?? null;
  }

  /**
   * 任意のアイテム名から対応するインベントリアイテムを推測する。
   */
  function findInventoryItemByName(targetBot: Bot, itemNameRaw: string): Item | null {
    const normalized = normalizeEquipToken(itemNameRaw);
    const items = targetBot.inventory.items();

    const byName = items.find((item) => normalizeEquipToken(item.name) === normalized);
    if (byName) {
      return byName;
    }

    const byDisplay = items.find((item) => normalizeEquipToken(item.displayName) === normalized);
    if (byDisplay) {
      return byDisplay;
    }

    return (
      items.find((item) => normalizeEquipToken(item.name).includes(normalized)) ??
      items.find((item) => normalizeEquipToken(item.displayName).includes(normalized)) ??
      null
    );
  }

  /**
   * equipItem コマンドを処理し、指定された装備を右手または左手へ持ち替える。
   */
  async function handleEquipItemCommand(args: Record<string, unknown>): Promise<CommandResponse> {
    const toolTypeRaw = typeof args.toolType === 'string' ? args.toolType : undefined;
    const itemNameRaw = typeof args.itemName === 'string' ? args.itemName : undefined;
    const destinationRaw = typeof args.destination === 'string' ? args.destination : 'hand';
    const destination: EquipDestination = destinationRaw === 'off-hand' ? 'off-hand' : 'hand';

    if (!toolTypeRaw && !itemNameRaw) {
      return { ok: false, error: 'Either toolType or itemName must be provided' };
    }

    const activeBot = getActiveBot();

    if (!activeBot) {
      console.warn('[EquipItemCommand] rejected because bot is unavailable');
      return { ok: false, error: 'Bot is not connected to the Minecraft server yet' };
    }

    let targetItem: Item | null = null;

    if (itemNameRaw) {
      targetItem = findInventoryItemByName(activeBot, itemNameRaw);
    }

    if (!targetItem && toolTypeRaw) {
      targetItem = findInventoryItemByToolType(activeBot, toolTypeRaw);
    }

    if (!targetItem) {
      console.warn('[EquipItemCommand] requested item not found', { toolTypeRaw, itemNameRaw });
      return { ok: false, error: 'Requested item is not available in inventory' };
    }

    try {
      await activeBot.equip(targetItem, destination);
      console.log(
        `[EquipItemCommand] equipped ${targetItem.displayName ?? targetItem.name} to ${destination}`,
      );
      return { ok: true };
    } catch (error) {
      console.error('[EquipItemCommand] failed to equip item', error);
      return { ok: false, error: 'Failed to equip item' };
    }
  }

  return { handleEquipItemCommand };
}
