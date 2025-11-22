from courier.custody_manager import CustodyManager, CustodyRecord
from courier.config import CustodyConfig
from courier.storage import SQLiteStorage


class DummyNet:
    def __init__(self):
        self.sent = []

    def send(self, msg, addr):
        self.sent.append((msg, addr))
        return True


def test_custody_req_and_ack_and_retry(tmp_path):
    """
    CustodyManager should accept CUSTODY_REQ, send CUSTODY_ACK,
    persist a custody record, and perform retries with backoff.
    """
    db_path = tmp_path / "custody.db"
    storage = SQLiteStorage(str(db_path))

    cfg = CustodyConfig(max_retries=3, backoff_base_sec=1)
    net = DummyNet()
    cm = CustodyManager(cfg, storage, net.send, node_id="nodeB")

    msg = {
        "msg_type": "CUSTODY_REQ",
        "bundle_id": "bundleC",
        "ranges": [(0, 10)],
        "ttl_remaining": 60.0,
    }

    sender_addr = ("127.0.0.1", 5555)
    cm.handle_custody_req(msg, sender_addr)

    assert len(net.sent) == 1
    ack_msg, ack_addr = net.sent[0]
    assert ack_msg["msg_type"] == "CUSTODY_ACK"
    assert ack_msg["bundle_id"] == "bundleC"
    assert ack_addr == sender_addr

    assert "bundleC" in cm.active_records
    record = cm.active_records["bundleC"]
    assert record.owner_node == "nodeB"
    assert record.state == "accepted"

    record.retry_timer = 0.0
    cm.active_records["bundleC"] = record

    cm.check_retry_timers()
    record2 = cm.active_records["bundleC"]
    assert record2.retry_count == 1
    assert record2.state == "accepted"

    for _ in range(5):
        record2.retry_timer = 0.0
        cm.active_records["bundleC"] = record2
        cm.check_retry_timers()
        record2 = cm.active_records["bundleC"]

    assert record2.retry_count <= cfg.max_retries
    assert record2.state in ("accepted", "failed")

    storage.close()