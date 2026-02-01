import hashlib
from typing import List

def sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()

def merkle_root(leaves: List[bytes]) -> bytes:
    """
    Simple Merkle root over a list of leaves.
    If odd number of nodes in a layer, duplicate the last one.
    """
    if not leaves:
        raise ValueError("No leaves to build Merkle tree")

    layer = [sha256(l) for l in leaves]

    while len(layer) > 1:
        next_layer = []
        for i in range(0, len(layer), 2):
            a = layer[i]
            b = layer[i + 1] if i + 1 < len(layer) else layer[i]
            next_layer.append(sha256(a + b))
        layer = next_layer

    return layer[0]