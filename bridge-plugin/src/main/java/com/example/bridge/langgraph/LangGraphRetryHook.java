package com.example.bridge.langgraph;

import java.util.logging.Logger;

/**
 * LangGraph のリトライノードを呼び出すための抽象フック。
 *
 * 障害検知トリガーから利用され、Paper 側で接続断を検出した際に
 * LangGraph へ再試行ノードの起動を通知する。
 */
public interface LangGraphRetryHook {

    void triggerRetry(String nodeId, String checkpointId, String reason, String eventLevel);

    static LangGraphRetryHook noop(Logger logger) {
        return (nodeId, checkpointId, reason, eventLevel) ->
                logger.fine(() -> "LangGraph retry hook disabled node=" + nodeId + " checkpoint=" + checkpointId);
    }
}
