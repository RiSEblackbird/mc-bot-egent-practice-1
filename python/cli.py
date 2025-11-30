# -*- coding: utf-8 -*-
"""運用向け CLI エントリポイント。"""

from __future__ import annotations

import argparse
import asyncio
import json
import threading
from dataclasses import dataclass
from typing import Any, Dict, Optional, Sequence, Tuple

from actions import Actions
from bridge_client import BridgeClient, BridgeError
from bridge_ws import BotBridge
from modes.tunnel import TunnelMode, TunnelSection
from modes.tunnel_direction import infer_tunnel_direction, format_direction
from dotenv import load_dotenv

_DANGER_LEVELS = {"warning", "fault", "danger"}


@dataclass
class TunnelArgs:
    world: str
    anchor: Tuple[int, int, int]
    direction: Optional[Tuple[int, int, int]]
    section: TunnelSection
    length: int
    owner: str
    auto_direction: bool = False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Minecraft Agent CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    tunnel = subparsers.add_parser("tunnel", help="継続採掘モードを実行する")
    tunnel.add_argument("--world", required=True, help="対象ワールド名")
    tunnel.add_argument("--anchor", nargs=3, type=int, metavar=("X", "Y", "Z"), required=True)
    tunnel.add_argument(
        "--dir",
        nargs="+",
        metavar="DIR",
        required=True,
        help="進行方向（例: --dir 1 0 0 または --dir auto）",
    )
    tunnel.add_argument("--section", default="2x2", help="断面サイズ（例: 2x2）")
    tunnel.add_argument("--len", type=int, default=64, help="採掘長さ")
    tunnel.add_argument("--owner", default="", help="WorldGuard 登録用の所有者名")

    agentbridge = subparsers.add_parser("agentbridge", help="AgentBridge 連携コマンド")
    agentbridge_subparsers = agentbridge.add_subparsers(dest="agentbridge_command", required=True)

    jobs_parser = agentbridge_subparsers.add_parser("jobs", help="ジョブ関連の監視")
    jobs_subparsers = jobs_parser.add_subparsers(dest="jobs_command", required=True)

    jobs_watch = jobs_subparsers.add_parser("watch", help="SSE ストリームでジョブイベントを監視する")
    jobs_watch.add_argument("--job-id", help="特定のジョブ ID に限定する")
    jobs_watch.add_argument(
        "--danger-only",
        action="store_true",
        help="warning / fault レベルのみ出力する",
    )
    jobs_watch.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="出力形式",
    )

    return parser.parse_args()


def parse_section(raw: str) -> TunnelSection:
    try:
        width_str, height_str = raw.lower().split("x", 1)
        width = max(int(width_str), 1)
        height = max(int(height_str), 1)
        return TunnelSection(width=width, height=height)
    except Exception as exc:  # noqa: BLE001
        raise argparse.ArgumentTypeError(f"Invalid section format: {raw}") from exc


def _parse_direction_argument(raw: Sequence[str]) -> Tuple[Optional[Tuple[int, int, int]], bool]:
    if not raw:
        return None, False
    if len(raw) == 1 and raw[0].lower() == "auto":
        return None, True
    if len(raw) != 3:
        raise SystemExit("--dir には 3 要素の整数ベクトル、または 'auto' を指定してください。")
    try:
        dx, dy, dz = (int(raw[0]), int(raw[1]), int(raw[2]))
    except ValueError as exc:  # noqa: BLE001
        raise SystemExit("--dir には整数を指定してください。") from exc
    if dy != 0:
        raise SystemExit("TunnelMode は水平のみ対応しています。Y 成分は 0 を指定してください。")
    if dx == 0 and dz == 0:
        raise SystemExit("進行方向ベクトルがゼロです。X/Z のいずれかに値を指定してください。")
    return (dx, dy, dz), False


