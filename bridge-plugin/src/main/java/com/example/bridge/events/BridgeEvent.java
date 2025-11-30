package com.example.bridge.events;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.sk89q.worldedit.math.BlockVector3;
import java.util.Map;

/**
 * Python 側へ配信するブリッジイベントの共通表現。イベントレベルや保護領域、
 * 危険ブロック座標を含めておき、LangGraph のリフレクションプロンプトに安全情報を
 * 注入しやすくする。
 */
public record BridgeEvent(
        String type,
        String message,
        String eventLevel,
        String region,
        BlockVector3 blockPosition,
        Map<String, Object> attributes) {

    public ObjectNode toJson(ObjectMapper mapper) {
        ObjectNode node = mapper.createObjectNode();
        node.put("type", type);
        node.put("message", message);
        node.put("event_level", eventLevel);
        if (region != null && !region.isEmpty()) {
            node.put("region", region);
        }
        if (blockPosition != null) {
            ObjectNode pos = mapper.createObjectNode();
            pos.put("x", blockPosition.getX());
            pos.put("y", blockPosition.getY());
            pos.put("z", blockPosition.getZ());
            node.set("block_pos", pos);
        }
        if (attributes != null && !attributes.isEmpty()) {
            node.set("attributes", mapper.valueToTree(attributes));
        }
        return node;
    }
}
