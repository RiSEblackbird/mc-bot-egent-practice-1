package com.example.bridge.http.handlers;

import com.example.bridge.AgentBridgePlugin;
import com.example.bridge.events.BridgeEventHub;
import com.example.bridge.http.BaseHandler;
import com.example.bridge.jobs.CardinalDirection;
import com.example.bridge.jobs.JobRegistry;
import com.example.bridge.jobs.MiningJob;
import com.example.bridge.util.AgentBridgeConfig;
import com.example.bridge.util.WorldGuardFacade;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.sk89q.worldedit.math.BlockVector3;
import com.sun.net.httpserver.HttpExchange;
import java.util.HashMap;
import java.util.Map;
import java.util.UUID;
import java.util.logging.Logger;
import org.bukkit.Bukkit;
import org.bukkit.World;

/**
 * 採掘ジョブの開始を受け付けるハンドラ。入力値検証と WorldGuard での領域確保をまとめる。
 */
public final class StartJobHandler extends BaseHandler {

    private final JobRegistry jobRegistry;
    private final WorldGuardFacade worldGuardFacade;

    public StartJobHandler(
            AgentBridgePlugin plugin,
            AgentBridgeConfig config,
            ObjectMapper mapper,
            Logger logger,
            BridgeEventHub eventHub,
            JobRegistry jobRegistry,
            WorldGuardFacade worldGuardFacade) {
        super(plugin, config, mapper, logger, eventHub);
        this.jobRegistry = jobRegistry;
        this.worldGuardFacade = worldGuardFacade;
    }

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
                config().frontier().regionBuffer(),
                config().frontier().windowLength(),
                owner);
        if (!worldGuardFacade.isAvailable()) {
            throw new IllegalStateException("WorldGuard is not available");
        }
        jobRegistry.register(job);
        callSync(() -> {
            worldGuardFacade.upsertRegion(job, regionName(jobId));
            return null;
        });
        ObjectNode response = mapper().createObjectNode();
        response.put("job_id", jobId.toString());
        response.set("frontier", toFrontierNode(job.window()));
        sendJson(exchange, 200, response);
        Map<String, Object> attributes = new HashMap<>();
        attributes.put("job_id", jobId.toString());
        attributes.put("world", worldName);
        attributes.put("owner", owner);
        attributes.put("length", length);
        attributes.put("section_width", width);
        attributes.put("section_height", height);
        attributes.put("direction", direction.name());
        publishEvent(
                "job_started",
                "ジョブが登録されました",
                "info",
                regionName(jobId),
                job.window().min(),
                attributes);
    }
}
