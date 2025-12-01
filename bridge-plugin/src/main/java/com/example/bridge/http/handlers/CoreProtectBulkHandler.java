package com.example.bridge.http.handlers;

import com.example.bridge.AgentBridgePlugin;
import com.example.bridge.events.BridgeEventHub;
import com.example.bridge.http.BaseHandler;
import com.example.bridge.util.AgentBridgeConfig;
import com.example.bridge.util.CoreProtectFacade;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.sk89q.worldedit.math.BlockVector3;
import com.sun.net.httpserver.HttpExchange;
import java.util.List;
import java.util.logging.Logger;
import org.bukkit.Bukkit;
import org.bukkit.World;

/**
 * CoreProtect 連携による一括履歴照会を担当するハンドラ。
 */
public final class CoreProtectBulkHandler extends BaseHandler {

    private final CoreProtectFacade coreProtectFacade;

    public CoreProtectBulkHandler(
            AgentBridgePlugin plugin,
            AgentBridgeConfig config,
            ObjectMapper mapper,
            Logger logger,
            BridgeEventHub eventHub,
            CoreProtectFacade coreProtectFacade) {
        super(plugin, config, mapper, logger, eventHub);
        this.coreProtectFacade = coreProtectFacade;
    }

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
        if (positionsNode.size() > config().safety().maxPositionsPerRequest()) {
            throw new IllegalArgumentException("Too many positions; max=" + config().safety().maxPositionsPerRequest());
        }
        int lookupSeconds = root.path("lookup_seconds").asInt(config().coreProtect().lookupSeconds());
        List<BlockVector3> positions = parsePositions(positionsNode);
        List<CoreProtectFacade.Result> results = coreProtectFacade.lookupBulk(world, positions, lookupSeconds);
        ArrayNode response = mapper().createArrayNode();
        for (CoreProtectFacade.Result result : results) {
            ObjectNode node = mapper().createObjectNode();
            node.set("pos", toPosNode(result.position()));
            node.put("is_player_placed", result.playerPlaced());
            node.put("who", result.playerName().orElse(null));
            response.add(node);
        }
        sendJson(exchange, 200, response);
    }
}
