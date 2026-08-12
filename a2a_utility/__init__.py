"""a2a_utility — shared A2A library: server-side and client-side, kept as two
independent subpackages so importing one doesn't drag in the other's deps
(server needs starlette/uvicorn/a2a-sdk's server extras; client only needs
httpx).

    from a2a_utility.server import serve_as_a2a, run_discovery_server
    from a2a_utility.client import call_agent, DiscoveryClient

See a2a_utility/server/README.md and this package's own README.md for the
full architecture writeup and usage examples.
"""
