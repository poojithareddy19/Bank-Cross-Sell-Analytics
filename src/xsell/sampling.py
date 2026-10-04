"""Deterministic customer sampling shared by the Parquet cache and the model.

A customer is in the sample when (customer_id * 2654435761) mod 2**32 falls
below share * 2**32 (Knuth multiplicative hashing). The arithmetic is plain
integer maths, so the same formula gives the same sample in SQL and Python on
any machine and any library version, unlike built-in hash functions.
"""

from __future__ import annotations

import numpy as np

HASH_MULTIPLIER = 2654435761
HASH_MODULUS = 2**32


def sample_threshold(share: float) -> int:
    if not 0 < share <= 1:
        raise ValueError(f"sample share must be in (0, 1], got {share}")
    return int(share * HASH_MODULUS)


def in_sample(customer_ids: np.ndarray, share: float) -> np.ndarray:
    """Boolean mask: which customer ids belong to the deterministic sample."""
    ids = np.asarray(customer_ids, dtype=np.uint64)
    hashed = (ids * np.uint64(HASH_MULTIPLIER)) % np.uint64(HASH_MODULUS)
    return hashed < np.uint64(sample_threshold(share))
