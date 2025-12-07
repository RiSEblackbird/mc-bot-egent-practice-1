package com.example.bridge.util;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import com.sk89q.worldedit.math.BlockVector3;
import java.lang.reflect.Field;
import java.util.List;
import java.util.Optional;
import java.util.logging.Logger;
import org.bukkit.World;
import org.bukkit.block.Block;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

/**
 * CoreProtectFacade のブロック判定がプレイヤー名を正しく保持できるかを確認するユニットテスト。
 * 実際の CoreProtect API には依存せず、最小限のフェイク API を注入して挙動を固定化する。
 */
final class CoreProtectFacadeTest {

    private CoreProtectFacade facade;
    private World world;

    @BeforeEach
    void setUp() {
        facade = new CoreProtectFacade(Logger.getLogger("test"));
        world = mock(World.class);
        Block block = mock(Block.class);
        when(world.getBlockAt(anyInt(), anyInt(), anyInt())).thenReturn(block);
    }

    @Test
    void lookupBulkReturnsPlayerNameWhenPlaced() throws Exception {
        injectApi(new FakeCoreProtectApi(List.<String[]>of(new String[] {"1", "TestPlayer"})));

        List<CoreProtectFacade.Result> results =
                facade.lookupBulk(world, List.of(BlockVector3.at(1, 2, 3)), 60);

        assertEquals(1, results.size());
        CoreProtectFacade.Result result = results.get(0);
        assertTrue(result.playerPlaced(), "設置判定が true になること");
        assertEquals(Optional.of("TestPlayer"), result.playerName(), "プレイヤー名が保持されること");
    }

    @Test
    void lookupBulkReturnsEmptyWhenPlayerNameMissing() throws Exception {
        injectApi(new FakeCoreProtectApi(List.<String[]>of(new String[] {"1", null})));

        List<CoreProtectFacade.Result> results =
                facade.lookupBulk(world, List.of(BlockVector3.at(4, 5, 6)), 120);

        assertEquals(1, results.size());
        CoreProtectFacade.Result result = results.get(0);
        assertFalse(result.playerPlaced(), "プレイヤー名がない場合は設置と見なさないこと");
        assertEquals(Optional.empty(), result.playerName(), "プレイヤー名は Optional.empty になること");
    }

    private void injectApi(Object api) throws Exception {
        // CoreProtect API へ依存せずにテストできるよう、リフレクションでフェイク API を注入する。
        Field apiField = CoreProtectFacade.class.getDeclaredField("api");
        apiField.setAccessible(true);
        apiField.set(facade, api);

        // Action enum の再解決を強制するため、placeActionId を毎回クリアしておく。
        Field placeActionIdField = CoreProtectFacade.class.getDeclaredField("placeActionId");
        placeActionIdField.setAccessible(true);
        placeActionIdField.set(facade, null);
    }

    /** CoreProtect API のごく一部だけを模倣する軽量フェイク。 */
    private static final class FakeCoreProtectApi {
        private final List<String[]> rows;

        FakeCoreProtectApi(List<String[]> rows) {
            this.rows = rows;
        }

        public List<String[]> blockLookup(Block block, int seconds) {
            return rows;
        }

        public Object parseResult(String[] row) {
            return new FakeResult(row);
        }

        // PLACE の ordinal を 1 に揃えておくことで、本番 API の Action 判定ロジックを模倣する。
        enum Action {
            BREAK,
            PLACE
        }

        private static final class FakeResult {
            private final int actionId;
            private final String player;

            FakeResult(String[] row) {
                this.actionId = Integer.parseInt(row[0]);
                this.player = row.length > 1 ? row[1] : null;
            }

            public int getActionId() {
                return actionId;
            }

            public String getPlayer() {
                return player;
            }
        }
    }
}
