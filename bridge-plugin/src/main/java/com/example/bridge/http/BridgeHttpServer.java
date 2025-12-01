package com.example.bridge.http;

import com.example.bridge.AgentBridgePlugin;
import com.example.bridge.events.BridgeEventHub;
import com.example.bridge.http.handlers.AdvanceJobHandler;
import com.example.bridge.http.handlers.BulkEvalHandler;
import com.example.bridge.http.handlers.CoreProtectBulkHandler;
import com.example.bridge.http.handlers.DisconnectionHandler;
import com.example.bridge.http.handlers.EventStreamHandler;
import com.example.bridge.http.handlers.HealthHandler;
import com.example.bridge.http.handlers.StartJobHandler;
import com.example.bridge.http.handlers.StopJobHandler;
import com.example.bridge.jobs.JobRegistry;
import com.example.bridge.langgraph.LangGraphRetryHook;
import com.example.bridge.util.AgentBridgeConfig;
import com.example.bridge.util.CoreProtectFacade;
import com.example.bridge.util.FunctionalBlockInspector;
import com.example.bridge.util.WorldGuardFacade;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.logging.Logger;

/**
 * HTTP サーバーのライフサイクル管理を担うエントリーポイント。ハンドラは別クラスへ分離し、
 * 本クラスは起動・停止とコンテキスト登録のみを担当する。
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
        registerContexts(server);
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

    private void registerContexts(HttpServer server) {
        server.createContext(
                "/v1/health",
                new HealthHandler(plugin, config, mapper, logger, eventHub, worldGuardFacade, coreProtectFacade));
        server.createContext(
                "/v1/jobs/start_mine",
                new StartJobHandler(plugin, config, mapper, logger, eventHub, jobRegistry, worldGuardFacade));
        server.createContext(
                "/v1/jobs/advance",
                new AdvanceJobHandler(plugin, config, mapper, logger, eventHub, jobRegistry, worldGuardFacade));
        server.createContext(
                "/v1/jobs/stop",
                new StopJobHandler(plugin, config, mapper, logger, eventHub, jobRegistry, worldGuardFacade));
        server.createContext(
                "/v1/blocks/bulk_eval",
                new BulkEvalHandler(plugin, config, mapper, logger, eventHub, jobRegistry, functionalInspector));
        server.createContext(
                "/v1/coreprotect/is_player_placed_bulk",
                new CoreProtectBulkHandler(plugin, config, mapper, logger, eventHub, coreProtectFacade));
        server.createContext(
                "/v1/events/disconnected",
                new DisconnectionHandler(plugin, config, mapper, logger, eventHub, retryHook));
        if (config.events().streamEnabled()) {
            server.createContext(
                    "/v1/events/stream", new EventStreamHandler(plugin, config, mapper, logger, eventHub));
        }
    }
}
