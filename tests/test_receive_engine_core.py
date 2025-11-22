import os
import zlib
from courier.receive_engine import ReceiveEngine
from courier.config import TransferConfig, FECConfig
from courier.storage import SQLiteStorage


class DummyNet:
    def __init__(self):
        self.sent = []

    def send(self, msg, addr):
        self.sent.append((msg, addr))
        return True


def test_receive_engine_end_to_end_no_fec(tmp_path):
    """
    Manually feed DATA messages into ReceiveEngine and ensure it
    writes the reconstructed file and marks the bundle as delivered.
    """
    db_path = tmp_path / "recv_core.db"
    storage = SQLiteStorage(str(db_path))

    out_dir = tmp_path / "received"
    transfer_cfg = TransferConfig(
        chunk_size=8,
        window_size=8,
        base_rto_ms=50,
        ttl_sec=300,
        max_rto_ms=5000,
    )
    fec_cfg = FECConfig(enabled=False, k=0, r=0)

    net = DummyNet()
    recv_engine = ReceiveEngine(
        transfer_config=transfer_cfg,
        fec_config=fec_cfg,
        storage=storage,
        network_send_func=net.send,
        node_id="nodeB",
        output_dir=str(out_dir),
    )

    bundle_id = "bundle-recv-1"
    sender_addr = ("127.0.0.1", 5001)

    content = b"Hello Courier! ReceiveEngine test."
    chunk_size = transfer_cfg.chunk_size
    chunks = [
        content[i : i + chunk_size]
        for i in range(0, len(content), chunk_size)
    ]
    total_chunks = len(chunks)

    for chunk_id, payload in enumerate(chunks):
        msg = {
            "msg_type": "DATA",
            "bundle_id": bundle_id,
            "chunk_id": chunk_id,
            "total_chunks": total_chunks,
            "block_id": 0,
            "k": 0,
            "r": 0,
            "checksum": zlib.crc32(payload) & 0xFFFFFFFF,
            "payload": payload,
        }
        recv_engine.handle_data(msg, sender_addr)

    bundle = storage.load_bundle(bundle_id)
    assert bundle is not None
    assert bundle["state"] == "delivered"

    out_path = os.path.join(str(out_dir), f"bundle_{bundle_id}.bin")
    assert os.path.exists(out_path)
    assert open(out_path, "rb").read() == content

    storage.close()