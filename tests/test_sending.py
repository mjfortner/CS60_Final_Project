#!/usr/bin/env python3

import unittest
import tempfile
import os
import sys
import time
import zlib
from unittest.mock import Mock, patch

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from courier.config import CourierConfig, NodeConfig, TransferConfig, FECConfig, CustodyConfig
from courier.storage import SQLiteStorage
from courier.send_engine import SendEngine
from courier.network_io import NetworkIO
from courier.protocol import make_sack_msg, make_delivered_msg

class TestSendEngine(unittest.TestCase):
    def setUp(self):
        """Set up test fixtures."""
        self.temp_db = tempfile.NamedTemporaryFile(delete=False)
        self.temp_db.close()
        
        self.config = TransferConfig(
            chunk_size=100,
            window_size=4,
            base_rto_ms=50,
            ttl_sec=300
        )
        
        self.fec_config = FECConfig(
            enabled=True,
            k=2,
            r=1
        )
        
        self.storage = SQLiteStorage(self.temp_db.name)
        
        # Mock network send function
        self.network_send_mock = Mock(return_value=True)
        
        self.send_engine = SendEngine(
            self.config,
            self.fec_config,
            self.storage,
            self.network_send_mock
        )
    
    def tearDown(self):
        """Clean up test fixtures."""
        self.storage.close()
        os.unlink(self.temp_db.name)
    
    def test_create_chunks(self):
        """Test file chunking without FEC."""
        # Create test file
        test_data = b"Hello, world! This is a test file for chunking."
        test_file = tempfile.NamedTemporaryFile(delete=False)
        test_file.write(test_data)
        test_file.close()
        
        try:
            bundle_id = "test-bundle-123"
            chunks = self.send_engine._create_chunks(test_file.name, bundle_id, fec_enabled=False)
            
            # Should create ceil(len(data)/chunk_size) chunks
            expected_chunks = (len(test_data) + self.config.chunk_size - 1) // self.config.chunk_size
            self.assertEqual(len(chunks), expected_chunks)
            
            # Verify first chunk
            first_chunk = chunks[0]
            self.assertEqual(first_chunk['bundle_id'], bundle_id)
            self.assertEqual(first_chunk['chunk_id'], 0)
            self.assertFalse(first_chunk['is_parity'])
            self.assertEqual(first_chunk['payload'], test_data[:self.config.chunk_size])
            
            # Verify checksum
            expected_checksum = zlib.crc32(test_data[:self.config.chunk_size]) & 0xffffffff
            self.assertEqual(first_chunk['checksum'], expected_checksum)
            
        finally:
            os.unlink(test_file.name)
    
    def test_create_chunks_with_fec(self):
        """Test file chunking with FEC enabled."""
        # Create test file
        test_data = b"Hello, world! This is a test file for FEC testing."
        test_file = tempfile.NamedTemporaryFile(delete=False)
        test_file.write(test_data)
        test_file.close()
        
        try:
            bundle_id = "test-bundle-fec"
            chunks = self.send_engine._create_chunks(test_file.name, bundle_id, fec_enabled=True)
            
            # Should have data chunks + parity chunks
            data_chunks = (len(test_data) + self.config.chunk_size - 1) // self.config.chunk_size
            
            # Count data and parity chunks
            data_chunk_count = sum(1 for c in chunks if not c['is_parity'])
            parity_chunk_count = sum(1 for c in chunks if c['is_parity'])
            
            self.assertEqual(data_chunk_count, data_chunks)
            self.assertGreater(parity_chunk_count, 0)  # Should have some parity chunks
            
        finally:
            os.unlink(test_file.name)
    
    def test_send_file_basic(self):
        """Test basic file sending functionality."""
        # Create test file
        test_data = b"Test file content for sending"
        test_file = tempfile.NamedTemporaryFile(delete=False)
        test_file.write(test_data)
        test_file.close()
        
        try:
            dest_addr = ("127.0.0.1", 5001)
            bundle_id = self.send_engine.send_file(
                test_file.name, 
                "dest-node", 
                dest_addr,
                fec_enabled=False
            )
            
            # Should return a bundle ID
            self.assertIsNotNone(bundle_id)
            self.assertIsInstance(bundle_id, str)
            
            # Should have created bundle in storage
            bundle_data = self.storage.load_bundle(bundle_id)
            self.assertIsNotNone(bundle_data)
            self.assertEqual(bundle_data['dst'], "dest-node")
            self.assertEqual(bundle_data['state'], "sending")
            
            # Should have called network send
            self.assertTrue(self.network_send_mock.called)
            
        finally:
            os.unlink(test_file.name)
    
    def test_handle_sack(self):
        """Test SACK message handling."""
        # Create test file with enough data for multiple chunks
        # With chunk_size=100, we need >100 bytes to get multiple chunks
        # Create 500 bytes to ensure we have at least 5 chunks
        test_data = b"X" * 500
        test_file = tempfile.NamedTemporaryFile(delete=False)
        test_file.write(test_data)
        test_file.close()
        
        try:
            dest_addr = ("127.0.0.1", 5001)
            bundle_id = self.send_engine.send_file(test_file.name, "dest-node", dest_addr)
            
            send_state = self.send_engine.active_sends[bundle_id]
            # With window_size=4, initial window sends chunks 0-3
            # Total chunks should be 5 (500 bytes / 100 bytes per chunk)
            self.assertEqual(send_state.total_chunks, 5)
            
            # Reset mock to count new calls
            self.network_send_mock.reset_mock()
            
            # Simulate SACK for first chunk only
            sack_msg = make_sack_msg(bundle_id, [0])
            
            self.send_engine.handle_sack(sack_msg, dest_addr)
            
            # Check that send state was updated
            send_state = self.send_engine.active_sends[bundle_id]
            self.assertIn(0, send_state.acked_chunks)
            self.assertEqual(send_state.window_start, 1)  # Should advance
            
            # Window should now be [1, 5), so chunk 4 should be sent
            self.assertTrue(self.network_send_mock.called)
            
        finally:
            os.unlink(test_file.name)
    
    def test_handle_delivered(self):
        """Test DELIVERED message handling."""
        # Create test file and start send
        test_data = b"Test delivery confirmation"
        test_file = tempfile.NamedTemporaryFile(delete=False)
        test_file.write(test_data)
        test_file.close()
        
        try:
            dest_addr = ("127.0.0.1", 5001)
            bundle_id = self.send_engine.send_file(test_file.name, "dest-node", dest_addr)
            
            # Simulate DELIVERED message
            delivered_msg = make_delivered_msg(bundle_id)
            self.send_engine.handle_delivered(delivered_msg)
            
            # Check that transfer is marked complete
            send_state = self.send_engine.active_sends[bundle_id]
            self.assertTrue(send_state.completed)
            
            # Check bundle state in storage
            bundle_data = self.storage.load_bundle(bundle_id)
            self.assertEqual(bundle_data['state'], 'delivered')
            
        finally:
            os.unlink(test_file.name)
    
    def test_timeout_and_retransmission(self):
        """Test timeout detection and retransmission."""
        # Create test file and start send
        test_data = b"Test timeout handling"
        test_file = tempfile.NamedTemporaryFile(delete=False)
        test_file.write(test_data)
        test_file.close()
        
        try:
            dest_addr = ("127.0.0.1", 5001)
            bundle_id = self.send_engine.send_file(test_file.name, "dest-node", dest_addr)
            
            send_state = self.send_engine.active_sends[bundle_id]
            
            # Simulate timeout by setting timers to past
            current_time = time.time() * 1000
            for chunk_id in send_state.chunk_timers:
                send_state.chunk_timers[chunk_id] = current_time - 100  # 100ms ago
            
            # Check timeouts
            self.send_engine.check_timeouts()
            
            # Should have chunks in retransmit queue
            self.assertGreater(len(send_state.retransmit_queue), 0)
            
            # Should have increased timeout interval (backoff)
            self.assertGreater(send_state.timeout_interval, self.config.base_rto_ms)
            
        finally:
            os.unlink(test_file.name)


