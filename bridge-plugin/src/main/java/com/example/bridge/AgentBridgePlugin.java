package com.example.bridge;

import com.example.bridge.http.BridgeHttpServer;
import com.example.bridge.jobs.JobRegistry;
import com.example.bridge.langgraph.LangGraphRetryClient;
import com.example.bridge.langgraph.LangGraphRetryHook;
import com.example.bridge.util.AgentBridgeConfig;
import com.example.bridge.util.CoreProtectFacade;
import com.example.bridge.util.FunctionalBlockInspector;
import com.example.bridge.util.WorldGuardFacade;
import java.io.IOException;
import java.util.logging.Level;
import org.bukkit.command.Command;
import org.bukkit.command.CommandSender;
import org.bukkit.plugin.java.JavaPlugin;

/**
 * AgentBridge プラグインのエントリポイント。Paper のライフサイクルと HTTP サーバー管理を担う。
 */
public final class AgentBridgePlugin extends JavaPlugin {

    private AgentBridgeConfig bridgeConfig;
    private BridgeHttpServer httpServer;
    private JobRegistry jobRegistry;

    @Override
    public void onEnable() {
        saveDefaultConfig();
        reloadBridgeConfig();
        if (getCommand("agentbridge") != null) {
            getCommand("agentbridge").setExecutor(this);
        }
    }

    @Override
    public void onDisable() {
        if (httpServer != null) {
            httpServer.stop();
        }
        if (jobRegistry != null) {
            jobRegistry.clear();
        }
    }

    @Override
    public boolean onCommand(CommandSender sender, Command command, String label, String[] args) {
        if (args.length == 1 && args[0].equalsIgnoreCase("reload")) {
            reloadBridgeConfig();
            sender.sendMessage("AgentBridge configuration reloaded.");
            return true;
        }
        sender.sendMessage("Usage: /agentbridge reload");
        return true;
    }

    private void reloadBridgeConfig() {
        reloadConfig();
        bridgeConfig = AgentBridgeConfig.from(getConfig());
        jobRegistry = new JobRegistry();
        WorldGuardFacade worldGuardFacade = new WorldGuardFacade(getLogger());
        CoreProtectFacade coreProtectFacade = new CoreProtectFacade(getLogger());
        FunctionalBlockInspector inspector = new FunctionalBlockInspector();
        LangGraphRetryHook retryHook = LangGraphRetryHook.noop(getLogger());
        AgentBridgeConfig.LangGraphConfig langGraph = bridgeConfig.langGraph();
        if (langGraph.enabled()) {
            retryHook = new LangGraphRetryClient(
                    langGraph.retryEndpoint(),
                    langGraph.apiKey(),
                    langGraph.timeoutMillis(),
                    getLogger());
        }
        if (httpServer != null) {
            httpServer.stop();
        }
        httpServer = new BridgeHttpServer(this, bridgeConfig, jobRegistry, worldGuardFacade, coreProtectFacade, inspector,
                retryHook, getLogger());
        try {
            httpServer.start();
        } catch (IOException e) {
            getLogger().log(Level.SEVERE, "Failed to start HTTP server", e);
        }
    }
}
