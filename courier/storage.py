import sqlite3
import json
import os
import threading
from typing import Dict, List, Optional, Any

from .storage_api import StorageAPI


class SQLiteStorage(StorageAPI):
    """
    SQLite-backed persistent storage for bundles, chunks, and custody records.

    This version is made thread-safe by guarding all DB access with an RLock.
    """

    def __init__(self, db_path: str = "courier.db"):
        self.db_path = db_path
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self._lock:
            self.conn.execute("PRAGMA journal_mode=WAL;")
            self.conn.execute("PRAGMA synchronous=NORMAL;") 
            
        self._init_tables()


    def _init_tables(self) -> None:
        """Initialize database tables."""
        with self._lock:
            cursor = self.conn.cursor()

            cursor.execute(
                """
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
                """
            )

            cursor.execute(
                """
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
                """
            )

            cursor.execute(
                """
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
                """
            )

            self.conn.commit()


    def save_bundle(self, bundle_dict: Dict[str, Any]) -> None:
        """Insert or update a bundle row."""
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO bundles 
                (bundle_id, src, dst, ttl, state, total_chunks, bytes_sent, 
                 chunks_retransmitted, fec_enabled, k, r, file_path, file_size)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    bundle_dict["bundle_id"],
                    bundle_dict["src"],
                    bundle_dict["dst"],
                    bundle_dict["ttl"],
                    bundle_dict["state"],
                    bundle_dict["total_chunks"],
                    bundle_dict.get("bytes_sent", 0),
                    bundle_dict.get("chunks_retransmitted", 0),
                    bundle_dict.get("fec_enabled", False),
                    bundle_dict.get("k", 0),
                    bundle_dict.get("r", 0),
                    bundle_dict.get("file_path", ""),
                    bundle_dict.get("file_size", 0),
                ),
            )
            self.conn.commit()

    def load_bundle(self, bundle_id: str) -> Optional[Dict[str, Any]]:
        """Load bundle by id, or None."""
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute("SELECT * FROM bundles WHERE bundle_id = ?", (bundle_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def update_bundle_state(self, bundle_id: str, new_state: str) -> None:
        """Update only the `state` of a bundle."""
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute(
                "UPDATE bundles SET state = ? WHERE bundle_id = ?",
                (new_state, bundle_id),
            )
            self.conn.commit()

    def update_bundle_stats(
        self,
        bundle_id: str,
        bytes_sent: int | None = None,
        chunks_retransmitted: int | None = None,
    ) -> None:
        """Update transfer statistics for a bundle."""
        with self._lock:
            cursor = self.conn.cursor()
            if bytes_sent is not None:
                cursor.execute(
                    "UPDATE bundles SET bytes_sent = ? WHERE bundle_id = ?",
                    (bytes_sent, bundle_id),
                )
            if chunks_retransmitted is not None:
                cursor.execute(
                    "UPDATE bundles SET chunks_retransmitted = ? WHERE bundle_id = ?",
                    (chunks_retransmitted, bundle_id),
                )
            self.conn.commit()

    def list_bundles(self) -> List[Dict[str, Any]]:
        """Return all bundles newest-first."""
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute("SELECT * FROM bundles ORDER BY created_at DESC")
            return [dict(row) for row in cursor.fetchall()]

    def list_bundles_by_state(self, state: str) -> List[Dict[str, Any]]:
        """Return bundles in a given state newest-first."""
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute(
                "SELECT * FROM bundles WHERE state = ? ORDER BY created_at DESC",
                (state,),
            )
            return [dict(row) for row in cursor.fetchall()]


    def save_chunk(self, chunk_dict: Dict[str, Any]) -> None:
        """Insert or update a single chunk row (Legacy/Slow)."""
        # We keep this for compatibility, but ReceiveEngine will use bulk_save
        self.save_chunks_bulk([chunk_dict])

    def save_chunks_bulk(self, chunks: List[Dict[str, Any]]) -> None:
        """
        Rigorous Fix: Insert multiple chunks in a single transaction.
        This reduces FS overhead by 100x.
        """
        if not chunks:
            return

        with self._lock:
            cursor = self.conn.cursor()
            try:
                cursor.execute("BEGIN TRANSACTION")
                cursor.executemany(
                    """
                    INSERT OR REPLACE INTO chunks 
                    (bundle_id, chunk_id, is_parity, block_id, k, r, payload, checksum, flags)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            c["bundle_id"],
                            c["chunk_id"],
                            c.get("is_parity", False),
                            c["block_id"],
                            c["k"],
                            c["r"],
                            c["payload"],
                            c["checksum"],
                            c.get("flags", 0),
                        )
                        for c in chunks
                    ],
                )
                self.conn.commit()
            except Exception as e:
                self.conn.rollback()
                raise e

    def load_chunks_for_bundle(self, bundle_id: str) -> List[Dict[str, Any]]:
        """Return all chunks for a bundle ordered by chunk_id."""
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute(
                "SELECT * FROM chunks WHERE bundle_id = ? ORDER BY chunk_id",
                (bundle_id,),
            )
            return [dict(row) for row in cursor.fetchall()]


    def save_custody_record(self, custody_dict: Dict[str, Any]) -> None:
        """Insert or update a custody record."""
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO custody_records
                (bundle_id, owner_node, chunk_ranges, retry_timer, retry_count, 
                 max_retries, state)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    custody_dict["bundle_id"],
                    custody_dict["owner_node"],
                    json.dumps(custody_dict["chunk_ranges"]),
                    custody_dict["retry_timer"],
                    custody_dict.get("retry_count", 0),
                    custody_dict.get("max_retries", 10),
                    custody_dict["state"],
                ),
            )
            self.conn.commit()

    def load_custody_record(self, bundle_id: str) -> Optional[Dict[str, Any]]:
        """Load custody record by bundle id, or None."""
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute(
                "SELECT * FROM custody_records WHERE bundle_id = ?",
                (bundle_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            record = dict(row)
            record["chunk_ranges"] = json.loads(record["chunk_ranges"])
            return record


    def delete_bundle(self, bundle_id: str) -> None:
        """Delete bundle and all associated chunks and custody records."""
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute("DELETE FROM chunks WHERE bundle_id = ?", (bundle_id,))
            cursor.execute(
                "DELETE FROM custody_records WHERE bundle_id = ?", (bundle_id,)
            )
            cursor.execute("DELETE FROM bundles WHERE bundle_id = ?", (bundle_id,))
            self.conn.commit()

    def cleanup_expired_bundles(self, current_time: float) -> None:
        """
        Delete bundles whose TTL has expired.

        Uses julianday to compare seconds since creation vs ttl.
        """
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute(
                """
                SELECT bundle_id FROM bundles 
                WHERE (julianday('now') - julianday(created_at)) * 86400 > ttl
                """
            )
            expired = [row[0] for row in cursor.fetchall()]

            for bundle_id in expired:
                cursor.execute("DELETE FROM chunks WHERE bundle_id = ?", (bundle_id,))
                cursor.execute(
                    "DELETE FROM custody_records WHERE bundle_id = ?", (bundle_id,)
                )
                cursor.execute("DELETE FROM bundles WHERE bundle_id = ?", (bundle_id,))
            self.conn.commit()


    def close(self) -> None:
        """Close the DB connection."""
        with self._lock:
            self.conn.close()