class TestNetworkIO(unittest.TestCase):
    def setUp(self):
        """Set up test fixtures."""
        self.message_handler_mock = Mock()
        self.network_io = NetworkIO(0, self.message_handler_mock)  # Use port 0 for auto-assign
    
    def tearDown(self):
        """Clean up test fixtures."""
        if self.network_io.running:
            self.network_io.stop()
    
    def test_message_serialization(self):
        """Test message serialization and deserialization."""
        # Test DATA message
        data_msg = {
            'msg_type': 'DATA',
            'bundle_id': 'test-bundle-123',
            'chunk_id': 42,
            'total_chunks': 100,
            'block_id': 5,
            'k': 4,
            'r': 2,
            'checksum': 0x12345678,
            'payload': b'Hello, test payload!'
        }
        
        # Serialize and deserialize
        serialized = self.network_io._serialize_message(data_msg)
        deserialized = self.network_io._deserialize_message(serialized)
        
        self.assertEqual(deserialized['msg_type'], 'DATA')
        self.assertEqual(deserialized['bundle_id'], 'test-bundle-123')
        self.assertEqual(deserialized['chunk_id'], 42)
        self.assertEqual(deserialized['total_chunks'], 100)
        self.assertEqual(deserialized['payload'], b'Hello, test payload!')
        
        # Test SACK message
        sack_msg = {
            'msg_type': 'SACK',
            'bundle_id': 'test-bundle-456',
            'acked_chunks': [0, 1, 2, 5, 10]
        }
        
        serialized = self.network_io._serialize_message(sack_msg)
        deserialized = self.network_io._deserialize_message(serialized)
        
        self.assertEqual(deserialized['msg_type'], 'SACK')
        self.assertEqual(deserialized['bundle_id'], 'test-bundle-456')
        self.assertEqual(deserialized['acked_chunks'], [0, 1, 2, 5, 10])
    
    def test_delivered_message_serialization(self):
        """Test DELIVERED message serialization."""
        delivered_msg = {
            'msg_type': 'DELIVERED',
            'bundle_id': 'test-bundle-789'
        }
        
        serialized = self.network_io._serialize_message(delivered_msg)
        deserialized = self.network_io._deserialize_message(serialized)
        
        self.assertEqual(deserialized['msg_type'], 'DELIVERED')
        self.assertEqual(deserialized['bundle_id'], 'test-bundle-789')


