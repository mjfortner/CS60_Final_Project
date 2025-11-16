import socket
import json
import struct
import threading
import time
import logging
from typing import Dict, Any, Callable, Tuple, Optional
from .protocol import *

logger = logging.getLogger(__name__)

class NetworkIO:
    def __init__(self, port: int, message_handler: Callable[[Dict[str, Any], Tuple[str, int]], None]):
        self.port = port
        self.socket = None
        self.running = False
        self.message_handler = message_handler
        self.receive_thread = None
        
    def start(self) -> None:
        """Start the network IO layer."""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind(('0.0.0.0', self.port))
            self.socket.settimeout(0.1)  # Non-blocking receive with timeout
            
            self.running = True
            self.receive_thread = threading.Thread(target=self._receive_loop, daemon=True)
            self.receive_thread.start()
            
            logger.info(f"NetworkIO started on port {self.port}")
        except Exception as e:
            logger.error(f"Failed to start NetworkIO: {e}")
            raise
    
    def stop(self) -> None:
        """Stop the network IO layer."""
        self.running = False
        if self.receive_thread:
            self.receive_thread.join(timeout=1.0)
        if self.socket:
            self.socket.close()
        logger.info("NetworkIO stopped")
    
    def _receive_loop(self) -> None:
        """Main receive loop running in separate thread."""
        while self.running:
            try:
                data, addr = self.socket.recvfrom(1200)  # Max UDP payload size
                message = self._deserialize_message(data)
                if message:
                    self.message_handler(message, addr)
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:  # Only log if we're still supposed to be running
                    logger.error(f"Error in receive loop: {e}")
    
    def send_message(self, message: Dict[str, Any], dest_addr: Tuple[str, int]) -> bool:
        """Send a message to the destination address."""
        try:
            data = self._serialize_message(message)
            if len(data) > 1200:
                logger.warning(f"Message too large: {len(data)} bytes")
                return False
            
            bytes_sent = self.socket.sendto(data, dest_addr)
            logger.debug(f"Sent {bytes_sent} bytes to {dest_addr}")
            return True
        except Exception as e:
            logger.error(f"Failed to send message to {dest_addr}: {e}")
            return False
    
    def _serialize_message(self, message: Dict[str, Any]) -> bytes:
        """Serialize message to bytes for network transmission."""
        msg_type = message.get('msg_type', '')
        
        if msg_type == 'DATA':
            return self._serialize_data_message(message)
        elif msg_type == 'SACK':
            return self._serialize_sack_message(message)
        elif msg_type == 'CUSTODY_REQ':
            return self._serialize_custody_req_message(message)
        elif msg_type == 'CUSTODY_ACK':
            return self._serialize_custody_ack_message(message)
        elif msg_type == 'DELIVERED':
            return self._serialize_delivered_message(message)
        else:
            # Fallback to JSON for unknown message types
            return json.dumps(message).encode('utf-8')
    
    def _serialize_data_message(self, message: Dict[str, Any]) -> bytes:
        """Serialize DATA message with binary header for efficiency."""
        # Fixed-size header: msg_type(1) + bundle_id(16) + chunk_id(4) + total_chunks(4) + 
        # block_id(4) + k(2) + r(2) + checksum(4) + flags(1) + payload_len(2) = 40 bytes
        header = struct.pack('!B16sIIIHHIBH', 
                           1,  # DATA = 1
                           message['bundle_id'][:16].encode('utf-8').ljust(16, b'\0'),
                           message['chunk_id'],
                           message['total_chunks'], 
                           message['block_id'],
                           message['k'],
                           message['r'],
                           int(message['checksum'], 16) if isinstance(message['checksum'], str) else message['checksum'],
                           0,  # flags (reserved)
                           len(message['payload']))
        
        # Payload as bytes
        if isinstance(message['payload'], str):
            payload = message['payload'].encode('utf-8')
        else:
            payload = message['payload']
        
        return header + payload
    
    def _serialize_sack_message(self, message: Dict[str, Any]) -> bytes:
        """Serialize SACK message."""
        header = struct.pack('!B16sH',
                           2,  # SACK = 2
                           message['bundle_id'][:16].encode('utf-8').ljust(16, b'\0'),
                           len(message['acked_chunks']))
        
        # Pack acked chunks as list of chunk IDs
        acked_data = b''
        for chunk_id in message['acked_chunks']:
            acked_data += struct.pack('!I', chunk_id)
        
        return header + acked_data
    
    def _serialize_custody_req_message(self, message: Dict[str, Any]) -> bytes:
        """Serialize CUSTODY_REQ message."""
        ranges_data = json.dumps(message['ranges']).encode('utf-8')
        header = struct.pack('!B16sIH',
                           3,  # CUSTODY_REQ = 3
                           message['bundle_id'][:16].encode('utf-8').ljust(16, b'\0'),
                           message['ttl_remaining'],
                           len(ranges_data))
        
        return header + ranges_data
    
    def _serialize_custody_ack_message(self, message: Dict[str, Any]) -> bytes:
        """Serialize CUSTODY_ACK message."""
        ranges_data = json.dumps(message['ranges']).encode('utf-8')
        header = struct.pack('!B16sH',
                           4,  # CUSTODY_ACK = 4
                           message['bundle_id'][:16].encode('utf-8').ljust(16, b'\0'),
                           len(ranges_data))
        
        return header + ranges_data
    
    def _serialize_delivered_message(self, message: Dict[str, Any]) -> bytes:
        """Serialize DELIVERED message."""
        return struct.pack('!B16s',
                         5,  # DELIVERED = 5
                         message['bundle_id'][:16].encode('utf-8').ljust(16, b'\0'))
    
    def _deserialize_message(self, data: bytes) -> Optional[Dict[str, Any]]:
        """Deserialize bytes back to message dictionary."""
        if len(data) < 1:
            return None
        
        msg_type = struct.unpack('!B', data[:1])[0]
        
        try:
            if msg_type == 1:  # DATA
                return self._deserialize_data_message(data)
            elif msg_type == 2:  # SACK
                return self._deserialize_sack_message(data)
            elif msg_type == 3:  # CUSTODY_REQ
                return self._deserialize_custody_req_message(data)
            elif msg_type == 4:  # CUSTODY_ACK
                return self._deserialize_custody_ack_message(data)
            elif msg_type == 5:  # DELIVERED
                return self._deserialize_delivered_message(data)
            else:
                # Try JSON fallback
                return json.loads(data.decode('utf-8'))
        except Exception as e:
            logger.error(f"Failed to deserialize message: {e}")
            return None
    
    def _deserialize_data_message(self, data: bytes) -> Dict[str, Any]:
        """Deserialize DATA message."""
        header = struct.unpack('!B16sIIIHHIBH', data[:40])
        bundle_id = header[1].rstrip(b'\0').decode('utf-8')
        payload_len = header[9]
        payload = data[40:40+payload_len]
        
        return {
            'msg_type': 'DATA',
            'bundle_id': bundle_id,
            'chunk_id': header[2],
            'total_chunks': header[3],
            'block_id': header[4],
            'k': header[5],
            'r': header[6],
            'checksum': header[7],
            'payload': payload
        }
    
    def _deserialize_sack_message(self, data: bytes) -> Dict[str, Any]:
        """Deserialize SACK message."""
        header = struct.unpack('!B16sH', data[:19])
        bundle_id = header[1].rstrip(b'\0').decode('utf-8')
        num_chunks = header[2]
        
        acked_chunks = []
        offset = 19
        for i in range(num_chunks):
            chunk_id = struct.unpack('!I', data[offset:offset+4])[0]
            acked_chunks.append(chunk_id)
            offset += 4
        
        return {
            'msg_type': 'SACK',
            'bundle_id': bundle_id,
            'acked_chunks': acked_chunks
        }
    
    def _deserialize_custody_req_message(self, data: bytes) -> Dict[str, Any]:
        """Deserialize CUSTODY_REQ message."""
        header = struct.unpack('!B16sIH', data[:23])
        bundle_id = header[1].rstrip(b'\0').decode('utf-8')
        ttl_remaining = header[2]
        ranges_len = header[3]
        ranges_data = data[23:23+ranges_len]
        ranges = json.loads(ranges_data.decode('utf-8'))
        
        return {
            'msg_type': 'CUSTODY_REQ',
            'bundle_id': bundle_id,
            'ttl_remaining': ttl_remaining,
            'ranges': ranges
        }
    
    def _deserialize_custody_ack_message(self, data: bytes) -> Dict[str, Any]:
        """Deserialize CUSTODY_ACK message."""
        header = struct.unpack('!B16sH', data[:19])
        bundle_id = header[1].rstrip(b'\0').decode('utf-8')
        ranges_len = header[2]
        ranges_data = data[19:19+ranges_len]
        ranges = json.loads(ranges_data.decode('utf-8'))
        
        return {
            'msg_type': 'CUSTODY_ACK',
            'bundle_id': bundle_id,
            'ranges': ranges
        }
    
    def _deserialize_delivered_message(self, data: bytes) -> Dict[str, Any]:
        """Deserialize DELIVERED message."""
        header = struct.unpack('!B16s', data[:17])
        bundle_id = header[1].rstrip(b'\0').decode('utf-8')
        
        return {
            'msg_type': 'DELIVERED',
            'bundle_id': bundle_id
        }