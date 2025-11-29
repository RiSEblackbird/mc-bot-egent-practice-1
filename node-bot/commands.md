# Node 側が受け付ける JSON コマンド（WS 経由）

- chat: `{ "type": "chat", "args": { "text": "こんにちは" } }`
- moveTo: `{ "type": "moveTo", "args": { "x": 100, "y": 64, "z": -30 } }`
- equipItem: `{ "type": "equipItem", "args": { "toolType": "pickaxe", "destination": "hand" } }`
- mineBlocks: `{ "type": "mineBlocks", "args": { "positions": [{"x":1,"y":64,"z":-3}] } }`
- placeBlock: `{ "type": "placeBlock", "args": { "block": "oak_planks", "position": {"x":2,"y":65,"z":5}, "face": "north", "sneak": true } }`
- followPlayer: `{ "type": "followPlayer", "args": { "target": "Taishi", "stopDistance": 2, "maintainLineOfSight": true } }`
- attackEntity: `{ "type": "attackEntity", "args": { "target": "zombie", "mode": "melee", "chaseDistance": 6 } }`
- craftItem: `{ "type": "craftItem", "args": { "item": "oak_planks", "amount": 3, "useCraftingTable": false } }`
- mineOre: `{ "type": "mineOre", "args": { "ores": ["redstone_ore"], "scanRadius": 12, "maxTargets": 3 } }`
