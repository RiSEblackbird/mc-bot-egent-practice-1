package com.example.bridge.util;

import java.util.Objects;
import java.util.logging.Logger;

import org.bukkit.World;

import com.example.bridge.jobs.MiningJob;
import com.sk89q.worldedit.bukkit.BukkitAdapter;
import com.sk89q.worldedit.math.BlockVector3;
import com.sk89q.worldguard.WorldGuard;
import com.sk89q.worldguard.protection.managers.RegionManager;
import com.sk89q.worldguard.protection.regions.ProtectedCuboidRegion;
import com.sk89q.worldguard.protection.regions.ProtectedRegion;

/**
 * WorldGuard のリージョン管理操作をカプセル化するヘルパークラス。
 * WorldGuard が有効でない場合は例外を投げ、API 呼び出し側がエラー応答を返せるようにする。
 */
public final class WorldGuardFacade {

    private final Logger logger;

    public WorldGuardFacade(Logger logger) {
        this.logger = Objects.requireNonNull(logger, "logger");
    }

    public boolean isAvailable() {
        return WorldGuard.getInstance() != null;
    }

    public void upsertRegion(MiningJob job, String regionName) throws Exception {
        MiningJob.Frontier frontier = job.window();
        RegionManager manager = requireManager(job.world());
        ProtectedRegion region = new ProtectedCuboidRegion(regionName, frontier.min(), frontier.max());
        region.setPriority(100);
        manager.addRegion(region);
        manager.save();
        logger.info(() -> "WorldGuard region synchronized for job " + job.jobId());
    }

    public void updateRegion(MiningJob job, String regionName) throws Exception {
        MiningJob.Frontier frontier = job.window();
        RegionManager manager = requireManager(job.world());
        ProtectedRegion region = manager.getRegion(regionName);
        if (region == null) {
            upsertRegion(job, regionName);
            return;
        }
        if (region instanceof ProtectedCuboidRegion cuboid) {
            cuboid.setMaximum(frontier.max());
            cuboid.setMinimum(frontier.min());
        } else {
            manager.removeRegion(regionName);
            manager.addRegion(new ProtectedCuboidRegion(regionName, frontier.min(), frontier.max()));
        }
        manager.save();
    }

    public void removeRegion(World world, String regionName) throws Exception {
        RegionManager manager = requireManager(world);
        if (manager.hasRegion(regionName)) {
            manager.removeRegion(regionName);
            manager.save();
            logger.info(() -> "WorldGuard region removed: " + regionName);
        }
    }

    private RegionManager requireManager(World world) throws Exception {
        RegionManager manager = WorldGuard.getInstance()
                .getPlatform()
                .getRegionContainer()
                .get(BukkitAdapter.adapt(world));
        if (manager == null) {
            throw new IllegalStateException("Failed to acquire WorldGuard region manager for world " + world.getName());
        }
        return manager;
    }
}
