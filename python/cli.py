# -*- coding: utf-8 -*-
"""運用向け CLI エントリポイント。"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from typing import Sequence, Tuple

from actions import Actions
from bridge_client import BridgeClient, BridgeError
from bridge_ws import BotBridge
from modes.tunnel import TunnelMode, TunnelSection
from dotenv import load_dotenv


@dataclass
class TunnelArgs:
    world: str
    anchor: Tuple[int, int, int]
    direction: Tuple[int, int, int]
    section: TunnelSection
    length: int
    owner: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Minecraft Agent CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    tunnel = subparsers.add_parser("tunnel", help="継続採掘モードを実行する")
    tunnel.add_argument("--world", required=True, help="対象ワールド名")
    tunnel.add_argument("--anchor", nargs=3, type=int, metavar=("X", "Y", "Z"), required=True)
    tunnel.add_argument(
        "--dir",
        nargs=3,
        type=int,
        metavar=("DX", "DY", "DZ"),
        help="進行方向（3要素整数ベクトル）",
    )
    tunnel.add_argument("--section", default="2x2", help="断面サイズ（例: 2x2）")
    tunnel.add_argument("--len", type=int, default=64, help="採掘長さ")
    tunnel.add_argument("--owner", default="", help="WorldGuard 登録用の所有者名")

    return parser.parse_args()


def parse_section(raw: str) -> TunnelSection:
    try:
        width_str, height_str = raw.lower().split("x", 1)
        width = max(int(width_str), 1)
        height = max(int(height_str), 1)
        return TunnelSection(width=width, height=height)
    except Exception as exc:  # noqa: BLE001
        raise argparse.ArgumentTypeError(f"Invalid section format: {raw}") from exc


async def run_tunnel(args: TunnelArgs) -> None:
    load_dotenv()
    bridge = BridgeClient()
    actions = Actions(BotBridge())
    mode = TunnelMode(bridge, actions)
    try:
        await mode.run(
            world=args.world,
            anchor={"x": args.anchor[0], "y": args.anchor[1], "z": args.anchor[2]},
            direction=args.direction,
            section=args.section,
            length=args.length,
            owner=args.owner,
        )
    except BridgeError as exc:
        raise SystemExit(f"Bridge error: {exc}") from exc
    finally:
        bridge.close()


def main() -> None:
    ns = parse_args()
    if ns.command == "tunnel":
        if ns.dir is None:
            raise SystemExit("--dir は必須です (auto 推定は今後の拡張予定)")
        section = parse_section(ns.section)
        tunnel_args = TunnelArgs(
            world=ns.world,
            anchor=(ns.anchor[0], ns.anchor[1], ns.anchor[2]),
            direction=(ns.dir[0], ns.dir[1], ns.dir[2]),
            section=section,
            length=ns.len,
            owner=ns.owner,
        )
        asyncio.run(run_tunnel(tunnel_args))
    else:
        raise SystemExit(f"Unknown command: {ns.command}")


if __name__ == "__main__":
    main()
