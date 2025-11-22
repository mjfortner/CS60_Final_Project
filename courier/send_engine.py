import os
import time
import uuid
import zlib
import logging
from typing import Dict, Set, List, Tuple, Optional, Any
from dataclasses import dataclass, field

from .config import TransferConfig, FECConfig
from .storage import SQLiteStorage
from .protocol import make_data_msg

logger = logging.getLogger(__name__)


@dataclass
class SendState:
    bundle_id: str
    window_start: int = 0
    window_end: int = 0
    window_size: int = 64
    acked_chunks: Set[int] = field(default_factory=set)
    retransmit_queue: List[int] = field(default_factory=list)
    chunk_timers: Dict[int, float] = field(default_factory=dict)
    send_timestamps: Dict[int, float] = field(default_factory=dict) 
    retransmitted_chunks: Set[int] = field(default_factory=set) 
    timeout_interval: float = 250.0  # ms 
    smoothed_rtt: float = 300.0      # ms
    rtt_variance: float = 150.0      # ms
    total_chunks: int = 0
    bytes_sent: int = 0
    chunks_retransmitted: int = 0
    completed: bool = False

    srtt: float = 0.0         
    rttvar: float = 0.0       
    rto: float = 300.0        
    
    send_times: Dict[int, float] = field(default_factory=dict) 
    retransmitted_chunks: Set[int] = field(default_factory=set)


