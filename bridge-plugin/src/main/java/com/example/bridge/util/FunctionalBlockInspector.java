package com.example.bridge.util;

import java.util.EnumSet;
import java.util.Set;

import org.bukkit.Material;
import org.bukkit.World;

/**
 * 機能ブロック（チェストやレッドストーン装置など）の近接を検知するユーティリティ。
 * 採掘ジョブの安全停止条件として利用するため、検知範囲やブロック集合を統一管理する。
 */
public final class FunctionalBlockInspector {

    private final Set<Material> functionalBlocks = EnumSet.noneOf(Material.class);

    public FunctionalBlockInspector() {
        // 運用で特に保護すべきブロックを列挙する。頻繁に更新されるため、重複を避ける目的で
        // EnumSet を使用し、読みやすいまとまりごとに add する。
        addStorageBlocks();
        addRedstoneBlocks();
        addUtilityBlocks();
        addDoorsAndRails();
        addDecorations();
    }

    private void addStorageBlocks() {
        functionalBlocks.add(Material.CHEST);
        functionalBlocks.add(Material.TRAPPED_CHEST);
        functionalBlocks.add(Material.BARREL);
        functionalBlocks.add(Material.ENDER_CHEST);
        functionalBlocks.add(Material.SHULKER_BOX);
    }

    private void addRedstoneBlocks() {
        functionalBlocks.add(Material.REDSTONE_WIRE);
        functionalBlocks.add(Material.REPEATER);
        functionalBlocks.add(Material.COMPARATOR);
        functionalBlocks.add(Material.LEVER);
        functionalBlocks.add(Material.STONE_BUTTON);
        functionalBlocks.add(Material.POLISHED_BLACKSTONE_BUTTON);
        functionalBlocks.add(Material.OAK_PRESSURE_PLATE);
        functionalBlocks.add(Material.SPRUCE_PRESSURE_PLATE);
        functionalBlocks.add(Material.STONE_PRESSURE_PLATE);
        functionalBlocks.add(Material.LIGHT_WEIGHTED_PRESSURE_PLATE);
        functionalBlocks.add(Material.HEAVY_WEIGHTED_PRESSURE_PLATE);
        functionalBlocks.add(Material.REDSTONE_TORCH);
        functionalBlocks.add(Material.REDSTONE_BLOCK);
        functionalBlocks.add(Material.PISTON);
        functionalBlocks.add(Material.STICKY_PISTON);
        functionalBlocks.add(Material.DISPENSER);
        functionalBlocks.add(Material.DROPPER);
        functionalBlocks.add(Material.OBSERVER);
    }

    private void addUtilityBlocks() {
        functionalBlocks.add(Material.FURNACE);
        functionalBlocks.add(Material.BLAST_FURNACE);
        functionalBlocks.add(Material.SMOKER);
        functionalBlocks.add(Material.ANVIL);
        functionalBlocks.add(Material.CHIPPED_ANVIL);
        functionalBlocks.add(Material.DAMAGED_ANVIL);
        functionalBlocks.add(Material.CRAFTING_TABLE);
        functionalBlocks.add(Material.GRINDSTONE);
        functionalBlocks.add(Material.ENCHANTING_TABLE);
        functionalBlocks.add(Material.BREWING_STAND);
        functionalBlocks.add(Material.BEACON);
        functionalBlocks.add(Material.CAULDRON);
        functionalBlocks.add(Material.LECTERN);
        functionalBlocks.add(Material.CARTOGRAPHY_TABLE);
        functionalBlocks.add(Material.FLETCHING_TABLE);
        functionalBlocks.add(Material.SMITHING_TABLE);
        functionalBlocks.add(Material.STONECUTTER);
        functionalBlocks.add(Material.LOOM);
        functionalBlocks.add(Material.BELL);
        functionalBlocks.add(Material.BEDROCK); // 人工物との衝突防止用途
    }

