package com.example.bridge.util;

import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.List;
import java.util.Objects;
import java.util.Optional;
import java.util.logging.Logger;

import org.bukkit.Bukkit;
import org.bukkit.World;
import org.bukkit.block.Block;
import org.bukkit.plugin.Plugin;

import com.sk89q.worldedit.math.BlockVector3;

/**
 * CoreProtect の API をリフレクションで呼び出すための薄いラッパー。
 * CoreProtect の jar をビルド時に同梱できないケースを想定し、実行時にメソッドを動的解決する。
 */
public final class CoreProtectFacade {

    private final Logger logger;
    private Object api;
    private Integer placeActionId;

    public CoreProtectFacade(Logger logger) {
        this.logger = Objects.requireNonNull(logger, "logger");
    }

    public boolean isAvailable() {
        ensureApi();
        return api != null;
    }

    public List<Result> lookupBulk(World world, List<BlockVector3> positions, int seconds) {
        ensureApi();
        List<Result> results = new ArrayList<>();
        if (api == null) {
            return results;
        }
        for (BlockVector3 pos : positions) {
            Block block = world.getBlockAt(pos.getBlockX(), pos.getBlockY(), pos.getBlockZ());
            boolean playerPlaced = isPlayerPlaced(block, seconds);
            results.add(new Result(pos, playerPlaced, Optional.empty()));
        }
        return results;
    }

    private boolean isPlayerPlaced(Block block, int seconds) {
        ensureApi();
        if (api == null) {
            return false;
        }
        try {
            Method blockLookup = api.getClass().getMethod("blockLookup", Block.class, int.class);
            @SuppressWarnings("unchecked")
            List<String[]> rows = (List<String[]>) blockLookup.invoke(api, block, seconds);
            if (rows == null) {
                return false;
            }
            Method parseResult = api.getClass().getMethod("parseResult", String[].class);
            Method getActionId = null;
            Method getPlayer = null;
            for (String[] row : rows) {
                Object parsed = parseResult.invoke(api, (Object) row);
                if (parsed == null) {
                    continue;
                }
                if (getActionId == null) {
                    getActionId = parsed.getClass().getMethod("getActionId");
                    getPlayer = parsed.getClass().getMethod("getPlayer");
                }
                int actionId = (Integer) getActionId.invoke(parsed);
                if (isPlaceAction(actionId) && getPlayer != null) {
                    Object who = getPlayer.invoke(parsed);
                    if (who != null) {
                        return true;
                    }
                }
            }
        } catch (NoSuchMethodException | IllegalAccessException | InvocationTargetException e) {
            logger.warning("CoreProtect lookup failed: " + e.getMessage());
        }
        return false;
    }

    private boolean isPlaceAction(int actionId) {
        if (placeActionId == null) {
            resolvePlaceActionId();
        }
        return placeActionId != null ? actionId == placeActionId : actionId == 1;
    }

    private void resolvePlaceActionId() {
        if (api == null) {
            return;
        }
        for (Class<?> inner : api.getClass().getDeclaredClasses()) {
            if (inner.isEnum() && inner.getSimpleName().equals("Action")) {
                Object[] constants = inner.getEnumConstants();
                for (Object constant : constants) {
                    Enum<?> enumConstant = (Enum<?>) constant;
                    if ("PLACE".equalsIgnoreCase(enumConstant.name())) {
                        placeActionId = enumConstant.ordinal();
                        return;
                    }
                }
            }
        }
    }

    private void ensureApi() {
        if (api != null) {
            return;
        }
        Plugin plugin = Bukkit.getPluginManager().getPlugin("CoreProtect");
        if (plugin == null || !plugin.isEnabled()) {
            return;
        }
        try {
            Method getAPI = plugin.getClass().getMethod("getAPI");
            api = getAPI.invoke(plugin);
            if (api != null) {
                Method isEnabled = api.getClass().getMethod("isEnabled");
                boolean enabled = (Boolean) isEnabled.invoke(api);
                if (!enabled) {
                    api = null;
                }
            }
        } catch (NoSuchMethodException | IllegalAccessException | InvocationTargetException e) {
            logger.warning("Failed to initialize CoreProtect API: " + e.getMessage());
            api = null;
        }
    }

    /** CoreProtect から返却される判定結果のシリアライズ用データ構造。 */
    public record Result(BlockVector3 position, boolean playerPlaced, Optional<String> playerName) {}
}
