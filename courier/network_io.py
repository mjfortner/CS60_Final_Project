import random
import socket
import json
import struct
import threading
import logging
from typing import Dict, Any, Callable, Tuple, Optional

from .protocol import MAX_SACK_WINDOW_BITS 

logger = logging.getLogger(__name__)


class NetworkIO:
    """
    Thin UDP I/O layer with binary serialization for core message types.
    """

    def __init__(
        self,
        port: int,
        message_handler: Callable[[Dict[str, Any], Tuple[str, int]], None],
    ):
        self.port = port
        self.socket: Optional[socket.socket] = None
        self.running = False
        self.message_handler = message_handler
        self.receive_thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the network IO layer."""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4 * 1024*1024)
            self.socket.bind(("0.0.0.0", self.port))
            self.socket.settimeout(0.1)  

            self.running = True
            self.receive_thread = threading.Thread(
                target=self._receive_loop, daemon=True
            )
            self.receive_thread.start()

            logger.info("NetworkIO started on port %d", self.port)
        except Exception as e:
            logger.error("Failed to start NetworkIO: %s", e)
            raise

    def stop(self) -> None:
        """Stop the network IO layer."""
        self.running = False
        if self.receive_thread:
            self.receive_thread.join(timeout=1.0)
        if self.socket:
            self.socket.close()
        logger.info("NetworkIO stopped")

    # ------------------------------------------------------------------

    def _receive_loop(self) -> None:
        """Main receive loop running in a separate thread."""
        while self.running:
            try:
                data, addr = self.socket.recvfrom(1200)  # Max UDP payload size
                message = self._deserialize_message(data)
                if message:
                    self.message_handler(message, addr)
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    logger.error("Error in receive loop: %s", e)

    # ------------------------------------------------------------------

    def send_message(self, message: Dict[str, Any], dest_addr: Tuple[str, int]) -> bool:
        """Send a message to the destination address."""
        try:

            data = self._serialize_message(message)
            if len(data) > 1200:
                logger.warning("Message too large: %d bytes", len(data))
                return False

            bytes_sent = self.socket.sendto(data, dest_addr) # type: ignore
            logger.debug("Sent %d bytes to %s", bytes_sent, dest_addr)
            return True
        
        except Exception as e:
            logger.error("Failed to send message to %s: %s", dest_addr, e)
            return False

    # ------------------------------------------------------------------

    def _serialize_message(self, message: Dict[str, Any]) -> bytes:
        """Serialize message to bytes for network transmission."""
        msg_type = message.get("msg_type", "")

        if msg_type == "DATA":
            return self._serialize_data_message(message)
        elif msg_type == "SACK":
            return self._serialize_sack_message(message)
        elif msg_type == "CUSTODY_REQ":
            return self._serialize_custody_req_message(message)
        elif msg_type == "CUSTODY_ACK":
            return self._serialize_custody_ack_message(message)
        elif msg_type == "DELIVERED":
            return self._serialize_delivered_message(message)
        else:
            return json.dumps(message).encode("utf-8")

    # ------------------------ DATA ------------------------------------

    def _serialize_data_message(self, message: Dict[str, Any]) -> bytes:
        """
        Serialize DATA message with binary header for efficiency.

        Header layout (network order):
          B   msg_type (1 for DATA)
          16s bundle_id (UTF-8, padded with NUL)
          I   chunk_id
          I   total_chunks
          I   block_id
          H   k
          H   r
          I   checksum
          B   flags (reserved)
          H   payload_len
        Total = 40 bytes header.
        """
        bundle_id_bytes = message["bundle_id"][:16].encode("utf-8").ljust(16, b"\0")
        checksum = int(message["checksum"])
        payload = message["payload"]
        if isinstance(payload, str):
            payload = payload.encode("utf-8")

        header = struct.pack(
            "!B16sIIIHHIBH",
            1,  # DATA = 1
            bundle_id_bytes,
            int(message["chunk_id"]),
            int(message["total_chunks"]),
            int(message["block_id"]),
            int(message["k"]),
            int(message["r"]),
            checksum,
            0,  
            len(payload),
        )
        return header + payload

    # ------------------------ SACK ------------------------------------

    def _serialize_sack_message(self, message: Dict[str, Any]) -> bytes:
        """
        Serialize SACK message.

        Header layout:
          B   msg_type (2)
          16s bundle_id
          I   recv_watermark (0xFFFFFFFF means 'none')
          H   bitmap_len (bytes)
        Followed by bitmap bytes (sack_bitmap).
        """
        bundle_id_bytes = message["bundle_id"][:16].encode("utf-8").ljust(16, b"\0")

        recv_watermark = int(message.get("recv_watermark", -1))
        if recv_watermark < 0:
            raw_watermark = 0xFFFFFFFF
        else:
            raw_watermark = recv_watermark

        bitmap = message.get("sack_bitmap", b"") or b""
        if len(bitmap) > (MAX_SACK_WINDOW_BITS // 8):
            bitmap = bitmap[: MAX_SACK_WINDOW_BITS // 8]

        header = struct.pack("!B16sIH", 2, bundle_id_bytes, raw_watermark, len(bitmap))
        return header + bitmap

    # ------------------------ CUSTODY_REQ ------------------------------

    def _serialize_custody_req_message(self, message: Dict[str, Any]) -> bytes:
        """
        Serialize CUSTODY_REQ message.

        Header layout:
          B   msg_type (3)
          16s bundle_id
          I   ttl_remaining (seconds)
          H   ranges_len (bytes)
        Followed by JSON-encoded ranges.
        """
        bundle_id_bytes = message["bundle_id"][:16].encode("utf-8").ljust(16, b"\0")
        ranges_data = json.dumps(message["ranges"]).encode("utf-8")

        header = struct.pack(
            "!B16sIH",
            3,
            bundle_id_bytes,
            int(message.get("ttl_remaining", 0)),
            len(ranges_data),
        )
        return header + ranges_data

    # ------------------------ CUSTODY_ACK ------------------------------

    def _serialize_custody_ack_message(self, message: Dict[str, Any]) -> bytes:
        """
        Serialize CUSTODY_ACK message.

        Header layout:
          B   msg_type (4)
          16s bundle_id
          Q   ack_nonce
          H   ranges_len
        Followed by JSON-encoded ranges.
        """
        bundle_id_bytes = message["bundle_id"][:16].encode("utf-8").ljust(16, b"\0")
        ranges_data = json.dumps(message["ranges"]).encode("utf-8")
        ack_nonce = int(message.get("ack_nonce", 0))

        header = struct.pack(
            "!B16sQH",
            4, 
            bundle_id_bytes,
            ack_nonce,
            len(ranges_data),
        )
        return header + ranges_data

    # ------------------------ DELIVERED --------------------------------

    def _serialize_delivered_message(self, message: Dict[str, Any]) -> bytes:
        """
        Serialize DELIVERED message.

        Header layout:
          B   msg_type (5)
          16s bundle_id
        """
        bundle_id_bytes = message["bundle_id"][:16].encode("utf-8").ljust(16, b"\0")
        return struct.pack("!B16s", 5, bundle_id_bytes)


    # Deserialization
    # ------------------------------------------------------------------

    def _deserialize_message(self, data: bytes) -> Optional[Dict[str, Any]]:
        """Deserialize bytes back to message dictionary."""
        if len(data) < 1:
            return None

        msg_type = struct.unpack("!B", data[:1])[0]

        try:
            if msg_type == 1:  
                return self._deserialize_data_message(data)
            elif msg_type == 2: 
                return self._deserialize_sack_message(data)
            elif msg_type == 3:  
                return self._deserialize_custody_req_message(data)
            elif msg_type == 4:  
                return self._deserialize_custody_ack_message(data)
            elif msg_type == 5:  
                return self._deserialize_delivered_message(data)
            else:
                return json.loads(data.decode("utf-8"))
        except Exception as e:
            logger.error("Failed to deserialize message: %s", e)
            return None

    def _deserialize_data_message(self, data: bytes) -> Dict[str, Any]:
        """Deserialize DATA message."""
        header = struct.unpack("!B16sIIIHHIBH", data[:40])
        bundle_id = header[1].rstrip(b"\0").decode("utf-8")
        payload_len = header[9]
        payload = data[40 : 40 + payload_len]

        return {
            "msg_type": "DATA",
            "bundle_id": bundle_id,
            "chunk_id": header[2],
            "total_chunks": header[3],
            "block_id": header[4],
            "k": header[5],
            "r": header[6],
            "checksum": header[7],
            "payload": payload,
        }

    def _deserialize_sack_message(self, data: bytes) -> Dict[str, Any]:
        """Deserialize SACK message and reconstruct acked_chunks."""
        header = struct.unpack("!B16sIH", data[:23])
        bundle_id = header[1].rstrip(b"\0").decode("utf-8")
        raw_watermark = header[2]
        bitmap_len = header[3]

        if raw_watermark == 0xFFFFFFFF:
            recv_watermark = -1
        else:
            recv_watermark = int(raw_watermark)

        bitmap = data[23 : 23 + bitmap_len]

        acked_chunks = set()

        if recv_watermark >= 0:
            acked_chunks.update(range(recv_watermark + 1))

        base = recv_watermark + 1
        for byte_index, b in enumerate(bitmap):
            for bit_pos in range(8):
                if b & (1 << (7 - bit_pos)):
                    chunk_id = base + byte_index * 8 + bit_pos
                    acked_chunks.add(chunk_id)

        return {
            "msg_type": "SACK",
            "bundle_id": bundle_id,
            "recv_watermark": recv_watermark,
            "sack_bitmap": bitmap,
            "acked_chunks": sorted(acked_chunks),
        }

    def _deserialize_custody_req_message(self, data: bytes) -> Dict[str, Any]:
        """Deserialize CUSTODY_REQ message."""
        header = struct.unpack("!B16sIH", data[:23])
        bundle_id = header[1].rstrip(b"\0").decode("utf-8")
        ttl_remaining = header[2]
        ranges_len = header[3]
        ranges_data = data[23 : 23 + ranges_len]
        ranges_raw = json.loads(ranges_data.decode("utf-8"))
        ranges = [tuple(r) for r in ranges_raw]

        return {
            "msg_type": "CUSTODY_REQ",
            "bundle_id": bundle_id,
            "ttl_remaining": ttl_remaining,
            "ranges": ranges,
        }

    def _deserialize_custody_ack_message(self, data: bytes) -> Dict[str, Any]:
        """Deserialize CUSTODY_ACK message."""
        header = struct.unpack("!B16sQH", data[:27])
        bundle_id = header[1].rstrip(b"\0").decode("utf-8")
        ack_nonce = header[2]
        ranges_len = header[3]
        ranges_data = data[27 : 27 + ranges_len]
        ranges_raw = json.loads(ranges_data.decode("utf-8"))
        ranges = [tuple(r) for r in ranges_raw]

        return {
            "msg_type": "CUSTODY_ACK",
            "bundle_id": bundle_id,
            "ranges": ranges,
            "ack_nonce": ack_nonce,
        }

    def _deserialize_delivered_message(self, data: bytes) -> Dict[str, Any]:
        """Deserialize DELIVERED message."""
        header = struct.unpack("!B16s", data[:17])
        bundle_id = header[1].rstrip(b"\0").decode("utf-8")

        return {
            "msg_type": "DELIVERED",
            "bundle_id": bundle_id,
        }
