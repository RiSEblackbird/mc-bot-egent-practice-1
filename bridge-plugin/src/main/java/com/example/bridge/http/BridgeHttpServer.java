package com.example.bridge.http;

import com.example.bridge.AgentBridgePlugin;
import com.example.bridge.events.BridgeEvent;
import com.example.bridge.events.BridgeEventHub;
import com.example.bridge.jobs.CardinalDirection;
import com.example.bridge.jobs.JobRegistry;
import com.example.bridge.jobs.MiningJob;
import com.example.bridge.langgraph.LangGraphRetryHook;
import com.example.bridge.util.AgentBridgeConfig;
import com.example.bridge.util.CoreProtectFacade;
import com.example.bridge.util.FunctionalBlockInspector;
import com.example.bridge.util.WorldGuardFacade;
import com.fasterxml.jackson.core.JsonParseException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.sun.net.httpserver.Headers;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpHandler;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import java.util.concurrent.Callable;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.logging.Level;
import java.util.logging.Logger;
import org.bukkit.Bukkit;
import org.bukkit.Material;
import org.bukkit.World;
import org.bukkit.block.Block;
import com.sk89q.worldedit.math.BlockVector3;

/**
 * HTTP サーバーを司るコンポーネント。REST 形式の API を Paper プラグインの外に公開し、
 * Python 側のボットが必要とする情報を提供する。認証・JSON パース・エラーハンドリングを
 * 一元化し、ハンドラを最小限の責務へ保つ。
 */
public final class BridgeHttpServer {

    private final AgentBridgePlugin plugin;
    private final AgentBridgeConfig config;
    private final JobRegistry jobRegistry;
    private final WorldGuardFacade worldGuardFacade;
    private final CoreProtectFacade coreProtectFacade;
    private final FunctionalBlockInspector functionalInspector;
    private final LangGraphRetryHook retryHook;
    private final Logger logger;
    private final ObjectMapper mapper;
    private final BridgeEventHub eventHub = new BridgeEventHub();
    private final ExecutorService executor = Executors.newCachedThreadPool();

    private HttpServer server;

