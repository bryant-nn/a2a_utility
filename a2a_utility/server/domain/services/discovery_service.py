"""Pure domain logic for ranking agents against a free-text query.

No I/O, no framework: given a set of descriptors and a query string, return the
descriptors that plausibly match, best first. The registry adapter owns storage;
this owns the matching policy so it can be unit-tested in isolation.
"""

from ..models.agent_card import AgentDescriptor


def _score(descriptor: AgentDescriptor, terms: list[str]) -> int:
    haystack = f"{descriptor.name} {descriptor.description}".lower()
    score = 0
    for term in terms:
        if term in descriptor.name.lower():
            score += 2  # a name hit is worth more than a description hit
        elif term in haystack:
            score += 1
    return score


def rank_agents(descriptors: list[AgentDescriptor], query: str) -> list[AgentDescriptor]:
    """Return descriptors matching `query`, best match first.

    An empty/whitespace query matches everything (a plain "list all" request).
    """
    terms = [t for t in query.lower().split() if t]
    if not terms:
        return list(descriptors)

    scored = [(d, _score(d, terms)) for d in descriptors]
    matched = [(d, s) for d, s in scored if s > 0]
    matched.sort(key=lambda pair: pair[1], reverse=True)
    return [d for d, _ in matched]
