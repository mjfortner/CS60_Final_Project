import sqlite3
import json
import os
from typing import Dict, List, Optional, Any
from .storage_api import StorageAPI

class SQLiteStorage(StorageAPI):
    def __init__(self, db_path: str = "courier.db"):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_tables()
    
    def _init_tables(self):
        """Initialize database tables."""
        cursor = self.conn.cursor()
        
        # Bundles table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS bundles (
                bundle_id TEXT PRIMARY KEY,
                src TEXT NOT NULL,
                dst TEXT NOT NULL,
                ttl INTEGER NOT NULL,
                state TEXT NOT NULL,
                total_chunks INTEGER NOT NULL,
                bytes_sent INTEGER DEFAULT 0,
                chunks_retransmitted INTEGER DEFAULT 0,
                fec_enabled BOOLEAN DEFAULT 0,
                k INTEGER DEFAULT 0,
                r INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                file_path TEXT,
                file_size INTEGER DEFAULT 0
            )
        ''')
        
        # Chunks table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS chunks (
                bundle_id TEXT NOT NULL,
                chunk_id INTEGER NOT NULL,
                is_parity BOOLEAN DEFAULT 0,
                block_id INTEGER NOT NULL,
                k INTEGER NOT NULL,
                r INTEGER NOT NULL,
                payload BLOB NOT NULL,
                checksum TEXT NOT NULL,
                flags INTEGER DEFAULT 0,
                PRIMARY KEY (bundle_id, chunk_id),
                FOREIGN KEY (bundle_id) REFERENCES bundles(bundle_id)
            )
        ''')
        
        # Custody records table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS custody_records (
                bundle_id TEXT PRIMARY KEY,
                owner_node TEXT NOT NULL,
                chunk_ranges TEXT NOT NULL,
                retry_timer TIMESTAMP NOT NULL,
                retry_count INTEGER DEFAULT 0,
                max_retries INTEGER DEFAULT 10,
                state TEXT NOT NULL,
                acquired_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (bundle_id) REFERENCES bundles(bundle_id)
            )
        ''')
        
        self.conn.commit()
    
    def save_bundle(self, bundle_dict: Dict[str, Any]) -> None:
        """Save bundle to database."""
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO bundles 
            (bundle_id, src, dst, ttl, state, total_chunks, bytes_sent, 
             chunks_retransmitted, fec_enabled, k, r, file_path, file_size)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            bundle_dict['bundle_id'],
            bundle_dict['src'], 
            bundle_dict['dst'],
            bundle_dict['ttl'],
            bundle_dict['state'],
            bundle_dict['total_chunks'],
            bundle_dict.get('bytes_sent', 0),
            bundle_dict.get('chunks_retransmitted', 0),
            bundle_dict.get('fec_enabled', False),
            bundle_dict.get('k', 0),
            bundle_dict.get('r', 0),
            bundle_dict.get('file_path', ''),
            bundle_dict.get('file_size', 0)
        ))
        self.conn.commit()
    
    def load_bundle(self, bundle_id: str) -> Optional[Dict[str, Any]]:
        """Load bundle from database."""
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM bundles WHERE bundle_id = ?', (bundle_id,))
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None
    
    def update_bundle_state(self, bundle_id: str, new_state: str) -> None:
        """Update bundle state."""
        cursor = self.conn.cursor()
        cursor.execute(
            'UPDATE bundles SET state = ? WHERE bundle_id = ?',
            (new_state, bundle_id)
        )
        self.conn.commit()
    
    def update_bundle_stats(self, bundle_id: str, bytes_sent: int = None, 
                           chunks_retransmitted: int = None) -> None:
        """Update bundle transfer statistics."""
        cursor = self.conn.cursor()
        if bytes_sent is not None:
            cursor.execute(
                'UPDATE bundles SET bytes_sent = ? WHERE bundle_id = ?',
                (bytes_sent, bundle_id)
            )
        if chunks_retransmitted is not None:
            cursor.execute(
                'UPDATE bundles SET chunks_retransmitted = ? WHERE bundle_id = ?',
                (chunks_retransmitted, bundle_id)
            )
        self.conn.commit()
    
    def save_chunk(self, chunk_dict: Dict[str, Any]) -> None:
        """Save chunk to database."""
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO chunks 
            (bundle_id, chunk_id, is_parity, block_id, k, r, payload, checksum, flags)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            chunk_dict['bundle_id'],
            chunk_dict['chunk_id'],
            chunk_dict.get('is_parity', False),
            chunk_dict['block_id'],
            chunk_dict['k'],
            chunk_dict['r'],
            chunk_dict['payload'],
            chunk_dict['checksum'],
            chunk_dict.get('flags', 0)
        ))
        self.conn.commit()
    
    def load_chunks_for_bundle(self, bundle_id: str) -> List[Dict[str, Any]]:
        """Load all chunks for a bundle."""
        cursor = self.conn.cursor()
        cursor.execute(
            'SELECT * FROM chunks WHERE bundle_id = ? ORDER BY chunk_id',
            (bundle_id,)
        )
        return [dict(row) for row in cursor.fetchall()]
    
    def save_custody_record(self, custody_dict: Dict[str, Any]) -> None:
        """Save custody record to database."""
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO custody_records
            (bundle_id, owner_node, chunk_ranges, retry_timer, retry_count, 
             max_retries, state)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            custody_dict['bundle_id'],
            custody_dict['owner_node'],
            json.dumps(custody_dict['chunk_ranges']),
            custody_dict['retry_timer'],
            custody_dict.get('retry_count', 0),
            custody_dict.get('max_retries', 10),
            custody_dict['state']
        ))
        self.conn.commit()
    
    def load_custody_record(self, bundle_id: str) -> Optional[Dict[str, Any]]:
        """Load custody record from database."""
        cursor = self.conn.cursor()
        cursor.execute(
            'SELECT * FROM custody_records WHERE bundle_id = ?',
            (bundle_id,)
        )
        row = cursor.fetchone()
        if row:
            record = dict(row)
            record['chunk_ranges'] = json.loads(record['chunk_ranges'])
            return record
        return None
    
    def list_bundles(self) -> List[Dict[str, Any]]:
        """List all bundles."""
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM bundles ORDER BY created_at DESC')
        return [dict(row) for row in cursor.fetchall()]
    
    def list_bundles_by_state(self, state: str) -> List[Dict[str, Any]]:
        """List bundles by state."""
        cursor = self.conn.cursor()
        cursor.execute(
            'SELECT * FROM bundles WHERE state = ? ORDER BY created_at DESC',
            (state,)
        )
        return [dict(row) for row in cursor.fetchall()]
    
    def delete_bundle(self, bundle_id: str) -> None:
        """Delete bundle and all associated data."""
        cursor = self.conn.cursor()
        cursor.execute('DELETE FROM chunks WHERE bundle_id = ?', (bundle_id,))
        cursor.execute('DELETE FROM custody_records WHERE bundle_id = ?', (bundle_id,))
        cursor.execute('DELETE FROM bundles WHERE bundle_id = ?', (bundle_id,))
        self.conn.commit()
    
    def cleanup_expired_bundles(self, current_time: float) -> None:
        """Clean up expired bundles based on TTL."""
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT bundle_id FROM bundles 
            WHERE (julianday('now') - julianday(created_at)) * 86400 > ttl
        ''')
        expired_bundles = [row[0] for row in cursor.fetchall()]
        
        for bundle_id in expired_bundles:
            self.delete_bundle(bundle_id)
    
    def close(self) -> None:
        """Close database connection."""
        self.conn.close()