"""LangGraph graph API の簡易スタブ。"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Dict, Iterable, Mapping, MutableMapping

from .state import CompiledStateGraph

START = "__start__"
END = "__end__"

NodeCallable = Callable[[MutableMapping[str, Any]], Awaitable[Dict[str, Any]] | Dict[str, Any]]
ResolverCallable = Callable[[MutableMapping[str, Any]], Any]


class StateGraph:
    """LangGraph StateGraph の単純なスタブ実装。"""

    def __init__(self, state_type: Any) -> None:
        self._state_type = state_type
        self._nodes: Dict[str, NodeCallable] = {}
        self._edges: Dict[str, list[str]] = {}
        self._conditional: Dict[str, tuple[ResolverCallable, Mapping[Any, str]]] = {}

    def add_node(self, name: str, func: NodeCallable) -> None:
        self._nodes[name] = func

    def add_edge(self, source: str, target: str) -> None:
        self._edges.setdefault(source, []).append(target)

    def add_conditional_edges(
        self,
        source: str,
        resolver: ResolverCallable,
        mapping: Mapping[Any, str],
    ) -> None:
        self._conditional[source] = (resolver, dict(mapping))

    def compile(self) -> CompiledStateGraph:
        return CompiledStateGraph(self._nodes, self._edges, self._conditional)


__all__ = ["START", "END", "StateGraph"]
