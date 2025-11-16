import time
import threading
import logging
import signal
import sys
from typing import Dict, Any, Tuple, List
from .config import CourierConfig, load_config
from .storage import SQLiteStorage
from .network_io import NetworkIO
from .send_engine import SendEngine

logger = logging.getLogger(__name__)

class Node:
    def __init__(self, config: CourierConfig, storage_path: str = None):
        self.config = config
        self.node_id = config.node.node_id
        self.port = config.node.port
        self.running = False
        
        # Initialize storage
        db_path = storage_path or f"courier_{self.node_id}_{self.port}.db"
        self.storage = SQLiteStorage(db_path)
        
        # Initialize network I/O
        self.network_io = NetworkIO(self.port, self._handle_message)
        
        # Initialize send engine
        self.send_engine = SendEngine(
            config.transfer, 
            config.fec, 
            self.storage,
            self.network_io.send_message
        )
        
        # Track destination addresses for active sends
        self.send_destinations: Dict[str, Tuple[str, int]] = {}
        
        # Event loop
        self.event_thread = None
        self.last_timeout_check = time.time()
        self.last_cleanup = time.time()
        
        # Setup signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def start(self) -> None:
        """Start the node."""
        try:
            # Start network I/O
            self.network_io.start()
            
            # Resume any interrupted transfers
            self.send_engine.resume_transfers()
            
            # Start event loop
            self.running = True
            self.event_thread = threading.Thread(target=self._event_loop, daemon=True)
            self.event_thread.start()
            
            logger.info(f"Node {self.node_id} started on port {self.port}")
            
        except Exception as e:
            logger.error(f"Failed to start node: {e}")
            self.stop()
            raise
    
    def stop(self) -> None:
        """Stop the node gracefully."""
        if not self.running:
            return
        
        logger.info(f"Stopping node {self.node_id}")
        self.running = False
        
        # Stop event loop
        if self.event_thread and self.event_thread.is_alive():
            self.event_thread.join(timeout=1.0)
        
        # Stop network I/O
        if self.network_io:
            self.network_io.stop()
        
        # Close storage
        if self.storage:
            self.storage.close()
        
        logger.info("Node stopped")
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals."""
        logger.info(f"Received signal {signum}, shutting down...")
        self.stop()
        sys.exit(0)
    
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
                
                # Sleep for 10ms
                time.sleep(0.01)
                
            except Exception as e:
                logger.error(f"Error in event loop: {e}")
                if self.running:
                    time.sleep(0.1)  # Brief pause on error
    
    def _check_timeouts(self) -> None:
        """Check for timeouts and trigger retransmissions."""
        # Check send engine timeouts
        self.send_engine.check_timeouts()
        
        # Process retransmit queues
        for bundle_id, dest_addr in self.send_destinations.items():
            self.send_engine.retransmit_chunks(bundle_id, dest_addr)
    
    def _periodic_cleanup(self) -> None:
        """Periodic cleanup tasks."""
        # Cleanup completed transfers
        self.send_engine.cleanup_completed_transfers()
        
        # Cleanup expired bundles from storage
        self.storage.cleanup_expired_bundles(time.time())
        
        logger.debug("Performed periodic cleanup")
    
    def _handle_message(self, message: Dict[str, Any], sender_addr: Tuple[str, int]) -> None:
        """Handle incoming network messages."""
        try:
            msg_type = message.get('msg_type', '')
            
            if msg_type == 'DATA':
                self._handle_data_message(message, sender_addr)
            elif msg_type == 'SACK':
                self._handle_sack_message(message, sender_addr)
            elif msg_type == 'DELIVERED':
                self._handle_delivered_message(message, sender_addr)
            elif msg_type == 'CUSTODY_REQ':
                self._handle_custody_req_message(message, sender_addr)
            elif msg_type == 'CUSTODY_ACK':
                self._handle_custody_ack_message(message, sender_addr)
            else:
                logger.warning(f"Unknown message type: {msg_type}")
                
        except Exception as e:
            logger.error(f"Error handling message from {sender_addr}: {e}")
    
    def _handle_data_message(self, message: Dict[str, Any], sender_addr: Tuple[str, int]) -> None:
        """Handle incoming DATA message - would be handled by ReceiveEngine."""
        # For part 1 (Send/Control Path), we mainly care about sending
        # This would be implemented in part 2 (Receive Path)
        logger.debug(f"Received DATA message from {sender_addr} (not implemented)")
        pass
    
    def _handle_sack_message(self, message: Dict[str, Any], sender_addr: Tuple[str, int]) -> None:
        """Handle SACK message from receiver."""
        self.send_engine.handle_sack(message, sender_addr)
    
    def _handle_delivered_message(self, message: Dict[str, Any], sender_addr: Tuple[str, int]) -> None:
        """Handle DELIVERED message."""
        self.send_engine.handle_delivered(message)
    
    def _handle_custody_req_message(self, message: Dict[str, Any], sender_addr: Tuple[str, int]) -> None:
        """Handle CUSTODY_REQ message - would be handled by CustodyManager."""
        # For part 1, we focus on sending side
        logger.debug(f"Received CUSTODY_REQ from {sender_addr} (not implemented)")
        pass
    
    def _handle_custody_ack_message(self, message: Dict[str, Any], sender_addr: Tuple[str, int]) -> None:
        """Handle CUSTODY_ACK message."""
        # For part 1, this would be used to confirm custody transfer
        logger.debug(f"Received CUSTODY_ACK from {sender_addr} (not implemented)")
        pass
    
    def send_file(self, file_path: str, destination_node: str, dest_host: str, dest_port: int,
                  fec_enabled: bool = False) -> str:
        """Send a file to destination node."""
        dest_addr = (dest_host, dest_port)
        
        # Start the send
        bundle_id = self.send_engine.send_file(
            file_path, destination_node, dest_addr, fec_enabled
        )
        
        # Track destination for retransmissions
        self.send_destinations[bundle_id] = dest_addr
        
        return bundle_id
    
    def get_send_status(self, bundle_id: str = None) -> Dict[str, Any]:
        """Get status of send operations."""
        if bundle_id:
            # Get specific bundle status
            status = self.send_engine.get_send_status(bundle_id)
            if status:
                # Add bundle metadata from storage
                bundle_data = self.storage.load_bundle(bundle_id)
                if bundle_data:
                    status.update({
                        'src': bundle_data['src'],
                        'dst': bundle_data['dst'],
                        'file_path': bundle_data.get('file_path', ''),
                        'file_size': bundle_data.get('file_size', 0),
                        'state': bundle_data['state'],
                        'fec_enabled': bundle_data.get('fec_enabled', False)
                    })
            return status
        else:
            # Get all bundle statuses
            bundles = self.storage.list_bundles()
            statuses = []
            
            for bundle_data in bundles:
                bundle_id = bundle_data['bundle_id']
                status = self.send_engine.get_send_status(bundle_id) or {}
                
                status.update({
                    'bundle_id': bundle_id,
                    'src': bundle_data['src'],
                    'dst': bundle_data['dst'],
                    'file_path': bundle_data.get('file_path', ''),
                    'file_size': bundle_data.get('file_size', 0),
                    'state': bundle_data['state'],
                    'fec_enabled': bundle_data.get('fec_enabled', False),
                    'created_at': bundle_data.get('created_at', ''),
                    'total_chunks': bundle_data['total_chunks'],
                    'bytes_sent': bundle_data.get('bytes_sent', 0),
                    'chunks_retransmitted': bundle_data.get('chunks_retransmitted', 0)
                })
                
                statuses.append(status)
            
            return {'bundles': statuses}
    
    def list_bundles(self, state_filter: str = None) -> List[Dict[str, Any]]:
        """List all bundles, optionally filtered by state."""
        if state_filter:
            return self.storage.list_bundles_by_state(state_filter)
        else:
            return self.storage.list_bundles()
    
    def run_receiver(self) -> None:
        """Run in receiver mode - just listen for incoming data."""
        logger.info(f"Running in receiver mode on port {self.port}")
        try:
            # Keep the main thread alive
            while self.running:
                time.sleep(1.0)
        except KeyboardInterrupt:
            logger.info("Received interrupt, shutting down...")
            self.stop()
    
    def wait_for_completion(self, bundle_id: str, timeout: float = None) -> bool:
        """Wait for a specific bundle to complete."""
        start_time = time.time()
        
        while self.running:
            status = self.get_send_status(bundle_id)
            if not status:
                logger.warning(f"Bundle {bundle_id} not found")
                return False
            
            if status.get('completed', False) or status.get('state') == 'delivered':
                return True
            
            if timeout and (time.time() - start_time) > timeout:
                logger.warning(f"Timeout waiting for bundle {bundle_id} completion")
                return False
            
            time.sleep(0.1)
        
        return False


def create_node(port: int = None, node_id: str = None, config_path: str = None) -> Node:
    """Create and configure a node instance."""
    config = load_config(config_path)
    
    if port:
        config.node.port = port
    if node_id:
        config.node.node_id = node_id
    
    return Node(config)