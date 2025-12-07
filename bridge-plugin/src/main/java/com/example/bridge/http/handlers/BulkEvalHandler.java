package com.example.bridge.http.handlers;

import com.example.bridge.AgentBridgePlugin;
import com.example.bridge.events.BridgeEvent;
import com.example.bridge.events.BridgeEventHub;
import com.example.bridge.http.BaseHandler;
import com.example.bridge.jobs.JobRegistry;
import com.example.bridge.jobs.MiningJob;
import com.example.bridge.util.AgentBridgeConfig;
import com.example.bridge.util.FunctionalBlockInspector;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.sk89q.worldedit.math.BlockVector3;
import com.sk89q.worldguard.bukkit.util.Materials;
import com.sun.net.httpserver.HttpExchange;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import java.util.logging.Logger;
import org.bukkit.Bukkit;
import org.bukkit.Material;
import org.bukkit.World;
import org.bukkit.block.Block;

/**
 * ブロック評価エンドポイント。採掘領域の安全確認や機能ブロック接近検知を担う。
 */
public final class BulkEvalHandler extends BaseHandler {

    private final JobRegistry jobRegistry;
    private final FunctionalBlockInspector functionalInspector;

    public BulkEvalHandler(
            AgentBridgePlugin plugin,
            AgentBridgeConfig config,
            ObjectMapper mapper,
            Logger logger,
            BridgeEventHub eventHub,
            JobRegistry jobRegistry,
            FunctionalBlockInspector functionalInspector) {
        super(plugin, config, mapper, logger, eventHub);
        this.jobRegistry = jobRegistry;
        this.functionalInspector = functionalInspector;
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
        ArrayNode positionsNode = requireArray(root, "positions");
        if (positionsNode.size() > config().safety().maxPositionsPerRequest()) {
            throw new IllegalArgumentException("Too many positions; max=" + config().safety().maxPositionsPerRequest());
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
        BlockEvaluationResult result = evaluateBlocks(world, positions, region, regionName);
        if (result.encounteredLiquid() && job.isPresent()) {
            job.get().blockForLiquid(result.firstLiquidPosition());
            sendLiquidStop(exchange, job.get(), result.firstLiquidPosition());
            return;
        }
        ArrayNode response = toEvaluationArray(result.evaluations());
        sendJson(exchange, 200, response);
    }

    private BlockEvaluationResult evaluateBlocks(
            World world, List<BlockVector3> positions, MiningJob.Frontier region, String regionName) {
        List<BlockEvaluation> list = new ArrayList<>();
        BlockVector3 firstLiquid = null;
        BridgeEvent hazardEvent = null;
        int liquidBlocks = 0;
        int functionalTouches = 0;
        int regionMatches = 0;
        for (BlockVector3 pos : positions) {
            Block block = world.getBlockAt(pos.getBlockX(), pos.getBlockY(), pos.getBlockZ());
            Material type = block.getType();
            String blockId = type.getKey().toString();
            boolean isAir = type.isAir();
            boolean isLiquid = Materials.isLiquid(type);
            boolean nearFunctional = functionalInspector.isNearFunctional(
                    world, pos.getBlockX(), pos.getBlockY(), pos.getBlockZ(), config().safety().functionalNearRadius());
            boolean inRegion = region != null && contains(region, pos);
            if (isLiquid) {
                liquidBlocks++;
            }
            if (nearFunctional) {
                functionalTouches++;
            }
            if (inRegion) {
                regionMatches++;
            }
            if (isLiquid && inRegion && firstLiquid == null) {
                firstLiquid = pos;
                Map<String, Object> attributes = new HashMap<>();
                attributes.put("world", world.getName());
                attributes.put("block_id", blockId);
                attributes.put("job_region", regionName);
                attributes.put("hazard", "liquid");
                hazardEvent = new BridgeEvent(
                        "danger_detected",
                        "採掘領域内で液体を検知しました",
                        "warning",
                        regionName,
                        pos,
                        attributes);
            } else if (hazardEvent == null && nearFunctional) {
                Map<String, Object> attributes = new HashMap<>();
                attributes.put("world", world.getName());
                attributes.put("block_id", blockId);
                attributes.put("job_region", regionName);
                attributes.put("hazard", "functional_block");
                hazardEvent = new BridgeEvent(
                        "danger_detected",
                        "機能ブロックへ近接しました",
                        "warning",
                        regionName,
                        pos,
                        attributes);
            }
            list.add(new BlockEvaluation(pos, blockId, isAir, isLiquid, nearFunctional, inRegion));
        }
        if (hazardEvent != null) {
            eventHub().publish(hazardEvent);
        }
        Map<String, Object> scanAttributes = new HashMap<>();
        scanAttributes.put("world", world.getName());
        scanAttributes.put("positions_evaluated", positions.size());
        scanAttributes.put("liquid_blocks", liquidBlocks);
        scanAttributes.put("functional_blocks", functionalTouches);
        scanAttributes.put("region_matches", regionMatches);
        if (regionName != null) {
            scanAttributes.put("job_region", regionName);
        }
        publishEvent(
                "environment_scan",
                "採掘領域の周辺状況をスキャンしました",
                "info",
                regionName,
                null,
                scanAttributes);
        return new BlockEvaluationResult(list, firstLiquid != null, firstLiquid);
    }

    private ArrayNode toEvaluationArray(List<BlockEvaluation> evaluations) {
        ArrayNode response = mapper().createArrayNode();
        for (BlockEvaluation evaluation : evaluations) {
            ObjectNode node = mapper().createObjectNode();
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

    private record BlockEvaluation(
            BlockVector3 position,
            String blockId,
            boolean isAir,
            boolean isLiquid,
            boolean nearFunctional,
            boolean inJobRegion) {}

    private record BlockEvaluationResult(
            List<BlockEvaluation> evaluations,
            boolean encounteredLiquid,
            BlockVector3 firstLiquidPosition) {}
}
