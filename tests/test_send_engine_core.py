from courier.send_engine import SendEngine
from courier.config import TransferConfig, FECConfig
from courier.storage import SQLiteStorage


class DummyNet:
    def __init__(self):
        self.sent = []

    def send(self, msg, addr):
        self.sent.append((msg, addr))
        return True


def test_send_file_creates_bundle_and_chunks(tmp_path):
    """
    send_file should create a bundle record, chunk records, and send
    at least one DATA message into the network function.
    """
    db_path = tmp_path / "send_core.db"
    storage = SQLiteStorage(str(db_path))

    transfer_cfg = TransferConfig(
        chunk_size=8,
        window_size=4,
        base_rto_ms=50,
        ttl_sec=300,
        max_rto_ms=5000,
    )
    fec_cfg = FECConfig(enabled=False, k=0, r=0)

    net = DummyNet()
    send_engine = SendEngine(
        config=transfer_cfg,
        fec_config=fec_cfg,
        storage=storage,
        network_send_func=net.send,
    )

    file_path = tmp_path / "small.bin"
    file_path.write_bytes(b"Hello Courier!")

    bundle_id = send_engine.send_file(
        file_path=str(file_path),
        destination="nodeB",
        dest_addr=("127.0.0.1", 6000),
        fec_enabled=False,
    )


    bundle = storage.load_bundle(bundle_id)
    assert bundle is not None
    assert bundle["dst"] == "nodeB"
    assert bundle["total_chunks"] > 0

    chunks = storage.load_chunks_for_bundle(bundle_id)
    assert len(chunks) == bundle["total_chunks"]

    assert any(m[0]["msg_type"] == "DATA" for m in net.sent)

    storage.close()


def test_sendengine_timeout_backoff_and_retransmit_queue(tmp_path):
    """
    Force all chunk timers to be 'expired' and ensure SendEngine
    moves chunks to retransmit_queue and increases timeout_interval.
    """
    db_path = tmp_path / "send_timeout.db"
    storage = SQLiteStorage(str(db_path))

    transfer_cfg = TransferConfig(
        chunk_size=4,
        window_size=4,
        base_rto_ms=50,
        ttl_sec=300,
        max_rto_ms=1000,
    )
    fec_cfg = FECConfig(enabled=False, k=0, r=0)

    net = DummyNet()
    send_engine = SendEngine(
        config=transfer_cfg,
        fec_config=fec_cfg,
        storage=storage,
        network_send_func=net.send,
    )

    file_path = tmp_path / "timeout.bin"
    file_path.write_bytes(b"ABCDEFGH") 

    bundle_id = send_engine.send_file(
        file_path=str(file_path),
        destination="nodeB",
        dest_addr=("127.0.0.1", 6000),
        fec_enabled=False,
    )

    state = send_engine.active_sends[bundle_id]
    original_rto = state.timeout_interval

    for cid in list(state.chunk_timers.keys()):
        state.chunk_timers[cid] = 0.0

    send_engine.check_timeouts()

    assert len(state.retransmit_queue) > 0

    assert state.timeout_interval > original_rto

    storage.close()