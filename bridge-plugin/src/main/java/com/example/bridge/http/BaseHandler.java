package com.example.bridge.http;

import com.example.bridge.AgentBridgePlugin;
import com.example.bridge.events.BridgeEvent;
import com.example.bridge.events.BridgeEventHub;
import com.example.bridge.jobs.MiningJob;
import com.example.bridge.util.AgentBridgeConfig;
import com.fasterxml.jackson.core.JsonParseException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.sk89q.worldedit.math.BlockVector3;
import com.sun.net.httpserver.Headers;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpHandler;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import java.util.concurrent.Callable;
import java.util.concurrent.TimeUnit;
import java.util.logging.Level;
import java.util.logging.Logger;
import org.bukkit.Bukkit;

/**
 * HTTP ハンドラの共通基盤。認証・例外ハンドリング・JSON レスポンス出力を集約し、
 * 各ハンドラがリクエスト固有の責務に集中できるようにする。
 */
public abstract class BaseHandler implements HttpHandler {

    private final AgentBridgePlugin plugin;
    private final AgentBridgeConfig config;
    private final ObjectMapper mapper;
    private final Logger logger;
    private final BridgeEventHub eventHub;

    protected BaseHandler(
            AgentBridgePlugin plugin,
            AgentBridgeConfig config,
            ObjectMapper mapper,
            Logger logger,
            BridgeEventHub eventHub) {
        this.plugin = plugin;
        this.config = config;
        this.mapper = mapper;
        this.logger = logger;
        this.eventHub = eventHub;
    }

    protected AgentBridgeConfig config() {
        return config;
    }

    protected ObjectMapper mapper() {
        return mapper;
    }

    protected BridgeEventHub eventHub() {
        return eventHub;
    }

    protected Logger logger() {
        return logger;
    }

    @Override
    public final void handle(HttpExchange exchange) throws IOException {
        try {
            if (requiresAuth() && !authenticate(exchange)) {
                sendJson(exchange, 401, mapper.createObjectNode().put("error", "unauthorized"));
                return;
            }
            handleAuthed(exchange);
        } catch (JsonParseException e) {
            logger.log(Level.WARNING, "Invalid JSON", e);
            sendJson(exchange, 400, mapper.createObjectNode().put("error", "invalid_json"));
        } catch (IllegalArgumentException e) {
            logger.log(Level.WARNING, "Bad request", e);
            sendJson(exchange, 400, mapper.createObjectNode().put("error", e.getMessage()));
        } catch (Exception e) {
            logger.log(Level.SEVERE, "HTTP handler error", e);
            sendJson(exchange, 500, mapper.createObjectNode().put("error", "internal_error"));
        }
    }

    protected boolean requiresAuth() {
        return true;
    }

    protected abstract void handleAuthed(HttpExchange exchange) throws Exception;

    protected boolean authenticate(HttpExchange exchange) {
        if (!config.hasValidApiKey()) {
            logger.warning(() -> "Rejecting request because api_key is not configured; check config.yml");
            return false;
        }
        Headers headers = exchange.getRequestHeaders();
        String provided = Optional.ofNullable(headers.getFirst("X-API-Key")).map(String::trim).orElse("");
        return !provided.isEmpty() && config.apiKey().equals(provided);
    }

    protected void ensureMethod(HttpExchange exchange, String expected) {
        if (!expected.equalsIgnoreCase(exchange.getRequestMethod())) {
            throw new IllegalArgumentException("Invalid method; expected " + expected);
        }
    }

    protected JsonNode parseBody(HttpExchange exchange) throws IOException {
        try (InputStream body = exchange.getRequestBody()) {
            return mapper.readTree(body);
        }
    }

    protected String requiredText(JsonNode node, String field) {
        JsonNode child = node.get(field);
        if (child == null || child.isNull() || child.asText().isEmpty()) {
            throw new IllegalArgumentException(field + " is required");
        }
        return child.asText();
    }

    protected ArrayNode requireArray(JsonNode node, String field) {
        JsonNode child = node.get(field);
        if (child == null || !child.isArray()) {
            throw new IllegalArgumentException(field + " must be an array");
        }
        return (ArrayNode) child;
    }

    protected List<BlockVector3> parsePositions(ArrayNode array) {
        List<BlockVector3> positions = new ArrayList<>();
        for (JsonNode element : array) {
            int x = element.path("x").asInt();
            int y = element.path("y").asInt();
            int z = element.path("z").asInt();
            positions.add(BlockVector3.at(x, y, z));
        }
        return positions;
    }

    protected ObjectNode toPosNode(BlockVector3 pos) {
        ObjectNode node = mapper.createObjectNode();
        node.put("x", pos.getX());
        node.put("y", pos.getY());
        node.put("z", pos.getZ());
        return node;
    }

    protected ObjectNode toFrontierNode(MiningJob.Frontier frontier) {
        ObjectNode node = mapper.createObjectNode();
        node.set("from", toPosNode(frontier.min()));
        node.set("to", toPosNode(frontier.max()));
        return node;
    }

    protected void sendJson(HttpExchange exchange, int status, JsonNode node) throws IOException {
        byte[] body = mapper.writeValueAsBytes(node);
        exchange.getResponseHeaders().add("Content-Type", "application/json; charset=utf-8");
        exchange.sendResponseHeaders(status, body.length);
        try (OutputStream os = exchange.getResponseBody()) {
            os.write(body);
        }
    }

    protected <T> T callSync(Callable<T> task) throws Exception {
        return Bukkit.getScheduler()
                .callSyncMethod(plugin, task)
                .get(config.timeout().httpMillis(), TimeUnit.MILLISECONDS);
    }

    protected String regionName(UUID jobId) {
        return config.frontier().regionNamePrefix() + jobId;
    }

    /**
     * 液体検知時に Mineflayer へ 409 を返却し、安全停止を徹底するための共通レスポンス。
     */
    protected void sendLiquidStop(HttpExchange exchange, MiningJob job) throws IOException {
        sendLiquidStop(exchange, job, job.blockedPosition());
    }

    protected void sendLiquidStop(HttpExchange exchange, MiningJob job, BlockVector3 position) throws IOException {
        ObjectNode response = mapper.createObjectNode();
        response.put("error", "liquid_detected");
        response.put("stop", true);
        response.put("job_id", job.jobId().toString());
        if (position != null) {
            response.set("stop_pos", toPosNode(position));
        }
        sendJson(exchange, 409, response);
    }

    protected void publishEvent(String type, String message, String eventLevel, String region, BlockVector3 blockPos) {
        publishEvent(type, message, eventLevel, region, blockPos, null);
    }

    protected void publishEvent(
            String type, String message, String eventLevel, String region, BlockVector3 blockPos, java.util.Map<String, Object> attrs) {
        eventHub.publish(new BridgeEvent(type, message, eventLevel, region, blockPos, attrs));
    }
}
