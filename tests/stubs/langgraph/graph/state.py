"""LangGraph の CompiledStateGraph スタブ。"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Dict, Mapping, MutableMapping, Callable


NodeCallable = Callable[[MutableMapping[str, Any]], Awaitable[Dict[str, Any]] | Dict[str, Any]]
ResolverCallable = Callable[[MutableMapping[str, Any]], Any]


class CompiledStateGraph:
    """StateGraph.compile() が返す簡易スタブ。"""

    def __init__(
        self,
        nodes: Mapping[str, NodeCallable],
        edges: Mapping[str, list[str]],
        conditional: Mapping[str, tuple[ResolverCallable, Mapping[Any, str]]],
    ) -> None:
        self._nodes = dict(nodes)
        self._edges = {source: list(targets) for source, targets in edges.items()}
        self._conditional = {
            name: (resolver, dict(mapping)) for name, (resolver, mapping) in conditional.items()
        }

    async def ainvoke(self, initial_state: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
        state: MutableMapping[str, Any] = dict(initial_state)
        current = "__start__"

        while True:
            if current in self._conditional:
                resolver, mapping = self._conditional[current]
                route_key = resolver(state)
                node_name = mapping.get(route_key, "__end__")
            else:
                targets = self._edges.get(current, [])
                if not targets:
                    break
                node_name = targets[0]

            if node_name == "__end__":
                break

            node = self._nodes[node_name]
            result = node(state)
            if asyncio.iscoroutine(result):
                result = await result
            if isinstance(result, dict):
                state.update(result)

            current = node_name

        return state


__all__ = ["CompiledStateGraph"]
