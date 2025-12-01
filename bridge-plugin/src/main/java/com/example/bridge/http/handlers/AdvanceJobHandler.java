package com.example.bridge.http.handlers;

import com.example.bridge.AgentBridgePlugin;
import com.example.bridge.events.BridgeEventHub;
import com.example.bridge.http.BaseHandler;
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

/**
 * 進捗更新リクエストを処理し、フロンティア計算と領域更新を同期実行するハンドラ。
 */
public final class AdvanceJobHandler extends BaseHandler {

    private final JobRegistry jobRegistry;
    private final WorldGuardFacade worldGuardFacade;

    public AdvanceJobHandler(
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
        ObjectNode response = mapper().createObjectNode();
        response.put("ok", true);
        response.set("frontier", toFrontierNode(frontier));
        response.put("finished", job.isFinished());
        sendJson(exchange, 200, response);
        Map<String, Object> attributes = new HashMap<>();
        attributes.put("job_id", jobId.toString());
        attributes.put("world", job.world().getName());
        attributes.put("steps", steps);
        attributes.put("finished", job.isFinished());
        publishEvent(
                "job_progress",
                "ジョブのフロンティアが更新されました",
                "info",
                regionName(jobId),
                frontier.max(),
                attributes);
    }
}
