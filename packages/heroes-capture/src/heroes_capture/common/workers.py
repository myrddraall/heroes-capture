"""A thread pool that keeps results in order (the stitches' per-tile work)."""

import os
from collections import deque
from concurrent.futures import ThreadPoolExecutor

WORKERS = os.cpu_count() or 4  # threads for the per-tile work, one a core (image decoding and numpy release Python's lock)


def ordered_map(fn, items, workers: int = WORKERS):
    """fn over items on a thread pool, results yielded in the items' order, with only a few
    running ahead (each result can be a full-size tile)."""
    items = iter(items)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = deque(pool.submit(fn, item) for _, item in zip(range(2 * workers), items))  # the count first: zip would otherwise take one item too many
        while pending:
            result = pending.popleft().result()
            nxt = next(items, pending)  # `pending` as the end marker
            if nxt is not pending:
                pending.append(pool.submit(fn, nxt))
            yield result