    public BridgeHttpServer(
            AgentBridgePlugin plugin,
            AgentBridgeConfig config,
            JobRegistry jobRegistry,
            WorldGuardFacade worldGuardFacade,
            CoreProtectFacade coreProtectFacade,
            FunctionalBlockInspector functionalInspector,
            LangGraphRetryHook retryHook,
            Logger logger) {
        this.plugin = plugin;
        this.config = config;
        this.jobRegistry = jobRegistry;
        this.worldGuardFacade = worldGuardFacade;
        this.coreProtectFacade = coreProtectFacade;
        this.functionalInspector = functionalInspector;
        this.retryHook = retryHook;
        this.logger = logger;
        this.mapper = new ObjectMapper();
        this.mapper.findAndRegisterModules();
        this.mapper.disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS);
    }

    public void start() throws IOException {
        InetSocketAddress address = new InetSocketAddress(config.bindAddress(), config.port());
        server = HttpServer.create(address, 0);
        server.createContext("/v1/health", new HealthHandler());
        server.createContext("/v1/jobs/start_mine", new StartJobHandler());
        server.createContext("/v1/jobs/advance", new AdvanceJobHandler());
        server.createContext("/v1/jobs/stop", new StopJobHandler());
        server.createContext("/v1/blocks/bulk_eval", new BulkEvalHandler());
        server.createContext("/v1/coreprotect/is_player_placed_bulk", new CoreProtectBulkHandler());
        server.createContext("/v1/events/disconnected", new DisconnectionHandler());
        if (config.events().streamEnabled()) {
            server.createContext("/v1/events/stream", new EventStreamHandler());
        }
        server.setExecutor(executor);
        server.start();
        logger.info(() -> "AgentBridge HTTP server started on " + address);
    }

    public void stop() {
        if (server != null) {
            server.stop(0);
            server = null;
        }
        executor.shutdown();
        try {
            executor.awaitTermination(5, TimeUnit.SECONDS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    private abstract class BaseHandler implements HttpHandler {
        @Override
        public final void handle(HttpExchange exchange) throws IOException {
            try (exchange) {
                if (!authenticate(exchange)) {
                    sendJson(exchange, 401, mapper.createObjectNode().put("error", "unauthorized"));
                    return;
                }
                handleAuthed(exchange);
            } catch (JsonParseException e) {
                logger.log(Level.WARNING, "JSON parse error", e);
                sendJson(exchange, 400, mapper.createObjectNode().put("error", "invalid_json"));
            } catch (IllegalArgumentException e) {
                sendJson(exchange, 400, mapper.createObjectNode().put("error", e.getMessage()));
            } catch (Exception e) {
                logger.log(Level.SEVERE, "HTTP handler error", e);
                sendJson(exchange, 500, mapper.createObjectNode().put("error", "internal_error"));
            }
        }

        protected abstract void handleAuthed(HttpExchange exchange) throws Exception;
    }

    private final class HealthHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            ObjectNode node = mapper.createObjectNode();
            node.put("ok", true);
            ObjectNode plugins = node.putObject("plugins");
            plugins.put("worldguard", worldGuardFacade.isAvailable());
            plugins.put("coreprotect", coreProtectFacade.isAvailable());
            sendJson(exchange, 200, node);
        }
    }

    private final class StartJobHandler extends BaseHandler {
        @Override
        protected void handleAuthed(HttpExchange exchange) throws Exception {
            ensureMethod(exchange, "POST");
            JsonNode root = parseBody(exchange);
            String worldName = requiredText(root, "world");
            World world = Bukkit.getWorld(worldName);
            if (world == null) {
                throw new IllegalArgumentException("Unknown world: " + worldName);
            }
            JsonNode anchorNode = root.get("anchor");
            if (anchorNode == null) {
                throw new IllegalArgumentException("anchor is required");
            }
            int ax = anchorNode.path("x").asInt();
            int ay = anchorNode.path("y").asInt();
            int az = anchorNode.path("z").asInt();
            JsonNode dirNode = root.get("dir");
            if (dirNode == null || !dirNode.isArray() || dirNode.size() != 3) {
                throw new IllegalArgumentException("dir must be a 3 element array");
            }
            CardinalDirection direction = CardinalDirection.fromComponents(
                    dirNode.get(0).asInt(), dirNode.get(1).asInt(), dirNode.get(2).asInt());
            JsonNode section = root.get("section");
            if (section == null) {
                throw new IllegalArgumentException("section is required");
            }
            int width = Math.max(section.path("w").asInt(1), 1);
            int height = Math.max(section.path("h").asInt(1), 1);
            int length = Math.max(root.path("length").asInt(1), 1);
            String owner = root.path("owner").asText("");
            UUID jobId = UUID.randomUUID();
            MiningJob job = new MiningJob(
                    jobId,
                    world,
                    BlockVector3.at(ax, ay, az),
                    direction,
                    width,
                    height,
                    length,
                    config.frontier().regionBuffer(),
                    config.frontier().windowLength(),
                    owner);
            if (!worldGuardFacade.isAvailable()) {
                throw new IllegalStateException("WorldGuard is not available");
            }
            jobRegistry.register(job);
            callSync(() -> {
                worldGuardFacade.upsertRegion(job, regionName(jobId));
                return null;
            });
            ObjectNode response = mapper.createObjectNode();
            response.put("job_id", jobId.toString());
            response.set("frontier", toFrontierNode(job.window()));
            sendJson(exchange, 200, response);
            publishEvent(
                    "job_started",
                    "ジョブが登録されました",
                    "info",
                    regionName(jobId),
                    job.window().min());
        }
    }

    private final class AdvanceJobHandler extends BaseHandler {
        @Override
        protected void handleAuthed(HttpExchange exchange) throws Exception {
            ensureMethod(exchange, "POST");
            JsonNode root = parseBody(exchange);
            UUID jobId = UUID.fromString(requiredText(root, "job_id"));
            int steps = Math.max(root.path("steps").asInt(1), 1);
            MiningJob job = jobRegistry.find(jobId).orElseThrow(() -> new IllegalArgumentException("job not found"));
            if (job.isBlocked()) {
                sendLiquidStop(exchange, job);
                return;
            }
            MiningJob.Frontier frontier = callSync(() -> {
                MiningJob.Frontier updated = job.advance(steps);
                worldGuardFacade.updateRegion(job, regionName(jobId));
                return updated;
            });
            ObjectNode response = mapper.createObjectNode();
            response.put("ok", true);
            response.set("frontier", toFrontierNode(frontier));
            response.put("finished", job.isFinished());
            sendJson(exchange, 200, response);
            publishEvent(
                    "job_progress",
                    "ジョブのフロンティアが更新されました",
                    "info",
                    regionName(jobId),
                    frontier.max());
        }
    }

    private final class StopJobHandler extends BaseHandler {
        @Override
        protected void handleAuthed(HttpExchange exchange) throws Exception {
            ensureMethod(exchange, "POST");
            JsonNode root = parseBody(exchange);
            UUID jobId = UUID.fromString(requiredText(root, "job_id"));
            MiningJob job = jobRegistry.find(jobId).orElseThrow(() -> new IllegalArgumentException("job not found"));
            callSync(() -> {
                worldGuardFacade.removeRegion(job.world(), regionName(jobId));
                return null;
            });
            jobRegistry.remove(jobId);
            ObjectNode response = mapper.createObjectNode();
            response.put("ok", true);
            sendJson(exchange, 200, response);
            publishEvent(
                    "job_stopped",
                    "ジョブが停止されました",
                    "info",
                    regionName(jobId),
                    null);
        }
    }

    private final class BulkEvalHandler extends BaseHandler {
        @Override
        protected void handleAuthed(HttpExchange exchange) throws Exception {
            ensureMethod(exchange, "POST");
            JsonNode root = parseBody(exchange);
            String worldName = requiredText(root, "world");
            World world = Bukkit.getWorld(worldName);
            if (world == null) {
                throw new IllegalArgumentException("Unknown world: " + worldName);
            }
            ArrayNode positionsNode = requireArray(root, "positions");
            if (positionsNode.size() > config.safety().maxPositionsPerRequest()) {
                throw new IllegalArgumentException("Too many positions; max=" + config.safety().maxPositionsPerRequest());
            }
            Optional<MiningJob> job = Optional.empty();
            if (root.hasNonNull("job_id")) {
                UUID jobId = UUID.fromString(root.get("job_id").asText());
                job = jobRegistry.find(jobId);
            }
            List<BlockVector3> positions = parsePositions(positionsNode);
            MiningJob.Frontier region = job.map(MiningJob::window).orElse(null);
            String regionName = job.map(value -> regionName(value.jobId())).orElse(null);
            if (job.map(MiningJob::isBlocked).orElse(false)) {
                sendLiquidStop(exchange, job.get());
                return;
            }
            BlockEvaluationResult result =
                    callSync(() -> evaluateBlocks(world, positions, region, regionName));
            if (result.encounteredLiquid() && job.isPresent()) {
                job.get().blockForLiquid(result.firstLiquidPosition());
                sendLiquidStop(exchange, job.get(), result.firstLiquidPosition());
                return;
            }
            ArrayNode response = toEvaluationArray(result.evaluations());
            sendJson(exchange, 200, response);
        }
    }

    private final class CoreProtectBulkHandler extends BaseHandler {
        @Override
        protected void handleAuthed(HttpExchange exchange) throws Exception {
            ensureMethod(exchange, "POST");
            if (!coreProtectFacade.isAvailable()) {
                throw new IllegalStateException("CoreProtect is not available");
            }
            JsonNode root = parseBody(exchange);
            String worldName = requiredText(root, "world");
            World world = Bukkit.getWorld(worldName);
            if (world == null) {
                throw new IllegalArgumentException("Unknown world: " + worldName);
            }
            ArrayNode positionsNode = requireArray(root, "positions");
            if (positionsNode.size() > config.safety().maxPositionsPerRequest()) {
                throw new IllegalArgumentException("Too many positions; max=" + config.safety().maxPositionsPerRequest());
            }
            int lookupSeconds = root.path("lookup_seconds").asInt(config.coreProtect().lookupSeconds());
            List<BlockVector3> positions = parsePositions(positionsNode);
            List<CoreProtectFacade.Result> results = coreProtectFacade.lookupBulk(world, positions, lookupSeconds);
            ArrayNode response = mapper.createArrayNode();
            for (CoreProtectFacade.Result result : results) {
                ObjectNode node = mapper.createObjectNode();
                node.set("pos", toPosNode(result.position()));
                node.put("is_player_placed", result.playerPlaced());
                node.put("who", result.playerName().orElse(null));
                response.add(node);
            }
            sendJson(exchange, 200, response);
        }
    }

    private final class DisconnectionHandler extends BaseHandler {
        @Override
        protected void handleAuthed(HttpExchange exchange) throws Exception {
            ensureMethod(exchange, "POST");
            JsonNode root = parseBody(exchange);
            String nodeId = requiredText(root, "node_id");
            String checkpointId = root.path("checkpoint_id").asText("");
            String reason = root.path("reason").asText("disconnected");
            String eventLevel = root.path("event_level").asText("error");
            logger.info(() -> "LangGraph disconnect detected node=" + nodeId + " checkpoint=" + checkpointId);
            retryHook.triggerRetry(nodeId, checkpointId, reason, eventLevel);
            ObjectNode response = mapper.createObjectNode();
            response.put("ok", true);
            sendJson(exchange, 200, response);
        }
    }

    private final class EventStreamHandler extends BaseHandler {
        @Override
        protected void handleAuthed(HttpExchange exchange) throws Exception {
            ensureMethod(exchange, "GET");
            Headers headers = exchange.getResponseHeaders();
            headers.add("Content-Type", "text/event-stream; charset=utf-8");
            headers.add("Cache-Control", "no-cache");
            headers.add("Connection", "keep-alive");
            exchange.sendResponseHeaders(200, 0);
            BlockingQueue<BridgeEvent> queue = eventHub.subscribe();
            try (OutputStream os = exchange.getResponseBody()) {
                sendSse(os, mapper.createObjectNode().put("message", "event stream started"));
                while (true) {
                    BridgeEvent event = queue.poll(config.events().keepaliveSeconds(), TimeUnit.SECONDS);
                    if (event == null) {
                        sendKeepAlive(os);
                        continue;
                    }
                    sendSse(os, event.toJson(mapper));
                }
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            } catch (IOException e) {
                logger.info("Event stream closed by client");
            } finally {
                eventHub.unsubscribe(queue);
            }
        }

        private void sendKeepAlive(OutputStream os) throws IOException {
            os.write("event: keepalive\n\n".getBytes(StandardCharsets.UTF_8));
            os.flush();
        }

        private void sendSse(OutputStream os, JsonNode payload) throws IOException {
            byte[] body = mapper.writeValueAsBytes(payload);
            os.write("data: ".getBytes(StandardCharsets.UTF_8));
            os.write(body);
            os.write("\n\n".getBytes(StandardCharsets.UTF_8));
            os.flush();
        }
    }

    private boolean authenticate(HttpExchange exchange) {
        if (!config.hasValidApiKey()) {
            logger.warning(() -> "Rejecting request because api_key is not configured; check config.yml");
            return false;
        }
        Headers headers = exchange.getRequestHeaders();
        String provided = Optional.ofNullable(headers.getFirst("X-API-Key")).map(String::trim).orElse("");
        return !provided.isEmpty() && config.apiKey().equals(provided);
    }

    private void ensureMethod(HttpExchange exchange, String expected) {
        if (!expected.equalsIgnoreCase(exchange.getRequestMethod())) {
            throw new IllegalArgumentException("Invalid method; expected " + expected);
        }
    }

    private JsonNode parseBody(HttpExchange exchange) throws IOException {
        try (InputStream body = exchange.getRequestBody()) {
            return mapper.readTree(body);
        }
    }

    private String requiredText(JsonNode node, String field) {
        JsonNode child = node.get(field);
        if (child == null || child.isNull() || child.asText().isEmpty()) {
            throw new IllegalArgumentException(field + " is required");
        }
        return child.asText();
    }

    private ArrayNode requireArray(JsonNode node, String field) {
        JsonNode child = node.get(field);
        if (child == null || !child.isArray()) {
            throw new IllegalArgumentException(field + " must be an array");
        }
        return (ArrayNode) child;
    }

    private List<BlockVector3> parsePositions(ArrayNode array) {
        List<BlockVector3> positions = new ArrayList<>();
        for (JsonNode element : array) {
            int x = element.path("x").asInt();
            int y = element.path("y").asInt();
            int z = element.path("z").asInt();
            positions.add(BlockVector3.at(x, y, z));
        }
        return positions;
    }

    private ObjectNode toPosNode(BlockVector3 pos) {
        ObjectNode node = mapper.createObjectNode();
        node.put("x", pos.getX());
        node.put("y", pos.getY());
        node.put("z", pos.getZ());
        return node;
    }

    private ObjectNode toFrontierNode(MiningJob.Frontier frontier) {
        ObjectNode node = mapper.createObjectNode();
        node.set("from", toPosNode(frontier.min()));
        node.set("to", toPosNode(frontier.max()));
        return node;
    }

    private BlockEvaluationResult evaluateBlocks(
            World world, List<BlockVector3> positions, MiningJob.Frontier region, String regionName) {
        List<BlockEvaluation> list = new ArrayList<>();
        BlockVector3 firstLiquid = null;
        BridgeEvent hazardEvent = null;
        for (BlockVector3 pos : positions) {
            Block block = world.getBlockAt(pos.getBlockX(), pos.getBlockY(), pos.getBlockZ());
            Material type = block.getType();
            boolean isAir = type.isAir();
            boolean isLiquid = type.isLiquid();
            boolean nearFunctional = functionalInspector.isNearFunctional(
                    world, pos.getBlockX(), pos.getBlockY(), pos.getBlockZ(), config.safety().functionalNearRadius());
            boolean inRegion = region != null && contains(region, pos);
            if (isLiquid && inRegion && firstLiquid == null) {
                // 液体検知時は最初の座標を保持し、Mineflayer に安全停止を促す。
                firstLiquid = pos;
                hazardEvent = new BridgeEvent(
                        "danger_detected",
                        "採掘領域内で液体を検知しました",
                        "warning",
                        regionName,
                        pos);
            } else if (hazardEvent == null && nearFunctional) {
                hazardEvent = new BridgeEvent(
                        "danger_detected",
                        "機能ブロックへ近接しました",
                        "warning",
                        regionName,
                        pos);
            }
            String blockId = type.getKey().toString();
            list.add(new BlockEvaluation(pos, blockId, isAir, isLiquid, nearFunctional, inRegion));
        }
        if (hazardEvent != null) {
            eventHub.publish(hazardEvent);
        }
        return new BlockEvaluationResult(list, firstLiquid != null, firstLiquid);
    }

    private ArrayNode toEvaluationArray(List<BlockEvaluation> evaluations) {
        ArrayNode response = mapper.createArrayNode();
        for (BlockEvaluation evaluation : evaluations) {
            ObjectNode node = mapper.createObjectNode();
            node.set("pos", toPosNode(evaluation.position()));
            node.put("block_id", evaluation.blockId());
            node.put("is_air", evaluation.isAir());
            node.put("is_liquid", evaluation.isLiquid());
            node.put("near_functional", evaluation.nearFunctional());
            node.put("in_job_region", evaluation.inJobRegion());
            response.add(node);
        }
        return response;
    }

    private boolean contains(MiningJob.Frontier frontier, BlockVector3 pos) {
        BlockVector3 min = frontier.min();
        BlockVector3 max = frontier.max();
        return pos.getX() >= min.getX()
                && pos.getY() >= min.getY()
                && pos.getZ() >= min.getZ()
                && pos.getX() <= max.getX()
                && pos.getY() <= max.getY()
                && pos.getZ() <= max.getZ();
    }

    /**
     * 液体検知時に Mineflayer へ 409 を返却し、安全停止を徹底するための共通レスポンス。
     */
    private void sendLiquidStop(HttpExchange exchange, MiningJob job) throws IOException {
        sendLiquidStop(exchange, job, job.blockedPosition());
    }

    private void sendLiquidStop(HttpExchange exchange, MiningJob job, BlockVector3 position) throws IOException {
        ObjectNode response = mapper.createObjectNode();
        response.put("error", "liquid_detected");
        response.put("stop", true);
        response.put("job_id", job.jobId().toString());
        if (position != null) {
            response.set("stop_pos", toPosNode(position));
        }
        sendJson(exchange, 409, response);
    }

    private void sendJson(HttpExchange exchange, int status, JsonNode node) throws IOException {
        byte[] body = mapper.writeValueAsBytes(node);
        exchange.getResponseHeaders().add("Content-Type", "application/json; charset=utf-8");
        exchange.sendResponseHeaders(status, body.length);
        try (OutputStream os = exchange.getResponseBody()) {
            os.write(body);
        }
    }

    private <T> T callSync(Callable<T> task) throws Exception {
        return Bukkit.getScheduler()
                .callSyncMethod(plugin, task)
                .get(config.timeout().httpMillis(), TimeUnit.MILLISECONDS);
    }

    private String regionName(UUID jobId) {
        return config.frontier().regionNamePrefix() + jobId;
    }

    private record BlockEvaluation(
            BlockVector3 position,
            String blockId,
            boolean isAir,
            boolean isLiquid,
            boolean nearFunctional,
            boolean inJobRegion) {
    }

    private record BlockEvaluationResult(
            List<BlockEvaluation> evaluations,
            boolean encounteredLiquid,
            BlockVector3 firstLiquidPosition) {
    }

    private void publishEvent(String type, String message, String eventLevel, String region, BlockVector3 blockPos) {
        eventHub.publish(new BridgeEvent(type, message, eventLevel, region, blockPos));
    }
}
