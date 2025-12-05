import type { Bot } from 'mineflayer';
import type { Item } from 'prismarine-item';

import type { ChatMessenger } from './chatBridge.js';
import type { FoodDictionary } from '../snapshots.js';

export interface SustainabilityConfig {
  starvationFoodLevel: number;
  hungerWarningCooldownMs: number;
}

export type SustainabilityLogger = (level: 'info' | 'warn' | 'error', message: string, context?: Record<string, unknown>) => void;

/**
 * Bot の生存に関わるロジック（空腹検知と食料消費）を集約する専用サービス。
 *
 * Bot のライフサイクル毎にインスタンス化し、空腹関連の状態（食料辞書のキャッシュや警告クールダウン）を
 * 外部へ漏らさないようにする。テストでは ChatMessenger を差し替えることでチャット送信をモックできる。
 */
export class SustainabilityService {
  private readonly config: SustainabilityConfig;

  private readonly chatMessenger: ChatMessenger;

  private readonly logger: SustainabilityLogger;

  private cachedFoodsByName: FoodDictionary = {};

  private isConsumingFood = false;

  private lastHungerWarningAt = 0;

  constructor(config: SustainabilityConfig, dependencies: { chatMessenger: ChatMessenger; logger?: SustainabilityLogger }) {
    this.config = config;
    this.chatMessenger = dependencies.chatMessenger;
    this.logger = dependencies.logger ?? ((level, message, context) => {
      const payload = context ? `${message} ${JSON.stringify(context)}` : message;
      if (level === 'warn') {
        console.warn(payload);
      } else if (level === 'error') {
        console.error(payload);
      } else {
        console.info(payload);
      }
    });
  }

  /**
   * minecraft-data から取得した食品辞書をセットし、インベントリ探索に利用する。
   */
  updateFoodDictionary(foodDictionary: FoodDictionary): void {
    this.cachedFoodsByName = foodDictionary ?? {};
  }

  /**
   * 空腹が深刻化した際の自動対応を実行する。食料が無い場合はクールダウン付きで警告を発し、
   * 食料があれば装備して消費する。装備・消費は Mineflayer の Bot API を用いて非同期で実行する。
   */
  async monitorCriticalHunger(targetBot: Bot): Promise<void> {
    if (targetBot.food > this.config.starvationFoodLevel) {
      return;
    }

    if (this.isConsumingFood) {
      return;
    }

    const edible = this.findEdibleItem(targetBot);

    if (!edible) {
      this.notifyFoodShortage();
      return;
    }

    this.isConsumingFood = true;
    try {
      await targetBot.equip(edible, 'hand');
      await targetBot.consume();
      this.chatMessenger.sendChat('空腹のため手持ちの食料を食べました。');
    } catch (error) {
      this.logger('error', '[Hunger] failed to consume food', { error: error instanceof Error ? error.message : String(error) });
    } finally {
      this.isConsumingFood = false;
    }
  }

  /**
   * インベントリ内から最初に見つかった食料アイテムを返す。
   */
  private findEdibleItem(targetBot: Bot): Item | undefined {
    return targetBot
      .inventory
      .items()
      .find((item) => Boolean(this.cachedFoodsByName[item.name]));
  }

  /**
   * 手元に食料が無い場合に、クールダウン付きでチャット警告を送信する。
   */
  private notifyFoodShortage(): void {
    const now = Date.now();
    if (now - this.lastHungerWarningAt < this.config.hungerWarningCooldownMs) {
      return;
    }

    const delivered = this.chatMessenger.sendChat('空腹ですが食料を所持していません。補給をお願いします。');
    if (!delivered) {
      this.logger('warn', '[Hunger] failed to notify food shortage because chat messenger was unavailable');
    }
    this.lastHungerWarningAt = now;
  }
}
