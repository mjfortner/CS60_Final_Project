"""
Protocol helpers for Courier.

These functions build the in-memory message dictionaries that the
NetworkIO layer will serialize to/from bytes.
"""

from typing import List, Dict, Any, Iterable, Tuple, Set, Optional
import secrets

MAX_SACK_WINDOW_BITS = 2048


def make_data_msg(
    bundle_id: str,
    chunk_id: int,
    total_chunks: int,
    block_id: int,
    k: int,
    r: int,
    checksum: int,
    payload: Optional[bytes] = None,
    payload_str: Optional[bytes | str] = None,
) -> Dict[str, Any]:
    payload_bytes: Any = payload if payload is not None else payload_str
    if payload_bytes is None:
        payload_bytes = b""
    if isinstance(payload_bytes, str):
        payload_bytes = payload_bytes.encode("utf-8")

    return {
        "msg_type": "DATA",
        "bundle_id": str(bundle_id),
        "chunk_id": int(chunk_id),
        "total_chunks": int(total_chunks),
        "block_id": int(block_id),
        "k": int(k),
        "r": int(r),
        "checksum": int(checksum),
        "payload": payload_bytes,
    }


def _compute_sack_window(acked_chunks: Iterable[int]) -> Tuple[int, bytes]:
    """
    Given a set/list of acked chunk_ids, compute:

      - recv_watermark: largest N such that all chunks 0..N are acked
      - sack_bitmap: bitmap for some window of chunks > recv_watermark

    The bitmap is limited to MAX_SACK_WINDOW_BITS bits to keep SACK
    messages comfortably under the MTU.
    """
    acked_set: Set[int] = {int(c) for c in acked_chunks if c >= 0}
    if not acked_set:
        return -1, b""

    watermark = -1
    next_expected = 0
    for c in sorted(acked_set):
        if c == next_expected:
            watermark = c
            next_expected += 1
        elif c > next_expected:
            break

    max_acked = max(acked_set)
    if max_acked <= watermark:
        return watermark, b""

    max_high = min(max_acked, watermark + MAX_SACK_WINDOW_BITS)
    num_bits = max_high - (watermark + 1) + 1
    num_bytes = (num_bits + 7) // 8
    bitmap = bytearray(num_bytes)

    for c in acked_set:
        if c <= watermark:
            continue
        if c > max_high:
            continue
        bit_index = c - (watermark + 1)
        byte_index = bit_index // 8
        bit_pos = bit_index % 8
        bitmap[byte_index] |= (1 << (7 - bit_pos))

    return watermark, bytes(bitmap)


def make_sack_msg(bundle_id: str, acked_chunks_list: Iterable[int]) -> Dict[str, Any]:
    recv_watermark, bitmap = _compute_sack_window(acked_chunks_list)
    return {
        "msg_type": "SACK",
        "bundle_id": str(bundle_id),
        "recv_watermark": int(recv_watermark),
        "sack_bitmap": bitmap,
    }


def make_custody_req_msg(
    bundle_id: str, ranges: List[Tuple[int, int]], ttl_remaining: float
) -> Dict[str, Any]:

    ranges_norm = [(int(a), int(b)) for (a, b) in ranges]
    return {
        "msg_type": "CUSTODY_REQ",
        "bundle_id": str(bundle_id),
        "ranges": ranges_norm,
        "ttl_remaining": float(ttl_remaining),
    }


def make_custody_ack_msg(
    bundle_id: str,
    ranges: List[Tuple[int, int]],
    ack_nonce: int | None = None,
) -> Dict[str, Any]:
    """
    Downstream node confirms that it has accepted custody
    for the specified chunk ranges.

    ack_nonce is a 64-bit token; if not provided, we generate one.
    """
    ranges_norm = [(int(a), int(b)) for (a, b) in ranges]
    if ack_nonce is None:
        ack_nonce = secrets.randbits(64)
    return {
        "msg_type": "CUSTODY_ACK",
        "bundle_id": str(bundle_id),
        "ranges": ranges_norm,
        "ack_nonce": int(ack_nonce),
    }


def make_delivered_msg(bundle_id: str) -> Dict[str, Any]:
    """
    Sent by the final destination back toward the original sender
    once the bundle has been fully reconstructed and written to disk.
    """
    return {
        "msg_type": "DELIVERED",
        "bundle_id": str(bundle_id),
    }