class SendEngine:
    """
    Send path: chunking, sliding window, SACK handling, and retransmissions.
    """

    def __init__(
        self,
        config: TransferConfig,
        fec_config: FECConfig,
        storage: SQLiteStorage,
        network_send_func,
        src_node_id: str = "local",
    ):
        self.config = config
        self.fec_config = fec_config
        self.storage = storage
        self.network_send = network_send_func
        self.src_node_id = src_node_id
        self.active_sends: Dict[str, SendState] = {}
        self._chunk_cache: Dict[str, Dict[int, Dict[str, Any]]] = {} 
        self._last_send_ms: float | None = None

    # ------------------------------------------------------------------

    def send_file(
        self,
        file_path: str,
        destination: str,
        dest_addr: Tuple[str, int],
        fec_enabled: Optional[bool] = None,
    ) -> str:
        """Start sending a file to destination nodeID at dest_addr."""
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        use_fec = bool(fec_enabled and self.fec_config.enabled)

        bundle_id = uuid.uuid4().hex[:16]

        chunks = self._create_chunks(file_path, bundle_id, use_fec)
        self._chunk_cache[bundle_id] = {c["chunk_id"]: c for c in chunks}

        bundle_data = {
            "bundle_id": bundle_id,
            "src": self.src_node_id,
            "dst": destination,
            "ttl": self.config.ttl_sec,
            "state": "sending",
            "total_chunks": len(chunks),
            "file_path": file_path,
            "file_size": os.path.getsize(file_path),
            "fec_enabled": use_fec,
            "k": self.fec_config.k if use_fec else 0,
            "r": self.fec_config.r if use_fec else 0,
        }
        self.storage.save_bundle(bundle_data)

        for chunk in chunks:
            self.storage.save_chunk(chunk)

        send_state = SendState(
            bundle_id=bundle_id,
            window_size=self.config.window_size,
            timeout_interval=self.config.base_rto_ms,
            total_chunks=len(chunks),
        )
        send_state.window_end = min(send_state.window_size, send_state.total_chunks)
        self.active_sends[bundle_id] = send_state

        self._send_window(bundle_id, dest_addr)

        logger.info(
            "Started sending file %s as bundle %s to %s",
            file_path,
            bundle_id,
            destination,
        )
        return bundle_id

    def _update_rto(self, state: SendState, measured_rtt: float):
        """Update RTO using RFC 6298 standard algorithm."""
        if state.srtt == 0.0:
            state.srtt = measured_rtt
            state.rttvar = measured_rtt / 2
        else:
            alpha = 0.125
            beta = 0.25
            state.rttvar = (1 - beta) * state.rttvar + beta * abs(state.srtt - measured_rtt)
            state.srtt = (1 - alpha) * state.srtt + alpha * measured_rtt

        state.rto = state.srtt + 4 * state.rttvar
        
        state.rto = max(100.0, min(state.rto, 60000.0))
        state.timeout_interval = state.rto
    # ------------------------------------------------------------------

    def _create_chunks(
        self,
        file_path: str,
        bundle_id: str,
        fec_enabled: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Split file into chunks and optionally apply FEC.

        Data chunks get contiguous chunk_ids starting at 0.
        Parity chunks, if any, are appended afterward.
        """
        chunks: List[Dict[str, Any]] = []

        with open(file_path, "rb") as f:
            file_data = f.read()

        chunk_size = self.config.chunk_size
        num_data_chunks = (len(file_data) + chunk_size - 1) // chunk_size

        for i in range(num_data_chunks):
            start = i * chunk_size
            end = min(start + chunk_size, len(file_data))
            payload = file_data[start:end]

            chunk = {
                "bundle_id": bundle_id,
                "chunk_id": i,
                "is_parity": False,
                "block_id": i // self.fec_config.k if fec_enabled and self.fec_config.k > 0 else 0,
                "k": self.fec_config.k if fec_enabled else 0,
                "r": self.fec_config.r if fec_enabled else 0,
                "payload": payload,
                "checksum": zlib.crc32(payload) & 0xFFFFFFFF,
                "flags": 0,
            }
            chunks.append(chunk)

        if fec_enabled and self.fec_config.enabled and self.fec_config.k > 0 and self.fec_config.r > 0:
            chunks.extend(self._generate_fec_chunks(chunks, bundle_id))

        return chunks

    def _generate_fec_chunks(
        self,
        data_chunks: List[Dict[str, Any]],
        bundle_id: str,
    ) -> List[Dict[str, Any]]:
        """
        Generate FEC parity chunks using XOR.

        For simplicity we generate r copies of the same parity stripe,
        so true diversity only exists when r == 1. It still lets the
        receiver reconstruct from any k of (k + r) when at most one chunk
        is missing per block.
        """
        parity_chunks: List[Dict[str, Any]] = []
        k = self.fec_config.k
        r = self.fec_config.r

        if k <= 0 or r <= 0:
            return parity_chunks

        num_data_chunks = len(data_chunks)
        for block_start in range(0, num_data_chunks, k):
            block_chunks = data_chunks[block_start : block_start + k]
            block_id = block_start // k

            if not block_chunks:
                continue

            parity_data = self._xor_chunks(block_chunks)

            for parity_idx in range(r):
                chunk_id = num_data_chunks + block_id * r + parity_idx
                parity_chunk = {
                    "bundle_id": bundle_id,
                    "chunk_id": chunk_id,
                    "is_parity": True,
                    "block_id": block_id,
                    "k": k,
                    "r": r,
                    "payload": parity_data,
                    "checksum": zlib.crc32(parity_data) & 0xFFFFFFFF,
                    "flags": 0,
                }
                parity_chunks.append(parity_chunk)

        return parity_chunks

    def _xor_chunks(self, chunks: List[Dict[str, Any]]) -> bytes:
        """XOR data chunk payloads together for FEC parity generation."""
        if not chunks:
            return b""

        result = bytearray(chunks[0]["payload"])

        for chunk in chunks[1:]:
            payload = chunk["payload"]
            limit = min(len(result), len(payload))
            for i in range(limit):
                result[i] ^= payload[i]
            if len(payload) > len(result):
                result.extend(payload[len(result) :])

        return bytes(result)

    # ------------------------------------------------------------------

    def _send_window(self, bundle_id: str, dest_addr: Tuple[str, int]) -> None:
        """
        Send chunks in the current window.
        Optimized: Uses in-memory cache and removes sleep pacing.
        """
        send_state = self.active_sends.get(bundle_id)
        if not send_state:
            return

        chunk_dict = self._chunk_cache.get(bundle_id)
        if not chunk_dict:
            chunks = self.storage.load_chunks_for_bundle(bundle_id)
            chunk_dict = {c["chunk_id"]: c for c in chunks}
            self._chunk_cache[bundle_id] = chunk_dict

        current_time = time.time() * 1000.0  # ms
        chunks_sent_batch = 0
        for chunk_id in range(send_state.window_start, send_state.window_end):
            if chunk_id in send_state.acked_chunks:
                continue
            
            if chunk_id in send_state.chunk_timers:
                if current_time < send_state.chunk_timers[chunk_id]:
                    continue

            if chunk_id not in chunk_dict:
                continue

            chunk = chunk_dict[chunk_id]
            msg = make_data_msg(
                bundle_id=chunk["bundle_id"],
                chunk_id=chunk["chunk_id"],
                total_chunks=send_state.total_chunks,
                block_id=chunk["block_id"],
                k=chunk["k"],
                r=chunk["r"],
                checksum=chunk["checksum"],
                payload=chunk["payload"],
            )

            if self.network_send(msg, dest_addr):
                send_state.chunk_timers[chunk_id] = current_time + send_state.timeout_interval
                send_state.send_timestamps[chunk_id] = current_time
                send_state.bytes_sent += len(chunk["payload"])
                logger.debug("Sent chunk %d of bundle %s", chunk_id, bundle_id)
                chunks_sent_batch += 1
                if chunks_sent_batch % 10 == 0:
                    time.sleep(0.001)
    # ------------------------------------------------------------------

    def handle_sack(self, msg: Dict[str, Any], sender_addr: Tuple[str, int]) -> None:
        """Handle SACK message from receiver."""
        bundle_id = msg["bundle_id"]
        send_state = self.active_sends.get(bundle_id)
        if not send_state:
            logger.warning("Received SACK for unknown bundle %s", bundle_id)
            return

        acked_chunks = set(msg.get("acked_chunks", []))
        newly_acked = acked_chunks - send_state.acked_chunks

        if newly_acked:
            logger.debug("Bundle %s: newly acked chunks %s", bundle_id, newly_acked)

        send_state.acked_chunks.update(acked_chunks)

        current_time = time.time() * 1000.0
        
        for chunk_id in newly_acked:
            if chunk_id in send_state.send_timestamps:
                if chunk_id not in send_state.retransmitted_chunks:
                    rtt_sample = current_time - send_state.send_timestamps[chunk_id]
                    self._update_rtt_estimates(send_state, rtt_sample)
            
            send_state.chunk_timers.pop(chunk_id, None)
            send_state.send_timestamps.pop(chunk_id, None)
            send_state.retransmitted_chunks.discard(chunk_id)

        if newly_acked:
             send_state.timeout_interval = max(
                send_state.smoothed_rtt + 4.0 * send_state.rtt_variance,
                self.config.base_rto_ms
            )

        while (
            send_state.window_start < send_state.total_chunks
            and send_state.window_start in send_state.acked_chunks
        ):
            send_state.window_start += 1

        old_window_end = send_state.window_end
        send_state.window_end = min(
            send_state.window_start + send_state.window_size,
            send_state.total_chunks,
        )

        if len(send_state.acked_chunks) >= send_state.total_chunks:
            self._complete_transfer(bundle_id)
            return

        if send_state.window_end > old_window_end:
            self._send_window(bundle_id, sender_addr)

    # ------------------------------------------------------------------

    def handle_delivered(self, msg: Dict[str, Any]) -> None:
        """Handle DELIVERED message - transfer complete."""
        bundle_id = msg["bundle_id"]
        if bundle_id in self.active_sends:
            self._complete_transfer(bundle_id)

    # ------------------------------------------------------------------

    def _update_rtt_estimates(self, send_state: SendState, rtt: float) -> None:
        """Update RTT estimates using RFC 6298 (TCP standard)."""
        if send_state.smoothed_rtt == 0.0 or send_state.smoothed_rtt == 300.0:  # First measurement
            send_state.smoothed_rtt = rtt
            send_state.rtt_variance = rtt / 2.0
        else:
            send_state.rtt_variance = 0.75 * send_state.rtt_variance + 0.25 * abs(
                send_state.smoothed_rtt - rtt
            )
            send_state.smoothed_rtt = 0.875 * send_state.smoothed_rtt + 0.125 * rtt

        rto = send_state.smoothed_rtt + 4.0 * send_state.rtt_variance

        send_state.timeout_interval = max(100.0, min(rto, self.config.max_rto_ms))

    def _complete_transfer(self, bundle_id: str) -> None:
        """Mark transfer as complete and clean up."""
        send_state = self.active_sends.get(bundle_id)
        if not send_state:
            return

        if not send_state.completed:
            send_state.completed = True
            self.storage.update_bundle_state(bundle_id, "delivered")
            self.storage.update_bundle_stats(
                bundle_id,
                bytes_sent=send_state.bytes_sent,
                chunks_retransmitted=send_state.chunks_retransmitted,
            )
            
            self._chunk_cache.pop(bundle_id, None)
            
            logger.info("Transfer completed for bundle %s", bundle_id)

    # ------------------------------------------------------------------

    def check_timeouts(self) -> None:
        """Check for timed out chunks and enqueue them for retransmission."""
        current_time = time.time() * 1000.0  # ms

        for bundle_id, send_state in list(self.active_sends.items()):
            if send_state.completed:
                continue

            timed_out_chunks = []
            for chunk_id, expiry_time in list(send_state.chunk_timers.items()):
                if chunk_id in send_state.acked_chunks:
                    del send_state.chunk_timers[chunk_id]
                    continue
                
                if current_time >= expiry_time:
                    timed_out_chunks.append(chunk_id)

            if not timed_out_chunks:
                continue

            logger.debug(
                "Bundle %s: chunks timed out %s", bundle_id, timed_out_chunks
            )

            for chunk_id in timed_out_chunks:
                send_state.retransmit_queue.append(chunk_id)
                send_state.chunk_timers.pop(chunk_id, None)

            send_state.timeout_interval = min(
                send_state.timeout_interval * 2.0,
                self.config.max_rto_ms,
            )
            send_state.chunks_retransmitted += len(timed_out_chunks)


    def retransmit_chunks(self, bundle_id: str, dest_addr: Tuple[str, int]) -> None:
        """Retransmit chunks in the retransmit queue."""
        send_state = self.active_sends.get(bundle_id)
        if not send_state or send_state.completed:
            return

        chunks = self.storage.load_chunks_for_bundle(bundle_id)
        chunk_dict = {chunk["chunk_id"]: chunk for chunk in chunks}

        current_time = time.time() * 1000.0  # ms
        
        chunks_sent_batch = 0

        while send_state.retransmit_queue:
            chunk_id = send_state.retransmit_queue.pop(0)

            if chunk_id in send_state.acked_chunks:
                continue
            if chunk_id not in chunk_dict:
                continue

            chunk = chunk_dict[chunk_id]
            msg = make_data_msg(
                bundle_id=chunk["bundle_id"],
                chunk_id=chunk["chunk_id"],
                total_chunks=send_state.total_chunks,
                block_id=chunk["block_id"],
                k=chunk["k"],
                r=chunk["r"],
                checksum=chunk["checksum"],
                payload=chunk["payload"],
            )

            if self.network_send(msg, dest_addr):
                send_state.chunk_timers[chunk_id] = current_time + send_state.timeout_interval
                send_state.retransmitted_chunks.add(chunk_id)
                logger.debug("Retransmitted chunk %d of bundle %s", chunk_id, bundle_id)
                
                chunks_sent_batch += 1
                if chunks_sent_batch % 10 == 0:
                    time.sleep(0.001)
                    
    # ------------------------------------------------------------------

    def get_send_status(self, bundle_id: str) -> Optional[Dict[str, Any]]:
        """Get status of a send operation, if it is currently active."""
        send_state = self.active_sends.get(bundle_id)
        if not send_state:
            return None

        progress = (
            len(send_state.acked_chunks) / send_state.total_chunks
            if send_state.total_chunks > 0
            else 0.0
        )

        return {
            "bundle_id": bundle_id,
            "total_chunks": send_state.total_chunks,
            "acked_chunks": len(send_state.acked_chunks),
            "progress": progress,
            "bytes_sent": send_state.bytes_sent,
            "chunks_retransmitted": send_state.chunks_retransmitted,
            "completed": send_state.completed,
            "window_start": send_state.window_start,
            "window_end": send_state.window_end,
            "timeout_interval": send_state.timeout_interval,
            "smoothed_rtt": send_state.smoothed_rtt,
        }

    # ------------------------------------------------------------------

    def cleanup_completed_transfers(self) -> None:
        """Remove completed transfers from memory."""
        completed_bundles = [
            bundle_id
            for bundle_id, send_state in self.active_sends.items()
            if send_state.completed
        ]
        for bundle_id in completed_bundles:
            self.active_sends.pop(bundle_id, None)
            logger.debug("Cleaned up completed transfer %s", bundle_id)

    # ------------------------------------------------------------------

    def resume_transfers(self) -> None:
        """
        Resume transfers from storage after restart.

        Currently, we recreate SendState for bundles in 'sending' state,
        starting their window from 0 again.
        """
        sending_bundles = self.storage.list_bundles_by_state("sending")

        for bundle_data in sending_bundles:
            bundle_id = bundle_data["bundle_id"]
            send_state = SendState(
                bundle_id=bundle_id,
                window_size=self.config.window_size,
                timeout_interval=self.config.base_rto_ms,
                total_chunks=bundle_data["total_chunks"],
                bytes_sent=bundle_data.get("bytes_sent", 0),
                chunks_retransmitted=bundle_data.get(
                    "chunks_retransmitted", 0
                ),
            )
            send_state.window_end = min(
                send_state.window_size, send_state.total_chunks
            )
            self.active_sends[bundle_id] = send_state
            logger.info("Resumed transfer for bundle %s", bundle_id)