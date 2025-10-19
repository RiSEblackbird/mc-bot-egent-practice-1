package com.example.bridge.langgraph;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.logging.Level;
import java.util.logging.Logger;

/**
 * LangGraph のリトライノードを HTTP 経由で起動するクライアント実装。
 */
public final class LangGraphRetryClient implements LangGraphRetryHook {

    private final Logger logger;
    private final HttpClient client;
    private final URI endpoint;
    private final String apiKey;
    private final Duration timeout;
    private final ObjectMapper mapper = new ObjectMapper();

    public LangGraphRetryClient(String endpointUrl, String apiKey, int timeoutMillis, Logger logger) {
        this.logger = logger;
        this.endpoint = URI.create(endpointUrl);
        this.apiKey = apiKey == null ? "" : apiKey;
        this.timeout = Duration.ofMillis(Math.max(timeoutMillis, 250));
        this.client = HttpClient.newBuilder().connectTimeout(this.timeout).build();
    }

    @Override
    public void triggerRetry(String nodeId, String checkpointId, String reason, String eventLevel) {
        ObjectNode payload = mapper.createObjectNode();
        payload.put("node_id", nodeId);
        if (checkpointId != null && !checkpointId.isEmpty()) {
            payload.put("checkpoint_id", checkpointId);
        }
        payload.put("reason", reason);
        payload.put("event_level", eventLevel);
        payload.put("source", "agentbridge");

        HttpRequest.Builder builder = HttpRequest.newBuilder(endpoint)
                .timeout(timeout)
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(payload.toString()));
        if (!apiKey.isEmpty()) {
            builder.header("X-API-Key", apiKey);
        }
        HttpRequest request = builder.build();
        try {
            HttpResponse<String> response = client.send(request, HttpResponse.BodyHandlers.ofString());
            if (response.statusCode() >= 400) {
                logger.log(
                        Level.WARNING,
                        () -> "LangGraph retry call failed status=" + response.statusCode() + " body=" + response.body());
            } else {
                logger.info(
                        () -> "LangGraph retry triggered node=" + nodeId + " checkpoint=" + checkpointId + " level="
                                + eventLevel);
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            logger.log(Level.SEVERE, "LangGraph retry call interrupted", e);
        } catch (IOException e) {
            logger.log(Level.SEVERE, "LangGraph retry call failed", e);
        }
    }
}
