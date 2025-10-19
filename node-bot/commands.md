# Node 側が受け付ける JSON コマンド（WS 経由）

- chat: `{ "type": "chat", "args": { "text": "こんにちは" } }`
- moveTo: `{ "type": "moveTo", "args": { "x": 100, "y": 64, "z": -30 } }`
- equipItem: `{ "type": "equipItem", "args": { "toolType": "pickaxe", "destination": "hand" } }`
- （今後追加）dig / place / follow / attack / craft / scan 等
- mineOre: `{ "type": "mineOre", "args": { "ores": ["redstone_ore"], "scanRadius": 12, "maxTargets": 3 } }`
