package com.example.bridge.http.handlers;

import com.example.bridge.AgentBridgePlugin;
import com.example.bridge.events.BridgeEventHub;
import com.example.bridge.http.BaseHandler;
import com.example.bridge.langgraph.LangGraphRetryHook;
import com.example.bridge.util.AgentBridgeConfig;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpExchange;
import java.io.IOException;
import java.util.logging.Logger;

/**
 * LangGraph からの切断通知を受け取り、再試行フックを起動するハンドラ。
 */
public final class DisconnectionHandler extends BaseHandler {

    private final LangGraphRetryHook retryHook;

    public DisconnectionHandler(
            AgentBridgePlugin plugin,
            AgentBridgeConfig config,
            ObjectMapper mapper,
            Logger logger,
            BridgeEventHub eventHub,
            LangGraphRetryHook retryHook) {
        super(plugin, config, mapper, logger, eventHub);
        this.retryHook = retryHook;
    }

    @Override
    protected void handleAuthed(HttpExchange exchange) throws IOException {
        ensureMethod(exchange, "POST");
        JsonNode root = parseBody(exchange);
        String nodeId = requiredText(root, "node_id");
        String checkpointId = root.path("checkpoint_id").asText("");
        String reason = root.path("reason").asText("disconnected");
        String eventLevel = root.path("event_level").asText("error");
        logger().info(() -> "LangGraph disconnect detected node=" + nodeId + " checkpoint=" + checkpointId);
        retryHook.triggerRetry(nodeId, checkpointId, reason, eventLevel);
        var response = mapper().createObjectNode();
        response.put("ok", true);
        sendJson(exchange, 200, response);
    }
}
