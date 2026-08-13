"""Outbound client for an a2a_utility DISCOVERY node.

Wraps the byte-compatible /register + GET /agents REST contract that
server/main.py's run_discovery_server() exposes (see that module's docstring
for why those two stay plain REST instead of A2A JSON-RPC), plus a search()
that goes through the real A2A JSON-RPC endpoint backed by
DiscoveryAgentExecutor — the "who can do X?" query plan.md describes, as
opposed to list_agents()'s unconditional list-everything-alive.

A coordinator normally only needs list_agents()/resolve(); search() is for
when the agent directory gets large enough that "list everything, let the
LLM pick" stops being a reasonable prompt.
"""

from typing import Optional

import httpx

from .agent_client import call_agent


class DiscoveryClient:
    """Caches the directory from one DISCOVERY node; call refresh()/list_agents()
    to pull the latest set of agents that are currently alive (TTL-filtered
    on the server side)."""

    def __init__(self, registry_url: str):
        self._registry_url = registry_url.rstrip("/")
        self._directory: dict[str, dict] = {}

    async def refresh(self) -> None:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{self._registry_url}/agents", timeout=5.0)
            resp.raise_for_status()
        self._directory = {
            entry["name"]: {
                "description": entry["description"],
                "agent_card_url": entry["agent_card_url"],
            }
            for entry in resp.json()["agents"]
        }

    async def list_agents(self) -> list[dict]:
        """Refreshes and returns [{"name": str, "description": str}, ...]."""
        await self.refresh()
        return [
            {"name": name, "description": entry["description"]}
            for name, entry in self._directory.items()
        ]

    async def resolve(self, name: str) -> Optional[dict]:
        """Returns {"description": str, "agent_card_url": str} for `name`,
        refreshing first if it isn't cached yet. None if still unknown."""
        if name not in self._directory:
            await self.refresh()
        return self._directory.get(name)

    async def search(self, query: str) -> str:
        """Asks the DISCOVERY node's own A2A endpoint to rank agents against
        `query` (DiscoveryAgentExecutor -> SearchAgentUseCase.search) and
        returns its answer as text. Empty query text means list-all on the
        server side, so prefer list_agents() for that case.

        Targets the registry base URL (not /a2a/v1/discovery): the native a2a
        client resolves the agent card from base_url/.well-known/... and then
        POSTs to the card's advertised interface url, which the DISCOVERY node
        serves at "/"."""
        return await call_agent(self._registry_url, query)

    @staticmethod
    def agent_base_url(agent_card_url: str) -> str:
        """agent_card_url is .../.well-known/agent-card.json; the A2A
        endpoint itself is that same origin's root."""
        return agent_card_url.rsplit("/.well-known/", 1)[0] + "/"
