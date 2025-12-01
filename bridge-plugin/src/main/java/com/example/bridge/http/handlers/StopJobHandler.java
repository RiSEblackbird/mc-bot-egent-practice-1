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
import com.sun.net.httpserver.HttpExchange;
import java.util.HashMap;
import java.util.Map;
import java.util.UUID;
import java.util.logging.Logger;

/**
 * 採掘ジョブの停止要求を処理し、領域削除とレジストリからの除外を行うハンドラ。
 */
public final class StopJobHandler extends BaseHandler {

    private final JobRegistry jobRegistry;
    private final WorldGuardFacade worldGuardFacade;

    public StopJobHandler(
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
        MiningJob job = jobRegistry.find(jobId).orElseThrow(() -> new IllegalArgumentException("job not found"));
        callSync(() -> {
            worldGuardFacade.removeRegion(job.world(), regionName(jobId));
            return null;
        });
        jobRegistry.remove(jobId);
        ObjectNode response = mapper().createObjectNode();
        response.put("ok", true);
        sendJson(exchange, 200, response);
        Map<String, Object> attributes = new HashMap<>();
        attributes.put("job_id", jobId.toString());
        attributes.put("world", job.world().getName());
        publishEvent(
                "job_stopped",
                "ジョブが停止されました",
                "info",
                regionName(jobId),
                null,
                attributes);
    }
}
