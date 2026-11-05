"""Pure rsync-style block-delta algorithm (no IO inside core functions).

This module provides the building blocks of an rsync-like incremental upload:

* ``compute_block_size``  -- pick a block size from the file size
* ``adler32`` / ``adler32_update``  -- fast rolling checksum
* ``md5``  -- strong checksum with optional seed prefix
* ``build_block_set``  -- slice data into blocks + checksums
* ``compute_delta``  -- sliding-window delta (literal + block refs + EOF)
* ``apply_delta``  -- reassemble data on the server side
* file-level helpers  -- convenience wrappers that still keep the core pure
* ``should_fallback``  -- decide whether full upload is cheaper
* ``RsyncEngine``  -- thin OO wrapper holding seed / block_size config
"""

from __future__ import annotations

import base64
import hashlib
import math
import os
from pathlib import Path

# Adler-32 modulus (largest prime < 2^16)
ADLER_MOD = 65521

# Tunables ---------------------------------------------------------------
_DEFAULT_FALLBACK_THRESHOLD = 0.20   # efficiency below which we fallback
_DEFAULT_FALLBACK_SIZE_BYTES = 1024 * 1024  # 1 MiB minimum size for fallback gate


# ------------------------------------------------------------------------
# 1. Block size formula
# ------------------------------------------------------------------------
def compute_block_size(file_size: int) -> int:
    """Choose an rsync-style block size for ``file_size`` bytes.

    Rule:
      * files <= 700 B            -> single block (return file_size)
      * otherwise:
        1. ``bs = ceil(sqrt(file_size))``
        2. clamp to [700, 16384]
        3. round up to the next multiple of 8
    """
    if file_size <= 700:
        return file_size
    bs = math.ceil(math.isqrt(file_size))
    bs = min(16384, max(700, bs))
    return ((bs + 7) // 8) * 8


# ------------------------------------------------------------------------
# 2. Adler-32 fast checksum
# ------------------------------------------------------------------------
def adler32(buf: bytes) -> int:
    """Compute Adler-32 checksum over ``buf`` from scratch."""
    a = 1
    b = 0
    for x in buf:
        a = (a + x) % ADLER_MOD
        b = (b + a) % ADLER_MOD
    return (b << 16) | a


def adler32_update(data: bytes, a: int, b: int) -> tuple[int, int, int]:
    """Incrementally append ``data`` to an existing Adler-32 state ``(a, b)``.

    Parameters
    ----------
    data:
        New bytes to *append* to the buffer whose prior state was ``(a, b)``.
        To use as a *rolling* update, the caller slides the window one byte
        at a time by also subtracting the outgoing byte — see ``compute_delta``
        for the complete rolling-window recipe.
    a, b:
        The two Adler-32 state variables from the previous computation
        (initial state is ``a=1, b=0``).

    Returns
    -------
    (full_checksum, new_a, new_b)
        ``full_checksum`` is ``(b<<16)|a`` for the concatenated buffer.
        ``new_a`` and ``new_b`` are the updated state variables, which the
        caller feeds back on the next call.
    """
    for x in data:
        a = (a + x) % ADLER_MOD
        b = (b + a) % ADLER_MOD
    return ((b << 16) | a, a, b)


# ------------------------------------------------------------------------
# 3. MD5 strong checksum
# ------------------------------------------------------------------------
def md5(data: bytes, seed: int | None = None) -> bytes:
    """16-byte MD5 digest of ``data``.

    If ``seed`` is provided it is serialised as a big-endian 8-byte integer
    and prepended to ``data`` before hashing — the standard rsync approach
    for keying the strong checksum.
    """
    if seed is not None:
        data = seed.to_bytes(8, "big") + data
    return hashlib.md5(data).digest()


# ------------------------------------------------------------------------
# 4. BuildBlockSet
# ------------------------------------------------------------------------
def build_block_set(
    data: bytes,
    seed: int,
    block_size: int | None = None,
) -> list[dict]:
    """Slice ``data`` into fixed-size blocks and compute their checksums.

    Returns a list of dicts::

        {
            "index":      int,              # 0-based block index
            "offset":     int,              # byte offset inside ``data``
            "length":     int,              # block length (<=block_size; last may be smaller)
            "fast_sum":   int,              # Adler-32 integer
            "strong_sum": str,              # base64(MD5(seed||block))
        }
    """
    if block_size is None:
        block_size = compute_block_size(len(data))
    out: list[dict] = []
    n = len(data)
    for offset in range(0, n, block_size):
        chunk = data[offset : offset + block_size]
        length = len(chunk)
        fast_sum = adler32(chunk)
        strong_b64 = base64.b64encode(md5(chunk, seed)).decode()
        out.append(
            {
                "index": len(out),
                "offset": offset,
                "length": length,
                "fast_sum": fast_sum,
                "strong_sum": strong_b64,
            }
        )
    return out


# ------------------------------------------------------------------------
# 5. ComputeDelta  (client side — produces the delta to upload)
# ------------------------------------------------------------------------
def compute_delta(
    src: bytes,
    dst_blockset: list[dict],
    seed: int,
    block_size: int | None = None,
) -> list[dict]:
    """Compute the delta from local ``src`` against the server's ``dst_blockset``.

    Algorithm
    ---------
    1. For each dst block we try to find it somewhere inside ``src``.
    2. We slide a window across ``src`` whose length matches the dst block,
       compute the (rolling) Adler-32, and, on fast-sum match, verify with
       seeded MD5.
    3. Byte ranges of ``src`` that matched a dst block become ``op=2``
       references; everything in between is emitted as ``op=1`` literal data.
    4. The resulting list always ends with an ``op=0`` EOF marker.

    Delta record schema::

        {"op": 0}                                # EOF / end of delta
        {"op": 1, "length": int, "data": str}    # literal, base64-encoded
        {"op": 2, "index": int}                  # reference to dst_blockset[index]
    """
    if not dst_blockset:
        # Nothing the server can reuse — send the whole source as literal.
        chunks: list[dict] = []
        if src:
            chunks.append(
                {
                    "op": 1,
                    "length": len(src),
                    "data": base64.b64encode(src).decode(),
                }
            )
        chunks.append({"op": 0})
        return chunks

    src_len = len(src)

    # --- Group dst entries by their window length.  Because the *last*
    #     block of the dst file may be shorter than block_size, we must
    #     support multiple candidate window lengths simultaneously. ---
    lengths: list[int] = []
    for e in dst_blockset:
        L = e["length"]
        if L not in lengths:
            lengths.append(L)
    max_len = max(lengths) if lengths else 0

    # Matched dst block indices (in order of *first* match).  Once we pass a
    # block in the linear scan we do not want to match it again later — that
    # would imply the same block appearing in two different places.  We keep
    # a "cursor" for each length: the smallest entry index not yet consumed.
    cursor_of_len: dict[int, int] = {L: 0 for L in lengths}
    count_of_len: dict[int, int] = {}
    for i, e in enumerate(dst_blockset):
        count_of_len.setdefault(e["length"], 0)
        count_of_len[e["length"]] += 1

    matched_so_far: dict[int, int] = {L: 0 for L in lengths}

    # Rolling Adler-32 state per distinct window length.
    # We keep:  a_state[L], b_state[L], window_valid[L], window_start[L]
    a_state: dict[int, int] = {L: 1 for L in lengths}
    b_state: dict[int, int] = {L: 0 for L in lengths}
    window_valid: dict[int, bool] = {L: False for L in lengths}
    window_start: dict[int, int] = {L: -1 for L in lengths}

    def _adler32_of_slice(b: bytes, start: int, end: int) -> int:
        a, bv = 1, 0
        for i in range(start, end):
            x = b[i]
            a = (a + x) % ADLER_MOD
            bv = (bv + a) % ADLER_MOD
        return (bv << 16) | a

    def _advance_rolling(L: int, current_start: int) -> None:
        """Recompute rolling Adler-32 for a window of length L starting at
        ``current_start``.  Uses incremental update when the new window is
        a strict right-shift of the old one; falls back to full recompute
        otherwise (e.g. the very first position, or after a match jump).
        """
        new_start = current_start
        if new_start < 0 or new_start + L > src_len:
            window_valid[L] = False
            return
        if (
            window_valid[L]
            and window_start[L] >= 0
            and new_start == window_start[L] + 1
        ):
            # Rolling update: drop the outgoing byte, add the incoming byte.
            out_byte = src[window_start[L]]
            in_byte = src[new_start + L - 1]
            a_old = a_state[L]
            b_old = b_state[L]
            # Derivation: A_new = 1 + sum of new window = A_old - out_byte + in_byte.
            # B = sum_{k}(k+1 + prefix_sum) -> sliding right adds up -L*out_byte
            # and +A_new, but the constant term "1" summed L times over the old
            # window disappears from B, hence an extra -1.
            a_new = (a_old - out_byte + in_byte) % ADLER_MOD
            b_new = (b_old - L * out_byte + a_new - 1) % ADLER_MOD
            a_state[L] = a_new
            b_state[L] = b_new
        else:
            # Full recompute.
            _checksum = _adler32_of_slice(src, new_start, new_start + L)
            a_state[L] = _checksum & 0xFFFF
            b_state[L] = (_checksum >> 16) & 0xFFFF
        window_valid[L] = True
        window_start[L] = new_start

    # Fast-sum index: fast_to_entries[fast_sum] -> list of dst entries
    # (only those *after* the per-length cursor are candidates).
    fast_to_entries: dict[int, list[dict]] = {}
    for e in dst_blockset:
        fast_to_entries.setdefault(e["fast_sum"], []).append(e)

    consumed_literal = bytearray()
    result: list[dict] = []

    # Initialise rolling states for each length at position i = 0.
    for L in lengths:
        _advance_rolling(L, 0)

    i = 0
    while i < src_len:
        best_entry: dict | None = None
        best_len = 0

        # Check every candidate length.
        for L in lengths:
            if i + L > src_len:
                continue

            # Make sure the rolling state is up-to-date for window [i, i+L).
            # We may be ahead by more than one step (e.g. after a jump), so
            # force a full recompute in that case.
            if not window_valid[L] or window_start[L] != i:
                _advance_rolling(L, i)

            if not window_valid[L]:
                continue

            # Find candidate entries whose fast_sum matches.
            fast = (b_state[L] << 16) | a_state[L]
            candidates = fast_to_entries.get(fast)
            if not candidates:
                continue

            # Among candidates of this length whose index >= cursor_of_len[L]
            # (rsync-order constraint), pick the first whose strong_sum also
            # matches.
            cursor = cursor_of_len[L]
            for c in candidates:
                if c["length"] != L:
                    continue
                if c["index"] < cursor:
                    continue
                window = src[i : i + L]
                if base64.b64encode(md5(window, seed)).decode() == c["strong_sum"]:
                    best_entry = c
                    best_len = L
                    break

            if best_entry is not None:
                break

        if best_entry is not None:
            # Flush pending literal bytes.
            if consumed_literal:
                result.append(
                    {
                        "op": 1,
                        "length": len(consumed_literal),
                        "data": base64.b64encode(bytes(consumed_literal)).decode(),
                    }
                )
                consumed_literal.clear()
            result.append({"op": 2, "index": best_entry["index"]})

            # Advance cursor for this length (rsync ordering).
            L = best_len
            cursor_of_len[L] = best_entry["index"] + 1
            matched_so_far[L] = matched_so_far.get(L, 0) + 1

            i += best_len

            # Invalidate rolling states for all lengths — the next position
            # is not simply i+1 from our previous state.
            for ll in lengths:
                window_valid[ll] = False
                window_start[ll] = -1
            if i < src_len:
                for ll in lengths:
                    _advance_rolling(ll, i)
        else:
            consumed_literal.append(src[i])
            i += 1
            # Advance rolling windows (they were already at i before this
            # iteration; now re-point them at the new i).
            for ll in lengths:
                _advance_rolling(ll, i)

    # Flush trailing literal.
    if consumed_literal:
        result.append(
            {
                "op": 1,
                "length": len(consumed_literal),
                "data": base64.b64encode(bytes(consumed_literal)).decode(),
            }
        )

    result.append({"op": 0})
    return result


# ------------------------------------------------------------------------
# 6. ApplyDelta  (server side — reassemble from old dst + delta)
# ------------------------------------------------------------------------
def apply_delta(
    dst_data: bytes,
    chunks: list[dict],
    blockset: list[dict],
) -> bytes:
    """Reconstruct the updated file on the server.

    * ``op=1`` literal chunks are base64-decoded and appended verbatim.
    * ``op=2`` block references copy bytes out of ``dst_data`` using the
      ``offset`` / ``length`` recorded in ``blockset[index]``.
    * ``op=0`` EOF terminates the stream.
    """
    out = bytearray()
    for ch in chunks:
        op = ch["op"]
        if op == 0:
            break
        if op == 1:
            raw = base64.b64decode(ch["data"])
            out.extend(raw)
        elif op == 2:
            idx = ch["index"]
            entry = blockset[idx]
            off = entry["offset"]
            ln = entry["length"]
            out.extend(dst_data[off : off + ln])
        else:
            raise ValueError(f"Unknown delta op {op!r}")
    return bytes(out)


# ------------------------------------------------------------------------
# 7. File-level helpers (still keep the core algorithms pure)
# ------------------------------------------------------------------------
def build_blockset_from_file(
    path: str | os.PathLike,
    seed: int,
    block_size: int | None = None,
) -> tuple[list[dict], int]:
    """Convenience: read ``path`` and build its blockset.

    Returns ``(blockset, file_size)``.
    """
    data = Path(path).read_bytes()
    bs = block_size if block_size is not None else compute_block_size(len(data))
    return build_block_set(data, seed, bs), len(data)


def apply_delta_to_file(
    dst_path: str | os.PathLike,
    temp_path: str | os.PathLike,
    chunks: list[dict],
    blockset: list[dict],
) -> None:
    """Atomically apply a delta to an existing file.

    Reads ``dst_path`` as the base, applies ``chunks`` writing the result to
    ``temp_path``, then ``os.replace(temp_path, dst_path)`` swaps them.  On
    Windows ``os.replace`` is atomic within the same volume.
    """
    dst_p = Path(dst_path)
    dst_data = dst_p.read_bytes()
    rebuilt = apply_delta(dst_data, chunks, blockset)
    tmp_p = Path(temp_path)
    tmp_p.write_bytes(rebuilt)
    os.replace(tmp_p, dst_p)


# ------------------------------------------------------------------------
# 8. Automatic fallback decision
# ------------------------------------------------------------------------
def should_fallback(
    src_size: int,
    dst_exists: bool,
    delta_efficiency: float,
    md5_mismatch: bool,
    *,
    threshold: float = _DEFAULT_FALLBACK_THRESHOLD,
    size_threshold: int = _DEFAULT_FALLBACK_SIZE_BYTES,
) -> bool:
    """Decide whether rsync delta is worth doing or we should upload whole file.

    Efficiency definition:

        efficiency = 1 - (total_literal_bytes / src_size)

    Higher is better (more bytes saved).  Returns ``True`` (fallback = full
    upload) when any of the following holds:

    * destination file does not exist yet,
    * pre-transfer MD5 verification on the destination mismatched,
    * efficiency is below ``threshold`` *and* source is larger than
      ``size_threshold``.
    """
    if not dst_exists or md5_mismatch:
        return True
    if delta_efficiency < threshold and src_size > size_threshold:
        return True
    return False


def _count_literal_bytes(chunks: list[dict]) -> int:
    """Sum ``length`` across all ``op=1`` chunks up to the EOF marker."""
    n = 0
    for c in chunks:
        if c["op"] == 0:
            break
        if c["op"] == 1:
            n += c["length"]
    return n


def delta_efficiency(chunks: list[dict], src_size: int) -> float:
    """Compute delta efficiency (1 - literal_bytes / src_size)."""
    if src_size <= 0:
        return 1.0
    return 1.0 - (_count_literal_bytes(chunks) / src_size)


# ------------------------------------------------------------------------
# RsyncEngine — thin configuration wrapper
# ------------------------------------------------------------------------
class RsyncEngine:
    """Convenience wrapper that bundles a seed and optional block size.

    All algorithms are still invoked through the module-level pure
    functions — this class only caches configuration.
    """

    def __init__(
        self,
        seed: int = 0,
        block_size: int | None = None,
        fallback_threshold: float = _DEFAULT_FALLBACK_THRESHOLD,
        fallback_size: int = _DEFAULT_FALLBACK_SIZE_BYTES,
    ) -> None:
        self.seed = seed
        self._block_size = block_size
        self.fallback_threshold = fallback_threshold
        self.fallback_size = fallback_size

    # --- Block size ----------------------------------------------------
    @property
    def block_size(self) -> int | None:
        """Explicitly configured block size, or ``None`` for auto."""
        return self._block_size

    def effective_block_size(self, file_size: int) -> int:
        """Resolve block size: explicit or auto-derived from ``file_size``."""
        if self._block_size is not None:
            return self._block_size
        return compute_block_size(file_size)

    # --- Pipeline entry points ----------------------------------------
    def build_blockset(
        self, data: bytes, block_size: int | None = None
    ) -> list[dict]:
        bs = block_size if block_size is not None else self.effective_block_size(len(data))
        return build_block_set(data, self.seed, bs)

    def build_blockset_from_file(
        self, path: str | os.PathLike
    ) -> tuple[list[dict], int]:
        return build_blockset_from_file(path, self.seed, self._block_size)

    def compute_delta(
        self, src: bytes, dst_blockset: list[dict]
    ) -> list[dict]:
        return compute_delta(src, dst_blockset, self.seed)

    def apply_delta(
        self, dst_data: bytes, chunks: list[dict], blockset: list[dict]
    ) -> bytes:
        return apply_delta(dst_data, chunks, blockset)

    def apply_delta_to_file(
        self,
        dst_path: str | os.PathLike,
        temp_path: str | os.PathLike,
        chunks: list[dict],
        blockset: list[dict],
    ) -> None:
        apply_delta_to_file(dst_path, temp_path, chunks, blockset)

    def should_fallback(
        self,
        src_size: int,
        dst_exists: bool,
        delta_eff: float,
        md5_mismatch: bool,
    ) -> bool:
        return should_fallback(
            src_size,
            dst_exists,
            delta_eff,
            md5_mismatch,
            threshold=self.fallback_threshold,
            size_threshold=self.fallback_size,
        )

    @staticmethod
    def compute_efficiency(chunks: list[dict], src_size: int) -> float:
        """Alias for :func:`delta_efficiency`."""
        return delta_efficiency(chunks, src_size)
