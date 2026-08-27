"""One parse per turtle document, shared.

Everything here derives from a processor's SHACL: which processors it declares,
the config form of one of them, its properties, its ports, the shape the export
writes values with. Each of those used to parse the file again -- and a
repository declaring eight processors was parsed once per processor, per
derivation. Listing a page of such repositories spent seconds in
`notation3.py`, and because parsing holds the GIL it also stopped the
concurrent fetches around it from overlapping.

The graph is keyed by the document text, so it is never stale: a different file
is a different key. It is **read-only**. A caller that needs to change a graph
parses its own copy -- `ComponentContract.to_raw_ttl` does exactly that, which
is why it does not come through here.
"""

from functools import lru_cache
from weakref import WeakKeyDictionary

from rdflib import Graph


# Turtle documents are processor files: tens of kilobytes at most, and the
# number of distinct ones is the number of repositories in play.
_CACHE_SIZE = 512


@lru_cache(maxsize=_CACHE_SIZE)
def parsed_graph(ttl_string: str) -> Graph | None:
    """The parsed document, or None if it is not turtle.

    None is cached too: an invalid file is asked about as often as a valid one
    (every listing checks whether each file parses at all) and re-failing is as
    expensive as re-succeeding.

    Treat the result as immutable. Mutating it corrupts every later reader of
    the same document.
    """
    graph = Graph()
    try:
        graph.parse(data=ttl_string, format="turtle")
    except Exception:
        return None
    return graph


def is_valid_turtle(ttl_string: str) -> bool:
    return parsed_graph(ttl_string) is not None


# `graph.namespaces()` walks the store and builds a list every time it is
# called. Shortening a URI needs that list, and a shape has three URIs per
# property, so a page of components called it over a hundred thousand times for
# a few dozen distinct documents. Keyed weakly on the graph: these are the
# read-only graphs above, and a namespace list belongs to exactly one of them.
_prefix_lists: WeakKeyDictionary = WeakKeyDictionary()


def namespace_prefixes(graph: Graph) -> tuple:
    """The graph's (prefix, namespace) pairs, in the order it yields them.

    Order is the graph's own, because the first namespace a URI starts with is
    the prefix that gets used.
    """
    prefixes = _prefix_lists.get(graph)
    if prefixes is None:
        prefixes = tuple(graph.namespaces())
        _prefix_lists[graph] = prefixes
    return prefixes