async def run_tunnel(args: TunnelArgs) -> None:
    load_dotenv()
    bridge = BridgeClient()
    actions = Actions(BotBridge())
    mode = TunnelMode(bridge, actions)
    anchor_dict = {"x": args.anchor[0], "y": args.anchor[1], "z": args.anchor[2]}
    try:
        direction = args.direction
        if args.auto_direction:
            try:
                inference = infer_tunnel_direction(
                    bridge,
                    args.world,
                    anchor=anchor_dict,
                    section=args.section,
                )
            except (BridgeError, ValueError) as exc:
                raise SystemExit(f"自動方向推定に失敗しました: {exc}") from exc
            direction = inference.direction
            print(
                f"[Tunnel] 推定方向: {format_direction(direction)} "
                f"(score={inference.score:.2f}, safe_blocks={inference.safe_blocks})"
            )
        if direction is None:
            raise SystemExit("進行方向を決定できませんでした。")
        await mode.run(
            world=args.world,
            anchor=anchor_dict,
            direction=direction,
            section=args.section,
            length=args.length,
            owner=args.owner,
        )
    except BridgeError as exc:
        raise SystemExit(f"Bridge error: {exc}") from exc
    finally:
        bridge.close()


def run_agentbridge_jobs_watch(args: argparse.Namespace) -> None:
    load_dotenv()
    bridge = BridgeClient()
    stop_event = threading.Event()
    exception_holder: list[BaseException] = []

    def handle_event(event: Dict[str, Any]) -> None:
        if not isinstance(event, dict):
            return
        if not _should_emit_event(event, job_id=args.job_id, danger_only=args.danger_only):
            return
        print(_format_bridge_event(event, args.format))

    def runner() -> None:
        try:
            bridge.consume_event_stream(handle_event, stop_event)
        except Exception as exc:  # pragma: no cover - bubbled up after join
            exception_holder.append(exc)

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    try:
        while thread.is_alive():
            thread.join(timeout=0.5)
    except KeyboardInterrupt:
        stop_event.set()
        thread.join(timeout=2.0)
    finally:
        bridge.close()
    if exception_holder:
        raise SystemExit(f"AgentBridge event stream failed: {exception_holder[0]}") from exception_holder[0]


def main() -> None:
    ns = parse_args()
    if ns.command == "tunnel":
        direction_input, auto_direction = _parse_direction_argument(ns.dir)
        section = parse_section(ns.section)
        tunnel_args = TunnelArgs(
            world=ns.world,
            anchor=(ns.anchor[0], ns.anchor[1], ns.anchor[2]),
            direction=direction_input,
            section=section,
            length=ns.len,
            owner=ns.owner,
            auto_direction=auto_direction,
        )
        asyncio.run(run_tunnel(tunnel_args))
    elif ns.command == "agentbridge":
        if ns.agentbridge_command == "jobs" and ns.jobs_command == "watch":
            run_agentbridge_jobs_watch(ns)
        else:
            raise SystemExit("Unsupported agentbridge subcommand")
    else:
        raise SystemExit(f"Unknown command: {ns.command}")


def _should_emit_event(event: Dict[str, Any], *, job_id: Optional[str], danger_only: bool) -> bool:
    if job_id:
        job_value = event.get("job_id") or event.get("jobId")
        if str(job_value) != job_id:
            return False
    if danger_only:
        level = str(event.get("event_level") or "").lower()
        if level not in _DANGER_LEVELS:
            return False
    return True


def _format_bridge_event(event: Dict[str, Any], output_format: str) -> str:
    if output_format == "json":
        return json.dumps(event, ensure_ascii=False)

    level = str(event.get("event_level") or "info").upper()
    job_id = event.get("job_id") or event.get("jobId") or "-"
    message = event.get("message") or event.get("type") or "event"
    region = event.get("region")
    block_pos = event.get("block_pos") or event.get("blockPos")
    extras = []
    if region:
        extras.append(f"region={region}")
    if isinstance(block_pos, dict):
        coords = ",".join(
            f"{axis}={block_pos.get(axis)}" for axis in ("x", "y", "z") if axis in block_pos
        )
        if coords:
            extras.append(f"pos={coords}")
    return f"[{level}] job={job_id} {message}" + (f" ({' '.join(extras)})" if extras else "")


if __name__ == "__main__":
    main()
