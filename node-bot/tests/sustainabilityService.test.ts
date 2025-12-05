import type { Bot } from 'mineflayer';
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';

import { SustainabilityService } from '../runtime/services/sustainabilityService.js';

describe('SustainabilityService', () => {
  const chatMessenger = { sendChat: vi.fn<boolean, [string]>() };
  const config = { starvationFoodLevel: 0, hungerWarningCooldownMs: 30_000 };
  let service: SustainabilityService;

  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2024-01-01T00:00:00Z'));
    chatMessenger.sendChat.mockReset();
    service = new SustainabilityService(config, { chatMessenger });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('warns about missing food with cooldown', async () => {
    const bot = {
      food: 0,
      inventory: { items: () => [] },
      equip: vi.fn(),
      consume: vi.fn(),
    } as unknown as Bot;

    chatMessenger.sendChat.mockReturnValue(true);

    await service.monitorCriticalHunger(bot);
    await service.monitorCriticalHunger(bot);
    expect(chatMessenger.sendChat).toHaveBeenCalledTimes(1);

    vi.setSystemTime(new Date('2024-01-01T00:01:00Z'));
    await service.monitorCriticalHunger(bot);
    expect(chatMessenger.sendChat).toHaveBeenCalledTimes(2);
  });

  it('consumes available food and notifies success', async () => {
    const bot = {
      food: 0,
      inventory: { items: () => [{ name: 'apple' }] },
      equip: vi.fn().mockResolvedValue(undefined),
      consume: vi.fn().mockResolvedValue(undefined),
    } as unknown as Bot;

    chatMessenger.sendChat.mockReturnValue(true);
    service.updateFoodDictionary({ apple: {} } as any);

    await service.monitorCriticalHunger(bot);

    expect(bot.equip).toHaveBeenCalled();
    expect(bot.consume).toHaveBeenCalled();
    expect(chatMessenger.sendChat).toHaveBeenCalledWith('空腹のため手持ちの食料を食べました。');
  });
});
