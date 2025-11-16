import os
import time
import uuid
import zlib
import logging
from typing import Dict, Set, List, Tuple, Optional, Any
from dataclasses import dataclass, field
from .config import TransferConfig, FECConfig
from .storage import SQLiteStorage
from .protocol import make_data_msg, make_custody_req_msg

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
    timeout_interval: float = 50.0  # ms
    smoothed_rtt: float = 50.0  # ms
    rtt_variance: float = 25.0  # ms
    total_chunks: int = 0
    bytes_sent: int = 0
    chunks_retransmitted: int = 0
    completed: bool = False

class SendEngine:
    def __init__(self, config: TransferConfig, fec_config: FECConfig, 
                 storage: SQLiteStorage, network_send_func):
        self.config = config
        self.fec_config = fec_config
        self.storage = storage
        self.network_send = network_send_func
        self.active_sends: Dict[str, SendState] = {}
        
    def send_file(self, file_path: str, destination: str, dest_addr: Tuple[str, int],
                  fec_enabled: bool = None) -> str:
        """Start sending a file to destination."""
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")
        
        # Generate bundle ID
        bundle_id = str(uuid.uuid4())
        
        # Read and chunk the file
        chunks = self._create_chunks(file_path, bundle_id, fec_enabled)
        
        # Create bundle record
        bundle_data = {
            'bundle_id': bundle_id,
            'src': 'localhost',  # Will be set properly by Node
            'dst': destination,
            'ttl': self.config.ttl_sec,
            'state': 'sending',
            'total_chunks': len(chunks),
            'file_path': file_path,
            'file_size': os.path.getsize(file_path),
            'fec_enabled': fec_enabled or False,
            'k': self.fec_config.k if fec_enabled else 0,
            'r': self.fec_config.r if fec_enabled else 0
        }
        self.storage.save_bundle(bundle_data)
        
        # Save chunks to storage
        for chunk in chunks:
            self.storage.save_chunk(chunk)
        
        # Initialize send state
        send_state = SendState(
            bundle_id=bundle_id,
            window_size=self.config.window_size,
            timeout_interval=self.config.base_rto_ms,
            total_chunks=len(chunks)
        )
        send_state.window_end = min(send_state.window_size, send_state.total_chunks)
        self.active_sends[bundle_id] = send_state
        
        # Start sending initial window
        self._send_window(bundle_id, dest_addr)
        
        logger.info(f"Started sending file {file_path} as bundle {bundle_id} to {destination}")
        return bundle_id
    
    def _create_chunks(self, file_path: str, bundle_id: str, fec_enabled: bool = None) -> List[Dict[str, Any]]:
        """Split file into chunks and optionally apply FEC."""
        chunks = []
        
        with open(file_path, 'rb') as f:
            file_data = f.read()
        
        chunk_size = self.config.chunk_size
        num_data_chunks = (len(file_data) + chunk_size - 1) // chunk_size
        
        # Create data chunks
        for i in range(num_data_chunks):
            start = i * chunk_size
            end = min(start + chunk_size, len(file_data))
            payload = file_data[start:end]
            
            chunk = {
                'bundle_id': bundle_id,
                'chunk_id': i,
                'is_parity': False,
                'block_id': i // self.fec_config.k if fec_enabled else 0,
                'k': self.fec_config.k if fec_enabled else 0,
                'r': self.fec_config.r if fec_enabled else 0,
                'payload': payload,
                'checksum': zlib.crc32(payload) & 0xffffffff,
                'flags': 0
            }
            chunks.append(chunk)
        
        # Add FEC parity chunks if enabled
        if fec_enabled and self.fec_config.enabled:
            chunks.extend(self._generate_fec_chunks(chunks, bundle_id))
        
        return chunks
    
    def _generate_fec_chunks(self, data_chunks: List[Dict[str, Any]], bundle_id: str) -> List[Dict[str, Any]]:
        """Generate FEC parity chunks using XOR."""
        parity_chunks = []
        k = self.fec_config.k
        r = self.fec_config.r
        
        # Group chunks into blocks of k
        for block_start in range(0, len(data_chunks), k):
            block_chunks = data_chunks[block_start:block_start + k]
            block_id = block_start // k
            
            # Generate r parity chunks for this block
            for parity_idx in range(r):
                parity_data = self._xor_chunks(block_chunks, parity_idx)
                
                parity_chunk = {
                    'bundle_id': bundle_id,
                    'chunk_id': len(data_chunks) + (block_id * r) + parity_idx,
                    'is_parity': True,
                    'block_id': block_id,
                    'k': k,
                    'r': r,
                    'payload': parity_data,
                    'checksum': zlib.crc32(parity_data) & 0xffffffff,
                    'flags': 0
                }
                parity_chunks.append(parity_chunk)
        
        return parity_chunks
    
    def _xor_chunks(self, chunks: List[Dict[str, Any]], parity_index: int) -> bytes:
        """XOR chunks together for FEC parity generation."""
        if not chunks:
            return b''
        
        # Start with first chunk
        result = bytearray(chunks[0]['payload'])
        
        # XOR with remaining chunks
        for chunk in chunks[1:]:
            payload = chunk['payload']
            for i in range(min(len(result), len(payload))):
                result[i] ^= payload[i]
            
            # Extend if this chunk is longer
            if len(payload) > len(result):
                result.extend(payload[len(result):])
        
        return bytes(result)
    
    def _send_window(self, bundle_id: str, dest_addr: Tuple[str, int]) -> None:
        """Send chunks in the current window."""
        send_state = self.active_sends.get(bundle_id)
        if not send_state:
            return
        
        # Load chunks from storage
        chunks = self.storage.load_chunks_for_bundle(bundle_id)
        chunk_dict = {chunk['chunk_id']: chunk for chunk in chunks}
        
        current_time = time.time() * 1000  # ms
        
        # Send chunks in window that aren't already acked
        for chunk_id in range(send_state.window_start, send_state.window_end):
            if chunk_id not in send_state.acked_chunks and chunk_id in chunk_dict:
                chunk = chunk_dict[chunk_id]
                
                # Create DATA message
                msg = make_data_msg(
                    bundle_id=chunk['bundle_id'],
                    chunk_id=chunk['chunk_id'],
                    total_chunks=send_state.total_chunks,
                    block_id=chunk['block_id'],
                    k=chunk['k'],
                    r=chunk['r'],
                    checksum=chunk['checksum'],
                    payload_str=chunk['payload']
                )
                
                # Send the message
                if self.network_send(msg, dest_addr):
                    # Set timer for this chunk
                    send_state.chunk_timers[chunk_id] = current_time + send_state.timeout_interval
                    send_state.bytes_sent += len(chunk['payload'])
                    logger.debug(f"Sent chunk {chunk_id} of bundle {bundle_id}")
    
    def handle_sack(self, msg: Dict[str, Any], sender_addr: Tuple[str, int]) -> None:
        """Handle SACK message from receiver."""
        bundle_id = msg['bundle_id']
        send_state = self.active_sends.get(bundle_id)
        if not send_state:
            logger.warning(f"Received SACK for unknown bundle {bundle_id}")
            return
        
        # Parse acked chunks
        acked_chunks = set(msg['acked_chunks'])
        newly_acked = acked_chunks - send_state.acked_chunks
        
        if newly_acked:
            logger.debug(f"Bundle {bundle_id}: newly acked chunks {newly_acked}")
        
        # Update acked chunks
        send_state.acked_chunks.update(acked_chunks)
        
        # Remove timers for acked chunks
        for chunk_id in newly_acked:
            if chunk_id in send_state.chunk_timers:
                # Calculate RTT and update estimates
                current_time = time.time() * 1000
                rtt = current_time - (send_state.chunk_timers[chunk_id] - send_state.timeout_interval)
                self._update_rtt_estimates(send_state, rtt)
                
                del send_state.chunk_timers[chunk_id]
        
        # Advance window start
        while (send_state.window_start < send_state.total_chunks and 
               send_state.window_start in send_state.acked_chunks):
            send_state.window_start += 1
        
        # Advance window end
        old_window_end = send_state.window_end
        send_state.window_end = min(
            send_state.window_start + send_state.window_size,
            send_state.total_chunks
        )
        
        # Check if transfer is complete
        if len(send_state.acked_chunks) >= send_state.total_chunks:
            self._complete_transfer(bundle_id)
            return
        
        # Send new chunks if window expanded
        if send_state.window_end > old_window_end:
            self._send_window(bundle_id, sender_addr)
    
    def handle_delivered(self, msg: Dict[str, Any]) -> None:
        """Handle DELIVERED message - transfer complete."""
        bundle_id = msg['bundle_id']
        if bundle_id in self.active_sends:
            self._complete_transfer(bundle_id)
    
    def _update_rtt_estimates(self, send_state: SendState, rtt: float) -> None:
        """Update RTT estimates using RFC 6298 algorithm."""
        if send_state.smoothed_rtt == 50.0:  # First measurement
            send_state.smoothed_rtt = rtt
            send_state.rtt_variance = rtt / 2
        else:
            # Update variance first
            send_state.rtt_variance = (0.75 * send_state.rtt_variance + 
                                     0.25 * abs(send_state.smoothed_rtt - rtt))
            # Update smoothed RTT
            send_state.smoothed_rtt = 0.875 * send_state.smoothed_rtt + 0.125 * rtt
        
        # Update timeout interval
        send_state.timeout_interval = max(
            send_state.smoothed_rtt + 4 * send_state.rtt_variance,
            50.0  # Minimum 50ms
        )
        send_state.timeout_interval = min(send_state.timeout_interval, self.config.max_rto_ms)
    
    def _complete_transfer(self, bundle_id: str) -> None:
        """Mark transfer as complete and clean up."""
        send_state = self.active_sends.get(bundle_id)
        if send_state:
            send_state.completed = True
            self.storage.update_bundle_state(bundle_id, 'delivered')
            self.storage.update_bundle_stats(
                bundle_id, 
                send_state.bytes_sent,
                send_state.chunks_retransmitted
            )
            logger.info(f"Transfer completed for bundle {bundle_id}")
    
    def check_timeouts(self) -> None:
        """Check for timed out chunks and retransmit."""
        current_time = time.time() * 1000  # ms
        
        for bundle_id, send_state in self.active_sends.items():
            if send_state.completed:
                continue
            
            timed_out_chunks = []
            for chunk_id, expiry_time in send_state.chunk_timers.items():
                if current_time >= expiry_time and chunk_id not in send_state.acked_chunks:
                    timed_out_chunks.append(chunk_id)
            
            if timed_out_chunks:
                logger.debug(f"Bundle {bundle_id}: chunks timed out {timed_out_chunks}")
                
                # Add to retransmit queue
                send_state.retransmit_queue.extend(timed_out_chunks)
                
                # Remove from timers
                for chunk_id in timed_out_chunks:
                    if chunk_id in send_state.chunk_timers:
                        del send_state.chunk_timers[chunk_id]
                
                # Exponential backoff
                send_state.timeout_interval = min(
                    send_state.timeout_interval * 1.5,
                    self.config.max_rto_ms
                )
                
                send_state.chunks_retransmitted += len(timed_out_chunks)
    
    def flush_retransmit_queues(self) -> None:
        """Process retransmit queues for all active transfers."""
        for bundle_id, send_state in self.active_sends.items():
            if send_state.completed or not send_state.retransmit_queue:
                continue
            
            # Find destination address (would need to be tracked)
            # For now, we'll need the Node to call retransmit with dest_addr
            pass
    
    def retransmit_chunks(self, bundle_id: str, dest_addr: Tuple[str, int]) -> None:
        """Retransmit chunks in the retransmit queue."""
        send_state = self.active_sends.get(bundle_id)
        if not send_state or send_state.completed:
            return
        
        chunks = self.storage.load_chunks_for_bundle(bundle_id)
        chunk_dict = {chunk['chunk_id']: chunk for chunk in chunks}
        
        current_time = time.time() * 1000  # ms
        
        # Retransmit chunks in queue
        chunks_to_retransmit = send_state.retransmit_queue[:]
        send_state.retransmit_queue.clear()
        
        for chunk_id in chunks_to_retransmit:
            if chunk_id in send_state.acked_chunks:
                continue  # Already acked, skip
            
            if chunk_id in chunk_dict:
                chunk = chunk_dict[chunk_id]
                
                msg = make_data_msg(
                    bundle_id=chunk['bundle_id'],
                    chunk_id=chunk['chunk_id'],
                    total_chunks=send_state.total_chunks,
                    block_id=chunk['block_id'],
                    k=chunk['k'],
                    r=chunk['r'],
                    checksum=chunk['checksum'],
                    payload_str=chunk['payload']
                )
                
                if self.network_send(msg, dest_addr):
                    send_state.chunk_timers[chunk_id] = current_time + send_state.timeout_interval
                    logger.debug(f"Retransmitted chunk {chunk_id} of bundle {bundle_id}")
    
    def get_send_status(self, bundle_id: str) -> Optional[Dict[str, Any]]:
        """Get status of a send operation."""
        send_state = self.active_sends.get(bundle_id)
        if not send_state:
            return None
        
        progress = len(send_state.acked_chunks) / send_state.total_chunks if send_state.total_chunks > 0 else 0
        
        return {
            'bundle_id': bundle_id,
            'total_chunks': send_state.total_chunks,
            'acked_chunks': len(send_state.acked_chunks),
            'progress': progress,
            'bytes_sent': send_state.bytes_sent,
            'chunks_retransmitted': send_state.chunks_retransmitted,
            'completed': send_state.completed,
            'window_start': send_state.window_start,
            'window_end': send_state.window_end,
            'timeout_interval': send_state.timeout_interval,
            'smoothed_rtt': send_state.smoothed_rtt
        }
    
    def cleanup_completed_transfers(self) -> None:
        """Remove completed transfers from memory."""
        completed_bundles = [
            bundle_id for bundle_id, send_state in self.active_sends.items()
            if send_state.completed
        ]
        
        for bundle_id in completed_bundles:
            del self.active_sends[bundle_id]
            logger.debug(f"Cleaned up completed transfer {bundle_id}")
    
    def resume_transfers(self) -> None:
        """Resume transfers from storage after restart."""
        sending_bundles = self.storage.list_bundles_by_state('sending')
        
        for bundle_data in sending_bundles:
            bundle_id = bundle_data['bundle_id']
            
            # Recreate send state
            send_state = SendState(
                bundle_id=bundle_id,
                window_size=self.config.window_size,
                timeout_interval=self.config.base_rto_ms,
                total_chunks=bundle_data['total_chunks'],
                bytes_sent=bundle_data.get('bytes_sent', 0),
                chunks_retransmitted=bundle_data.get('chunks_retransmitted', 0)
            )
            
            # TODO: Recover acked chunks from storage or restart from beginning
            send_state.window_end = min(send_state.window_size, send_state.total_chunks)
            
            self.active_sends[bundle_id] = send_state
            logger.info(f"Resumed transfer for bundle {bundle_id}")