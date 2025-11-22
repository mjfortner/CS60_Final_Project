import time
import logging
from dataclasses import dataclass
from typing import Dict, Any, List, Tuple

from .config import CustodyConfig
from .storage import SQLiteStorage
from .protocol import make_custody_ack_msg

logger = logging.getLogger(__name__)


@dataclass
class CustodyRecord:
    bundle_id: str
    owner_node: str
    chunk_ranges: List[Tuple[int, int]]
    retry_timer: float
    retry_count: int = 0
    max_retries: int = 10
    state: str = "pending"  


class CustodyManager:
    """
    Manages custody transfer records for bundles.

    Allows a node to take responsibility (custody) for a bundle.
    If a node accepts custody, it promises to ensure the bundle reaches
    the next hop, releasing the previous sender from that duty.
    """

    def __init__(
        self,
        config: CustodyConfig,
        storage: SQLiteStorage,
        network_send_func,
        node_id: str,
    ):
        self.config = config
        self.storage = storage
        self.network_send = network_send_func
        self.node_id = node_id

        # In-memory view of bundles we currently have custody of
        self.active_records: Dict[str, CustodyRecord] = {}

    # ------------------------------------------------------------------
    # Receiver Side: Accepting Custody
    # ------------------------------------------------------------------

    def handle_custody_req(
        self, msg: Dict[str, Any], sender_addr: Tuple[str, int]
    ) -> None:
        """
        Handle CUSTODY_REQ: 
        1. Accept custody (persist the vow to deliver).
        2. Send CUSTODY_ACK to release the sender.
        """
        bundle_id = msg["bundle_id"]
        ranges = msg.get("ranges", [])
        ttl_remaining = msg.get("ttl_remaining", 0.0)

        now = time.time()
        retry_timer = now + self.config.backoff_base_sec

        record = CustodyRecord(
            bundle_id=bundle_id,
            owner_node=self.node_id,  
            chunk_ranges=[tuple(r) for r in ranges],
            retry_timer=retry_timer,
            retry_count=0,
            max_retries=self.config.max_retries,
            state="accepted",
        )

        self._save_record(record)

        logger.info(
            "Accepted custody for bundle %s from %s (ttl_remaining=%.1f)",
            bundle_id,
            sender_addr,
            float(ttl_remaining),
        )

        ack_msg = make_custody_ack_msg(bundle_id, ranges)
        self.network_send(ack_msg, sender_addr)

    # ------------------------------------------------------------------
    # Sender Side: Releasing Custody
    # ------------------------------------------------------------------

    def handle_custody_ack(self, msg: Dict[str, Any]) -> None:
        """
        Received CUSTODY_ACK. 
        The downstream node has taken responsibility. We can mark our copy 
        as 'custody_transferred' and stop worrying about it.
        """
        bundle_id = msg["bundle_id"]
        ack_nonce = msg.get("ack_nonce")
        ranges = msg.get("ranges", [])

        self.storage.update_bundle_state(bundle_id, "custody_transferred")

        logger.info(
            "Custody transfer CONFIRMED for bundle %s (Nonce: %s). Ownership released.",
            bundle_id,
            ack_nonce,
        )

    def handle_delivered(self, msg: Dict[str, Any]) -> None:
        """
        Received DELIVERED msg (End-to-End confirmation).
        Mark any local custody records as complete.
        """
        bundle_id = msg["bundle_id"]
        record = self.active_records.get(bundle_id)
        
        if record and record.state not in ("complete", "failed"):
            record.state = "complete"
            self._save_record(record)
            logger.info(
                "Custody complete for bundle %s (Final Delivery Confirmation)", bundle_id
            )

    # ------------------------------------------------------------------
    # Periodic Maintenance
    # ------------------------------------------------------------------

    def check_retry_timers(self) -> None:
        """
        Periodically called by Node to manage bundles we hold custody of.
        In a full routing system, this would trigger forwarding to the next hop.
        """
        now = time.time()
        for bundle_id, record in list(self.active_records.items()):
            if record.state in ("complete", "failed"):
                continue

            if now >= record.retry_timer:
                if record.retry_count >= record.max_retries:
                    record.state = "failed"
                    self._save_record(record)
                    logger.warning(
                        "Custody forwarding for bundle %s failed after %d retries",
                        bundle_id,
                        record.retry_count,
                    )
                    continue

                record.retry_count += 1
                backoff = (2 ** record.retry_count) * self.config.backoff_base_sec
                record.retry_timer = now + backoff
                self._save_record(record)

                logger.info(
                    "Custody retry logic active for bundle %s (attempt %d, next check in %.1fs)",
                    bundle_id,
                    record.retry_count,
                    backoff,
                )

    def _save_record(self, record: CustodyRecord) -> None:
        """Persist record to SQLite and update memory cache."""
        data = {
            "bundle_id": record.bundle_id,
            "owner_node": record.owner_node,
            "chunk_ranges": record.chunk_ranges,
            "retry_timer": record.retry_timer,
            "retry_count": record.retry_count,
            "max_retries": record.max_retries,
            "state": record.state,
        }
        self.storage.save_custody_record(data)
        self.active_records[record.bundle_id] = record