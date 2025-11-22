import time
import threading
import logging
import signal
import sys
from typing import Dict, Any, Tuple, List, Optional

from .config import CourierConfig, load_config
from .storage import SQLiteStorage
from .network_io import NetworkIO
from .send_engine import SendEngine
from .receive_engine import ReceiveEngine
from .custody_manager import CustodyManager

logger = logging.getLogger(__name__)


class Node:
    """
    Orchestrator: owns SendEngine, ReceiveEngine, CustodyManager, and Storage,
    runs the event loop and dispatches network messages.
    """

    def __init__(self, config: CourierConfig, storage_path: Optional[str] = None):
        self.config = config
        self.node_id = config.node.node_id
        self.port = config.node.port
        self.running = False

        if storage_path is not None:
            db_path = storage_path
        else:
            storage_cfg = getattr(config, "storage", None)
            if storage_cfg is not None and getattr(storage_cfg, "db_path", None) is not None:
                db_path = storage_cfg.db_path
            else:
                db_path = f"courier_{self.node_id}_{self.port}.db"

        logger.info("Using SQLite database %s", db_path)
        self.storage = SQLiteStorage(db_path)

        self.network_io = NetworkIO(self.port, self._handle_message)

        self.send_engine = SendEngine(
            config.transfer,
            config.fec,
            self.storage,
            self.network_io.send_message,
            self.node_id,
        )

        self.receive_engine = ReceiveEngine(
            config.transfer,
            config.fec,
            self.storage,
            self.network_io.send_message,
            self.node_id,
        )

        self.custody_manager = CustodyManager(
            config.custody,
            self.storage,
            self.network_io.send_message,
            self.node_id,
        )

        self.send_destinations: Dict[str, Tuple[str, int]] = {}

        # Event loop state
        self.event_thread: Optional[threading.Thread] = None
        self.last_timeout_check = time.time()
        self.last_cleanup = time.time()
        try:
            signal.signal(signal.SIGINT, self._signal_handler)
            signal.signal(signal.SIGTERM, self._signal_handler)
        except ValueError:

            logger.debug("Signal handlers not installed (non-main thread).")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the node."""
        try:
            self.network_io.start()
            self.send_engine.resume_transfers()

            self.running = True
            self.event_thread = threading.Thread(
                target=self._event_loop, daemon=True
            )
            self.event_thread.start()

            logger.info("Node %s started on port %d", self.node_id, self.port)
        except Exception as e:
            logger.error("Failed to start node: %s", e)
            self.stop()
            raise

    def stop(self) -> None:
        """Stop the node gracefully."""
        if not self.running:
            return

        logger.info("Stopping node %s", self.node_id)
        self.running = False

        if self.event_thread and self.event_thread.is_alive():
            self.event_thread.join(timeout=1.0)

        if self.network_io:
            self.network_io.stop()

        if self.storage:
            self.storage.close()

        logger.info("Node stopped")

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals."""
        logger.info("Received signal %s, shutting down...", signum)
        self.stop()
        sys.exit(0)

    # 
    # Event loop
    # ------------------------------------------------------------------

    def _event_loop(self) -> None:
        """Main event loop - runs every 10ms."""
        while self.running:
            try:
                current_time = time.time()

                # Check timeouts every 50ms
                if current_time - self.last_timeout_check > 0.05:
                    self._check_timeouts()
                    self.last_timeout_check = current_time

                # Cleanup every 60 seconds
                if current_time - self.last_cleanup > 60.0:
                    self._periodic_cleanup()
                    self.last_cleanup = current_time

                time.sleep(0.01)
            except Exception as e:
                logger.error("Error in event loop: %s", e)
                if self.running:
                    time.sleep(0.1)

    def _check_timeouts(self) -> None:
        """Check for timeouts and trigger retransmissions."""
        self.send_engine.check_timeouts()

        for bundle_id, dest_addr in self.send_destinations.items():
            self.send_engine.retransmit_chunks(bundle_id, dest_addr)

        self.custody_manager.check_retry_timers()

    def _periodic_cleanup(self) -> None:
        """Periodic cleanup tasks."""
        self.send_engine.cleanup_completed_transfers()
        self.storage.cleanup_expired_bundles(time.time())
        logger.debug("Performed periodic cleanup")

    # ------------------------------------------------------------------
    # Network message handling
    # ------------------------------------------------------------------

    def _handle_message(
        self, message: Dict[str, Any], sender_addr: Tuple[str, int]
    ) -> None:
        """Handle incoming network messages."""
        try:
            msg_type = message.get("msg_type", "")

            if msg_type == "DATA":
                self._handle_data_message(message, sender_addr)
            elif msg_type == "SACK":
                self._handle_sack_message(message, sender_addr)
            elif msg_type == "DELIVERED":
                self._handle_delivered_message(message, sender_addr)
            elif msg_type == "CUSTODY_REQ":
                self._handle_custody_req_message(message, sender_addr)
            elif msg_type == "CUSTODY_ACK":
                self._handle_custody_ack_message(message, sender_addr)
            else:
                logger.warning("Unknown message type: %s", msg_type)
        except Exception as e:
            logger.error("Error handling message from %s: %s", sender_addr, e)

    def _handle_data_message(
        self, message: Dict[str, Any], sender_addr: Tuple[str, int]
    ) -> None:
        self.receive_engine.handle_data(message, sender_addr)

    def _handle_sack_message(
        self, message: Dict[str, Any], sender_addr: Tuple[str, int]
    ) -> None:
        self.send_engine.handle_sack(message, sender_addr)

    def _handle_delivered_message(
        self, message: Dict[str, Any], sender_addr: Tuple[str, int]
    ) -> None:
        self.send_engine.handle_delivered(message)
        self.custody_manager.handle_delivered(message)

    def _handle_custody_req_message(
        self, message: Dict[str, Any], sender_addr: Tuple[str, int]
    ) -> None:
        self.custody_manager.handle_custody_req(message, sender_addr)

    def _handle_custody_ack_message(
        self, message: Dict[str, Any], sender_addr: Tuple[str, int]
    ) -> None:
        self.custody_manager.handle_custody_ack(message)

    # Public API used by CLI
    # ------------------------------------------------------------------

    def send_file(
        self,
        file_path: str,
        destination_node: str,
        dest_host: str,
        dest_port: int,
        fec_enabled: bool = False,
    ) -> str:
        """Send a file to destination node."""
        dest_addr = (dest_host, dest_port)

        bundle_id = self.send_engine.send_file(
            file_path, destination_node, dest_addr, fec_enabled
        )

        self.send_destinations[bundle_id] = dest_addr
        return bundle_id

    def get_send_status(self, bundle_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Get status of send operations.

        If bundle_id is given, return a single bundle's status (or None).
        Otherwise return {'bundles': [...]} for all known bundles.

        IMPORTANT: For a specific bundle, we now prefer the in-memory
        SendEngine state and only *enrich* it with DB rows, instead of
        failing when the DB row is missing.
        """
        if bundle_id is not None:
            send_state = self.send_engine.get_send_status(bundle_id)
            bundle_data = self.storage.load_bundle(bundle_id)

            if send_state is None and bundle_data is None:
                return None

            if send_state is not None:
                progress = send_state.get("progress", 0.0)
                acked_chunks = send_state.get("acked_chunks", 0)
                completed = send_state.get("completed", False)
                window_start = send_state.get("window_start", 0)
                window_end = send_state.get("window_end", 0)
                timeout_interval = send_state.get("timeout_interval", 0.0)
                smoothed_rtt = send_state.get("smoothed_rtt", 0.0)
                bytes_sent = send_state.get("bytes_sent", 0)
                chunks_retx = send_state.get("chunks_retransmitted", 0)
                total_chunks = send_state.get("total_chunks", 0)
            else:
                # No in-memory state: derive minimal info from bundle_data.
                progress = 1.0 if bundle_data and bundle_data["state"] == "delivered" else 0.0
                acked_chunks = 0
                completed = bundle_data and bundle_data["state"] == "delivered"
                window_start = 0
                window_end = 0
                timeout_interval = 0.0
                smoothed_rtt = 0.0
                bytes_sent = bundle_data.get("bytes_sent", 0) if bundle_data else 0
                chunks_retx = bundle_data.get("chunks_retransmitted", 0) if bundle_data else 0
                total_chunks = bundle_data["total_chunks"] if bundle_data else 0

            if bundle_data is not None:
                src = bundle_data["src"]
                dst = bundle_data["dst"]
                file_path = bundle_data.get("file_path", "")
                file_size = bundle_data.get("file_size", 0)
                state = bundle_data["state"]
                fec_enabled = bundle_data.get("fec_enabled", False)
                created_at = bundle_data.get("created_at", "")
                db_bytes_sent = bundle_data.get("bytes_sent", bytes_sent)
                db_chunks_retx = bundle_data.get("chunks_retransmitted", chunks_retx)

                bytes_sent = max(bytes_sent, db_bytes_sent)
                chunks_retx = max(chunks_retx, db_chunks_retx)
            else:
                src = self.node_id
                dst = "unknown"
                file_path = ""
                file_size = 0
                state = "sending" if not completed else "delivered"
                fec_enabled = False
                created_at = ""

            if completed and state != "delivered":
                state = "delivered"

            status: Dict[str, Any] = {
                "bundle_id": bundle_id,
                "src": src,
                "dst": dst,
                "file_path": file_path,
                "file_size": file_size,
                "state": state,
                "fec_enabled": fec_enabled,
                "total_chunks": total_chunks,
                "bytes_sent": bytes_sent,
                "chunks_retransmitted": chunks_retx,
                "created_at": created_at,
                "progress": progress,
                "acked_chunks": acked_chunks,
                "completed": completed,
                "window_start": window_start,
                "window_end": window_end,
                "timeout_interval": timeout_interval,
                "smoothed_rtt": smoothed_rtt,
            }
            return status

        bundles = self.storage.list_bundles()
        statuses: List[Dict[str, Any]] = []

        for bundle_data in bundles:
            bid = bundle_data["bundle_id"]
            send_state = self.send_engine.get_send_status(bid) or {}

            if "progress" in send_state:
                progress = send_state["progress"]
            else:
                progress = 1.0 if bundle_data["state"] == "delivered" else 0.0

            status = {
                "bundle_id": bid,
                "src": bundle_data["src"],
                "dst": bundle_data["dst"],
                "file_path": bundle_data.get("file_path", ""),
                "file_size": bundle_data.get("file_size", 0),
                "state": bundle_data["state"],
                "fec_enabled": bundle_data.get("fec_enabled", False),
                "created_at": bundle_data.get("created_at", ""),
                "total_chunks": bundle_data["total_chunks"],
                "bytes_sent": bundle_data.get("bytes_sent", 0),
                "chunks_retransmitted": bundle_data.get(
                    "chunks_retransmitted", 0
                ),
                "progress": progress,
            }
            statuses.append(status)

        return {"bundles": statuses}

    def list_bundles(self, state_filter: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all bundles, optionally filtered by state."""
        if state_filter:
            return self.storage.list_bundles_by_state(state_filter)
        else:
            return self.storage.list_bundles()

    def run_receiver(self) -> None:
        """Run in receiver mode - just listen for incoming data."""
        logger.info("Running in receiver mode on port %d", self.port)
        try:
            while self.running:
                time.sleep(1.0)
        except KeyboardInterrupt:
            logger.info("Received interrupt, shutting down...")
            self.stop()

    def wait_for_completion(self, bundle_id: str, timeout: Optional[float] = None) -> bool:
        """Wait for a specific bundle to complete."""
        start_time = time.time()

        while self.running:
            status = self.get_send_status(bundle_id)
            if not status:
                logger.warning("Bundle %s not found (no state in engine or DB)", bundle_id)
                return False

            if status.get("completed", False) or status.get("state") == "delivered":
                return True

            if timeout is not None and (time.time() - start_time) > timeout:
                logger.warning(
                    "Timeout waiting for bundle %s completion", bundle_id
                )
                return False

            time.sleep(0.1)

        return False


def create_node(
    port: Optional[int] = None,
    node_id: Optional[str] = None,
    config_path: Optional[str] = None,
) -> Node:
    """Create and configure a node instance."""
    config = load_config(config_path)

    if port is not None:
        config.node.port = port
    if node_id is not None:
        config.node.node_id = node_id

    return Node(config)