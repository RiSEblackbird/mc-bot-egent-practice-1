package com.example.bridge.http.handlers;

import com.example.bridge.AgentBridgePlugin;
import com.example.bridge.events.BridgeEvent;
import com.example.bridge.events.BridgeEventHub;
import com.example.bridge.http.BaseHandler;
import com.example.bridge.util.AgentBridgeConfig;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.Headers;
import com.sun.net.httpserver.HttpExchange;
import java.io.IOException;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.logging.Logger;

/**
 * Server-Sent Events を通じて BridgeEvent を配信するハンドラ。
 */
public final class EventStreamHandler extends BaseHandler {

    public EventStreamHandler(
            AgentBridgePlugin plugin,
            AgentBridgeConfig config,
            ObjectMapper mapper,
            Logger logger,
            BridgeEventHub eventHub) {
        super(plugin, config, mapper, logger, eventHub);
    }

    @Override
    protected void handleAuthed(HttpExchange exchange) throws Exception {
        ensureMethod(exchange, "GET");
        Headers headers = exchange.getResponseHeaders();
        headers.add("Content-Type", "text/event-stream; charset=utf-8");
        headers.add("Cache-Control", "no-cache");
        headers.add("Connection", "keep-alive");
        exchange.sendResponseHeaders(200, 0);
        BlockingQueue<BridgeEvent> queue = eventHub().subscribe();
        try (OutputStream os = exchange.getResponseBody()) {
            sendSse(os, mapper().createObjectNode().put("message", "event stream started"));
            while (true) {
                BridgeEvent event = queue.poll(config().events().keepaliveSeconds(), TimeUnit.SECONDS);
                if (event == null) {
                    sendKeepAlive(os);
                    continue;
                }
                sendSse(os, event.toJson(mapper()));
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        } catch (IOException e) {
            logger().info("Event stream closed by client");
        } finally {
            eventHub().unsubscribe(queue);
        }
    }

    @Override
    protected boolean requiresAuth() {
        return true;
    }

    private void sendKeepAlive(OutputStream os) throws IOException {
        os.write("event: keepalive\n\n".getBytes(StandardCharsets.UTF_8));
        os.flush();
    }

    private void sendSse(OutputStream os, JsonNode payload) throws IOException {
        byte[] body = mapper().writeValueAsBytes(payload);
        os.write("data: ".getBytes(StandardCharsets.UTF_8));
        os.write(body);
        os.write("\n\n".getBytes(StandardCharsets.UTF_8));
        os.flush();
    }
}
