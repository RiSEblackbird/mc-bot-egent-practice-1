import { EventEmitter } from 'node:events';

import type { Bot } from 'mineflayer';
import { describe, expect, it, vi } from 'vitest';

import { BotChatMessenger, ChatBridge } from '../runtime/services/chatBridge.js';

class MockWebSocket extends EventEmitter {
  public lastSent: string | null = null;
  public terminated = false;
  public closed = false;

  send(data: string): void {
    this.lastSent = data;
  }

  terminate(): void {
    this.terminated = true;
  }

  removeAllListeners(): void {
    super.removeAllListeners();
  }

  close(): void {
    this.closed = true;
    this.emit('close');
  }
}

describe('ChatBridge', () => {
  it('reports position and forwards chat to the agent', async () => {
    const mockWebSocket = new MockWebSocket();
    const chatMessenger = { sendChat: vi.fn().mockReturnValue(true) };
    const chatBridge = new ChatBridge(
      { agentControlWebsocketUrl: 'ws://agent', currentPositionKeywords: ['いまどこ'] },
      { chatMessenger, createWebSocket: () => mockWebSocket },
    );

    const bot = {
      username: 'bot',
      entity: { position: { x: 1.2, y: 64, z: -3.7 } },
    } as unknown as Bot;

    const handlePromise = chatBridge.handleIncomingChat(bot, 'player', 'いまどこ？');
    mockWebSocket.emit('open');
    mockWebSocket.emit('message', JSON.stringify({ ok: true }));
    await handlePromise;

    expect(chatMessenger.sendChat).toHaveBeenCalledWith('現在位置は X=1 / Y=64 / Z=-4 です。');
    expect(mockWebSocket.lastSent).not.toBeNull();
    const parsed = JSON.parse(mockWebSocket.lastSent ?? '{}');
    expect(parsed.args).toEqual({ username: 'player', message: 'いまどこ？' });
  });

  it('warns when bot entity is missing and still forwards chat', async () => {
    const mockWebSocket = new MockWebSocket();
    const chatMessenger = { sendChat: vi.fn().mockReturnValue(false) };
    const chatBridge = new ChatBridge(
      { agentControlWebsocketUrl: 'ws://agent', currentPositionKeywords: ['どこ'] },
      { chatMessenger, createWebSocket: () => mockWebSocket, logger: vi.fn() },
    );

    const bot = { username: 'bot', entity: null } as unknown as Bot;

    const promise = chatBridge.handleIncomingChat(bot, 'alice', 'どこ？');
    mockWebSocket.emit('open');
    mockWebSocket.emit('message', JSON.stringify({ ok: true }));
    await promise;

    expect(mockWebSocket.lastSent).not.toBeNull();
    expect(JSON.parse(mockWebSocket.lastSent ?? '{}').args.username).toBe('alice');
  });

  it('allows BotChatMessenger to handle missing bot gracefully', () => {
    const messenger = new BotChatMessenger(() => null);
    const result = messenger.sendChat('hello');
    expect(result).toBe(false);
  });
});
