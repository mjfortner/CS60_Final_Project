import os
import zlib
import logging
from dataclasses import dataclass, field
from typing import Dict, Set, Tuple, Any, List, Optional

from .config import TransferConfig, FECConfig
from .storage import SQLiteStorage
from .protocol import make_sack_msg, make_delivered_msg
from .protocol import make_sack_msg, make_delivered_msg, make_custody_ack_msg

logger = logging.getLogger(__name__)


@dataclass
class ReceiveState:
    bundle_id: str
    total_chunks: int
    fec_enabled: bool
    k: int
    r: int
    output_path: str
    num_data_chunks: int = 0

    received_chunks: Set[int] = field(default_factory=set)
    data_chunks: Dict[int, bytes] = field(default_factory=dict)
    parity_chunks: Dict[int, List[bytes]] = field(default_factory=dict)
    delivered: bool = False
    write_buffer: List[Dict[str, Any]] = field(default_factory=list)


class ReceiveEngine:
    """
    Receive path: handle DATA packets, send SACKs,
    and reassemble files on completion. Supports optional XOR FEC decoding.
    """

    def __init__(
        self,
        transfer_config: TransferConfig,
        fec_config: FECConfig,
        storage: SQLiteStorage,
        network_send_func,
        node_id: str,
        output_dir: str = "received",
    ):
        self.transfer_config = transfer_config
        self.fec_config = fec_config
        self.storage = storage
        self.network_send = network_send_func
        self.node_id = node_id

        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

        self.active_receives: Dict[str, ReceiveState] = {}


    def handle_data(self, msg: Dict[str, Any], sender_addr: Tuple[str, int]) -> None:
        bundle_id = msg["bundle_id"]
        chunk_id = int(msg["chunk_id"])
        
        state = self.active_receives.get(bundle_id)
        if state is None:
            state = self._create_receive_state(
                bundle_id=bundle_id,
                total_chunks=int(msg["total_chunks"]),
                k=int(msg.get("k", 0)),
                r=int(msg.get("r", 0)),
                sender_addr=sender_addr,
            )
            self.active_receives[bundle_id] = state

        if chunk_id in state.received_chunks:
            self._send_sack(bundle_id, sender_addr)
            return

        payload = msg["payload"]
        if isinstance(payload, str):
            payload = payload.encode("utf-8")

        state.received_chunks.add(chunk_id)
        
        block_id = int(msg.get("block_id", 0))
        if state.fec_enabled and state.k > 0 and state.num_data_chunks > 0:
            if chunk_id < state.num_data_chunks:
                state.data_chunks[chunk_id] = payload
            else:
                state.parity_chunks.setdefault(block_id, []).append(payload)
        else:
            state.data_chunks[chunk_id] = payload

        chunk_record = {
            "bundle_id": bundle_id,
            "chunk_id": chunk_id,
            "is_parity": int(state.fec_enabled and chunk_id >= state.num_data_chunks),
            "block_id": block_id,
            "k": state.k,
            "r": state.r,
            "payload": payload,
            "checksum": int(msg["checksum"]),
            "flags": 0,
        }
        state.write_buffer.append(chunk_record)

        if state.fec_enabled:
            self._try_fec_reconstruct(state, block_id)
        # ------------------------------------------------------------

        if len(state.write_buffer) >= 500 or len(state.received_chunks) == state.total_chunks:
            self.storage.save_chunks_bulk(state.write_buffer)
            state.write_buffer.clear()

        if len(state.received_chunks) % 50 == 0 or not state.write_buffer or len(state.received_chunks) == state.total_chunks:
            self._send_sack(bundle_id, sender_addr)

        self._maybe_deliver(state, sender_addr)

    # 
    # Internal helpers
    # ------------------------------------------------------------------

    def _create_receive_state(
        self,
        bundle_id: str,
        total_chunks: int,
        k: int,
        r: int,
        sender_addr: Tuple[str, int],
    ) -> ReceiveState:
        """
        Create bundle metadata and ReceiveState when the first DATA arrives.
        Also infers how many data chunks exist (excluding parity) if FEC is used.
        """
        fec_enabled = bool(k and r and self.fec_config.enabled)

        num_data_chunks = total_chunks
        if fec_enabled:
            inferred_blocks, inferred_data = self._infer_block_and_data_count(
                total_chunks, k, r
            )
            if inferred_blocks is not None:
                num_data_chunks = inferred_data
            else:
                fec_enabled = False
                k = 0
                r = 0
                logger.warning(
                    "Bundle %s: could not infer FEC layout from total_chunks=%d, k=%d, r=%d. "
                    "Disabling FEC for this bundle.",
                    bundle_id,
                    total_chunks,
                    k,
                    r,
                )

        output_path = os.path.join(self.output_dir, f"bundle_{bundle_id}.bin")

        bundle_record = {
            "bundle_id": bundle_id,
            "src": f"{sender_addr[0]}:{sender_addr[1]}",
            "dst": self.node_id,
            "ttl": self.transfer_config.ttl_sec,
            "state": "receiving",
            "total_chunks": total_chunks,
            "file_path": output_path,
            "file_size": 0,
            "fec_enabled": fec_enabled,
            "k": k if fec_enabled else 0,
            "r": r if fec_enabled else 0,
            "bytes_sent": 0,
            "chunks_retransmitted": 0,
        }
        self.storage.save_bundle(bundle_record)

        logger.info(
            "Created receive state for bundle %s: total_chunks=%d, fec_enabled=%s, num_data_chunks=%d",
            bundle_id,
            total_chunks,
            fec_enabled,
            num_data_chunks,
        )

        return ReceiveState(
            bundle_id=bundle_id,
            total_chunks=total_chunks,
            fec_enabled=fec_enabled,
            k=k,
            r=r,
            output_path=output_path,
            num_data_chunks=num_data_chunks,
        )

    def _infer_block_and_data_count(
        self, total_chunks: int, k: int, r: int
    ) -> Tuple[Optional[int], Optional[int]]:
        """
        Infer number of FEC blocks and data chunks from total_chunks, k, r.

        We know:
          total_chunks = num_data_chunks + r * B
          B = ceil(num_data_chunks / k)

        We search for integer B satisfying:
          k*(B-1) < num_data_chunks <= k*B
        """
        for B in range(1, total_chunks + 1):
            num_data = total_chunks - r * B
            if num_data <= 0:
                continue
            if k * (B - 1) < num_data <= k * B:
                return B, num_data
        return None, None

    def _send_sack(self, bundle_id: str, sender_addr: Tuple[str, int]) -> None:
        """Send SACK with the set of received chunk IDs."""
        state = self.active_receives.get(bundle_id)
        if not state:
            return

        acked = sorted(state.received_chunks)
        msg = make_sack_msg(bundle_id, acked)
        self.network_send(msg, sender_addr)

    def _xor_bytes(self, base: bytes, others: List[bytes]) -> bytes:
        """
        XOR a base payload with a list of other payloads.
        Mirrors the logic used on the send side for FEC parity.
        """
        result = bytearray(base)
        for payload in others:
            limit = min(len(result), len(payload))
            for i in range(limit):
                result[i] ^= payload[i]
            if len(payload) > len(result):
                result.extend(payload[len(result) :])
        return bytes(result)

    def _try_fec_reconstruct(self, state: ReceiveState, block_id: int) -> None:
        """
        Try to reconstruct one missing data chunk in the given block using XOR FEC.
        Only handles the case of exactly one missing data chunk.
        """
        if not state.fec_enabled or state.k <= 0 or state.r <= 0:
            return
        if block_id not in state.parity_chunks:
            return

        block_k = state.k
        start = block_id * block_k
        end = min(start + block_k, state.num_data_chunks)
        expected_ids = list(range(start, end))

        present_ids = [cid for cid in expected_ids if cid in state.data_chunks]
        missing_ids = [cid for cid in expected_ids if cid not in state.data_chunks]

        if len(missing_ids) != 1:
            return

        missing_id = missing_ids[0]
        parity_payload = state.parity_chunks[block_id][0]

        known_payloads = [state.data_chunks[cid] for cid in present_ids]
        recovered = self._xor_bytes(parity_payload, known_payloads)

        state.data_chunks[missing_id] = recovered
        state.received_chunks.add(missing_id)

        chunk_record = {
            "bundle_id": state.bundle_id,
            "chunk_id": missing_id,
            "is_parity": 0,
            "block_id": block_id,
            "k": state.k,
            "r": state.r,
            "payload": recovered,
            "checksum": zlib.crc32(recovered) & 0xFFFFFFFF,
            "flags": 0,
        }
        self.storage.save_chunk(chunk_record)

        logger.info(
            "Bundle %s: reconstructed missing data chunk %d in block %d via FEC",
            state.bundle_id,
            missing_id,
            block_id,
        )

    def _maybe_deliver(self, state: ReceiveState, sender_addr: Tuple[str, int]) -> None:
        """
        If we have all data chunks, assemble file and send DELIVERED + CUSTODY_ACK.
        """
        if state.delivered:
            return

        if state.num_data_chunks == 0:
            num_data = state.total_chunks
        else:
            num_data = state.num_data_chunks

        missing = [cid for cid in range(num_data) if cid not in state.data_chunks]
        if missing:
            return

        ordered_ids = list(range(num_data))
        try:
            with open(state.output_path, "wb") as f:
                for cid in ordered_ids:
                    f.write(state.data_chunks[cid])
        except Exception as e:
            logger.error("Failed to write output file: %s", e)
            return

        self.storage.update_bundle_state(state.bundle_id, "delivered")
        
        state.delivered = True

        logger.info(
            "Bundle %s delivered: wrote bytes to %s",
            state.bundle_id,
            state.output_path,
        )

        msg = make_delivered_msg(state.bundle_id)
        self.network_send(msg, sender_addr)

        custody_msg = make_custody_ack_msg(state.bundle_id, [])
        self.network_send(custody_msg, sender_addr)