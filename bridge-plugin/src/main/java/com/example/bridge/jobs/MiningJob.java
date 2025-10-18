package com.example.bridge.jobs;

import java.util.ArrayList;
import java.util.List;
import java.util.UUID;

import org.bukkit.World;
import org.bukkit.util.Vector;

import com.sk89q.worldedit.math.BlockVector3;

/**
 * 継続採掘モードのジョブを表現するドメインモデル。リージョン情報や進捗を保持し、
 * フロンティアの前進に合わせて WorldGuard の保護領域を計算する責務を担う。
 */
public final class MiningJob {

    private final UUID jobId;
    private final World world;
    private final BlockVector3 anchor;
    private final CardinalDirection direction;
    private final int width;
    private final int height;
    private final int length;
    private final int regionBuffer;
    private final int windowLength;
    private final String owner;

    private int progress; // 何ブロック前進したか

    public MiningJob(
            UUID jobId,
            World world,
            BlockVector3 anchor,
            CardinalDirection direction,
            int width,
            int height,
            int length,
            int regionBuffer,
            int windowLength,
            String owner) {
        this.jobId = jobId;
        this.world = world;
        this.anchor = anchor;
        this.direction = direction;
        this.width = width;
        this.height = height;
        this.length = length;
        this.regionBuffer = Math.max(regionBuffer, 0);
        this.windowLength = Math.max(windowLength, 1);
        this.owner = owner;
        this.progress = 0;
    }

    public UUID jobId() {
        return jobId;
    }

    public World world() {
        return world;
    }

    public BlockVector3 anchor() {
        return anchor;
    }

    public CardinalDirection direction() {
        return direction;
    }

    public int width() {
        return width;
    }

    public int height() {
        return height;
    }

    public int length() {
        return length;
    }

    public int progress() {
        return progress;
    }

    public String owner() {
        return owner;
    }

    public boolean isFinished() {
        return progress >= length;
    }

    public int windowLength() {
        return windowLength;
    }

    /**
     * 現在の進捗をもとに保護すべきリージョン範囲を算出する。
     */
    public Frontier window() {
        int fromStep = progress;
        int toStep = Math.min(progress + windowLength, length);
        return computeFrontier(fromStep, toStep);
    }

    /**
     * フロンティアを steps ブロック分進め、更新後のリージョン範囲を返す。
     */
    public Frontier advance(int steps) {
        if (steps <= 0) {
            return window();
        }
        progress = Math.min(progress + steps, length);
        return window();
    }

    /**
     * 指定範囲の断面座標を列挙する。API レスポンスに利用するため、BlockVector3 のリストで返す。
     */
    public List<BlockVector3> sectionPositions(int fromStep, int toStep) {
        List<BlockVector3> positions = new ArrayList<>();
        Vector lateral = direction.lateralVector();
        for (int step = fromStep; step < toStep; step++) {
            int baseX = anchor.getBlockX() + direction.dx() * step;
            int baseY = anchor.getBlockY();
            int baseZ = anchor.getBlockZ() + direction.dz() * step;
            for (int w = 0; w < width; w++) {
                int offsetX = (int) (lateral.getX() * w);
                int offsetY = (int) (lateral.getY() * w);
                int offsetZ = (int) (lateral.getZ() * w);
                for (int h = 0; h < height; h++) {
                    positions.add(BlockVector3.at(baseX + offsetX, baseY + h + offsetY, baseZ + offsetZ));
                }
            }
        }
        return positions;
    }

    /**
     * 現在のウィンドウに対応する WorldGuard 用の保護範囲を算出する。
     */
    private Frontier computeFrontier(int fromStep, int toStep) {
        if (toStep <= fromStep) {
            BlockVector3 single = anchor;
            return new Frontier(single, single);
        }
        List<BlockVector3> positions = sectionPositions(fromStep, toStep);
        int minX = Integer.MAX_VALUE;
        int minY = Integer.MAX_VALUE;
        int minZ = Integer.MAX_VALUE;
        int maxX = Integer.MIN_VALUE;
        int maxY = Integer.MIN_VALUE;
        int maxZ = Integer.MIN_VALUE;
        for (BlockVector3 pos : positions) {
            minX = Math.min(minX, pos.getX());
            minY = Math.min(minY, pos.getY());
            minZ = Math.min(minZ, pos.getZ());
            maxX = Math.max(maxX, pos.getX());
            maxY = Math.max(maxY, pos.getY());
            maxZ = Math.max(maxZ, pos.getZ());
        }
        if (regionBuffer > 0) {
            minX -= regionBuffer;
            minY -= regionBuffer;
            minZ -= regionBuffer;
            maxX += regionBuffer;
            maxY += regionBuffer;
            maxZ += regionBuffer;
        }
        return new Frontier(BlockVector3.at(minX, minY, minZ), BlockVector3.at(maxX, maxY, maxZ));
    }

    /**
     * JSON レスポンスで利用する座標範囲を保持する不変オブジェクト。
     */
    public record Frontier(BlockVector3 min, BlockVector3 max) {}
}