class TestStorage(unittest.TestCase):
    def setUp(self):
        """Set up test fixtures."""
        self.temp_db = tempfile.NamedTemporaryFile(delete=False)
        self.temp_db.close()
        self.storage = SQLiteStorage(self.temp_db.name)
    
    def tearDown(self):
        """Clean up test fixtures."""
        self.storage.close()
        os.unlink(self.temp_db.name)
    
    def test_bundle_operations(self):
        """Test bundle save/load operations."""
        bundle_data = {
            'bundle_id': 'test-bundle-storage',
            'src': 'node1',
            'dst': 'node2',
            'ttl': 300,
            'state': 'sending',
            'total_chunks': 10,
            'file_path': '/test/file.txt',
            'file_size': 1024
        }
        
        # Save bundle
        self.storage.save_bundle(bundle_data)
        
        # Load bundle
        loaded = self.storage.load_bundle('test-bundle-storage')
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded['bundle_id'], 'test-bundle-storage')
        self.assertEqual(loaded['src'], 'node1')
        self.assertEqual(loaded['dst'], 'node2')
        self.assertEqual(loaded['total_chunks'], 10)
        
        # Update state
        self.storage.update_bundle_state('test-bundle-storage', 'delivered')
        updated = self.storage.load_bundle('test-bundle-storage')
        self.assertEqual(updated['state'], 'delivered')
    
    def test_chunk_operations(self):
        """Test chunk save/load operations."""
        # First save a bundle
        bundle_data = {
            'bundle_id': 'test-chunk-bundle',
            'src': 'node1',
            'dst': 'node2',
            'ttl': 300,
            'state': 'sending',
            'total_chunks': 2
        }
        self.storage.save_bundle(bundle_data)
        
        # Save chunks
        chunk1 = {
            'bundle_id': 'test-chunk-bundle',
            'chunk_id': 0,
            'is_parity': False,
            'block_id': 0,
            'k': 4,
            'r': 2,
            'payload': b'Chunk 0 data',
            'checksum': 'abc123'
        }
        
        chunk2 = {
            'bundle_id': 'test-chunk-bundle',
            'chunk_id': 1,
            'is_parity': False,
            'block_id': 0,
            'k': 4,
            'r': 2,
            'payload': b'Chunk 1 data',
            'checksum': 'def456'
        }
        
        self.storage.save_chunk(chunk1)
        self.storage.save_chunk(chunk2)
        
        # Load chunks
        chunks = self.storage.load_chunks_for_bundle('test-chunk-bundle')
        self.assertEqual(len(chunks), 2)
        
        chunks_by_id = {c['chunk_id']: c for c in chunks}
        self.assertEqual(chunks_by_id[0]['payload'], b'Chunk 0 data')
        self.assertEqual(chunks_by_id[1]['payload'], b'Chunk 1 data')


def run_tests():
    """Run all unit tests."""
    unittest.main(verbosity=2)


if __name__ == '__main__':
    run_tests()