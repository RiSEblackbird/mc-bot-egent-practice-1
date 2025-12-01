package com.example.bridge.http.handlers;

import com.example.bridge.AgentBridgePlugin;
import com.example.bridge.events.BridgeEventHub;
import com.example.bridge.http.BaseHandler;
import com.example.bridge.util.AgentBridgeConfig;
import com.example.bridge.util.CoreProtectFacade;
import com.example.bridge.util.WorldGuardFacade;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.sun.net.httpserver.HttpExchange;
import java.io.IOException;
import java.util.logging.Logger;

/**
 * サーバーヘルスを返却するエンドポイント。認証不要とし、依存プラグインの有効状態のみを返す。
 */
public final class HealthHandler extends BaseHandler {

    private final WorldGuardFacade worldGuardFacade;
    private final CoreProtectFacade coreProtectFacade;

    public HealthHandler(
            AgentBridgePlugin plugin,
            AgentBridgeConfig config,
            ObjectMapper mapper,
            Logger logger,
            BridgeEventHub eventHub,
            WorldGuardFacade worldGuardFacade,
            CoreProtectFacade coreProtectFacade) {
        super(plugin, config, mapper, logger, eventHub);
        this.worldGuardFacade = worldGuardFacade;
        this.coreProtectFacade = coreProtectFacade;
    }

    @Override
    protected boolean requiresAuth() {
        return false;
    }

    @Override
    protected void handleAuthed(HttpExchange exchange) throws IOException {
        ObjectNode node = mapper().createObjectNode();
        node.put("ok", true);
        ObjectNode plugins = node.putObject("plugins");
        plugins.put("worldguard", worldGuardFacade.isAvailable());
        plugins.put("coreprotect", coreProtectFacade.isAvailable());
        sendJson(exchange, 200, node);
    }
}