    private void addDoorsAndRails() {
        functionalBlocks.add(Material.OAK_DOOR);
        functionalBlocks.add(Material.SPRUCE_DOOR);
        functionalBlocks.add(Material.BIRCH_DOOR);
        functionalBlocks.add(Material.JUNGLE_DOOR);
        functionalBlocks.add(Material.ACACIA_DOOR);
        functionalBlocks.add(Material.DARK_OAK_DOOR);
        functionalBlocks.add(Material.MANGROVE_DOOR);
        functionalBlocks.add(Material.BAMBOO_DOOR);
        functionalBlocks.add(Material.IRON_DOOR);
        functionalBlocks.add(Material.OAK_TRAPDOOR);
        functionalBlocks.add(Material.SPRUCE_TRAPDOOR);
        functionalBlocks.add(Material.BIRCH_TRAPDOOR);
        functionalBlocks.add(Material.JUNGLE_TRAPDOOR);
        functionalBlocks.add(Material.ACACIA_TRAPDOOR);
        functionalBlocks.add(Material.DARK_OAK_TRAPDOOR);
        functionalBlocks.add(Material.IRON_TRAPDOOR);
        functionalBlocks.add(Material.RAIL);
        functionalBlocks.add(Material.POWERED_RAIL);
        functionalBlocks.add(Material.DETECTOR_RAIL);
        functionalBlocks.add(Material.ACTIVATOR_RAIL);
    }

    private void addDecorations() {
        functionalBlocks.add(Material.ITEM_FRAME);
        functionalBlocks.add(Material.GLOW_ITEM_FRAME);
        functionalBlocks.add(Material.PAINTING);
        functionalBlocks.add(Material.OAK_SIGN);
        functionalBlocks.add(Material.SPRUCE_SIGN);
        functionalBlocks.add(Material.BIRCH_SIGN);
        functionalBlocks.add(Material.JUNGLE_SIGN);
        functionalBlocks.add(Material.ACACIA_SIGN);
        functionalBlocks.add(Material.DARK_OAK_SIGN);
        functionalBlocks.add(Material.MANGROVE_SIGN);
        functionalBlocks.add(Material.BAMBOO_SIGN);
        functionalBlocks.add(Material.WARPED_SIGN);
        functionalBlocks.add(Material.CRIMSON_SIGN);
        functionalBlocks.add(Material.OAK_HANGING_SIGN);
        functionalBlocks.add(Material.SPRUCE_HANGING_SIGN);
        functionalBlocks.add(Material.BIRCH_HANGING_SIGN);
        functionalBlocks.add(Material.JUNGLE_HANGING_SIGN);
        functionalBlocks.add(Material.ACACIA_HANGING_SIGN);
        functionalBlocks.add(Material.DARK_OAK_HANGING_SIGN);
        functionalBlocks.add(Material.MANGROVE_HANGING_SIGN);
        functionalBlocks.add(Material.BAMBOO_HANGING_SIGN);
        functionalBlocks.add(Material.CRIMSON_HANGING_SIGN);
        functionalBlocks.add(Material.WARPED_HANGING_SIGN);
        functionalBlocks.add(Material.LADDER);
    }

    public boolean isFunctional(Material material) {
        return functionalBlocks.contains(material);
    }

    /**
     * 指定座標の周囲に機能ブロックが存在するかを走査する。
     * 断面評価のたびに呼ばれるため、ループを単純化しつつ早期 return でコストを抑える。
     */
    public boolean isNearFunctional(World world, int x, int y, int z, int radius) {
        if (radius <= 0) {
            return false;
        }
        for (int dx = -radius; dx <= radius; dx++) {
            for (int dy = -radius; dy <= radius; dy++) {
                for (int dz = -radius; dz <= radius; dz++) {
                    if (functionalBlocks.contains(world.getBlockAt(x + dx, y + dy, z + dz).getType())) {
                        return true;
                    }
                }
            }
        }
        return false;
    }
}
