package com.example.bridge.events;

import java.util.List;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.LinkedBlockingQueue;

/**
 * JobRegistry や保護プラグインからの更新を配信するためのシンプルなイベントハブ。
 * スレッドセーフなキューを購読者ごとに発行し、SSE/WS から非同期に読み出せるようにする。
 */
public final class BridgeEventHub {

    private final List<BlockingQueue<BridgeEvent>> subscribers = new CopyOnWriteArrayList<>();

    public BlockingQueue<BridgeEvent> subscribe() {
        BlockingQueue<BridgeEvent> queue = new LinkedBlockingQueue<>();
        subscribers.add(queue);
        return queue;
    }

    public void unsubscribe(BlockingQueue<BridgeEvent> queue) {
        subscribers.remove(queue);
    }

    public void publish(BridgeEvent event) {
        for (BlockingQueue<BridgeEvent> queue : subscribers) {
            queue.offer(event);
        }
    }
}
