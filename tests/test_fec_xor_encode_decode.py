import zlib
from courier.send_engine import SendEngine
from courier.receive_engine import ReceiveEngine
from courier.config import TransferConfig, FECConfig
from courier.storage import SQLiteStorage


class DummyNet:
    def __init__(self):
        self.sent = []

    def send(self, msg, addr):
        self.sent.append((msg, addr))
        return True


def test_fec_xor_encode_decode_single_block(tmp_path):
    """
    Use SendEngine's XOR generator and ReceiveEngine's XOR combiner
    to show that a single missing data chunk in a block can be
    reconstructed.
    """
    db_path = tmp_path / "fec_test.db"
    storage = SQLiteStorage(str(db_path))

    transfer_cfg = TransferConfig(
        chunk_size=4,
        window_size=64,
        base_rto_ms=50,
        ttl_sec=300,
        max_rto_ms=5000,
    )
    fec_cfg = FECConfig(enabled=True, k=3, r=1)

    dummy_net = DummyNet()

    send_engine = SendEngine(
        config=transfer_cfg,
        fec_config=fec_cfg,
        storage=storage,
        network_send_func=dummy_net.send,
    )

    bundle_id = "fec-bundle"
    data_chunks = []
    for i, payload in enumerate([b"AAA", b"BBB", b"CCC"]):
        data_chunks.append(
            {
                "bundle_id": bundle_id,
                "chunk_id": i,
                "is_parity": False,
                "block_id": 0,
                "k": fec_cfg.k,
                "r": fec_cfg.r,
                "payload": payload,
                "checksum": zlib.crc32(payload) & 0xFFFFFFFF,
                "flags": 0,
            }
        )

    parity_chunks = send_engine._generate_fec_chunks(data_chunks, bundle_id)
    assert len(parity_chunks) == fec_cfg.r

    parity_payload = parity_chunks[0]["payload"]

    recv_engine = ReceiveEngine(
        transfer_config=transfer_cfg,
        fec_config=fec_cfg,
        storage=storage,
        network_send_func=dummy_net.send,
        node_id="nodeB",
        output_dir=str(tmp_path / "recv"),
    )

    lost_chunk = data_chunks[-1]
    present_chunks = data_chunks[:-1]

    recovered = recv_engine._xor_bytes(
        parity_payload, [c["payload"] for c in present_chunks]
    )

    assert recovered == lost_chunk["payload"]

    orig_crc = lost_chunk["checksum"]
    rec_crc = zlib.crc32(recovered) & 0xFFFFFFFF
    assert orig_crc == rec_crc

    storage.close()