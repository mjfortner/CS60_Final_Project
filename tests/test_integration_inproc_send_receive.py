import os
from courier.config import TransferConfig, FECConfig
from courier.storage import SQLiteStorage
from courier.send_engine import SendEngine
from courier.receive_engine import ReceiveEngine


class Link:
    """
    Simulated in-process 'link' between sender and receiver.
    Sender's network_send pushes into to_receiver.
    Receiver's network_send pushes into to_sender.
    """
    def __init__(self):
        self.to_receiver = []
        self.to_sender = []

    def send_to_receiver(self, msg, addr):
        self.to_receiver.append((msg, addr))
        return True

    def send_to_sender(self, msg, addr):
        self.to_sender.append((msg, addr))
        return True


def _drive_once(send_engine, recv_engine, link, bundle_id):
    """
    One event loop tick: deliver messages in each direction.
    """
    while link.to_receiver:
        msg, addr = link.to_receiver.pop(0)
        if msg["msg_type"] == "DATA":
            recv_engine.handle_data(msg, addr)

    while link.to_sender:
        msg, addr = link.to_sender.pop(0)
        if msg["msg_type"] == "SACK":
            send_engine.handle_sack(msg, addr)
        elif msg["msg_type"] == "DELIVERED":
            send_engine.handle_delivered(msg)


def test_inproc_send_receive_no_fec(tmp_path):
    """
    Full pipeline:
      - SendEngine chunks a file and sends DATA
      - ReceiveEngine stores chunks, sends SACKs, assembles file
      - ReceiveEngine sends DELIVERED
      - SendEngine marks bundle complete

    All in one process, no real UDP.
    """
    db_path = tmp_path / "inproc.db"
    storage = SQLiteStorage(str(db_path))

    transfer_cfg = TransferConfig(
        chunk_size=16,
        window_size=4,
        base_rto_ms=50,
        ttl_sec=300,
        max_rto_ms=5000,
    )
    fec_cfg = FECConfig(enabled=False, k=0, r=0)

    link = Link()

    sender = SendEngine(
        config=transfer_cfg,
        fec_config=fec_cfg,
        storage=storage,
        network_send_func=link.send_to_receiver,
    )
    receiver = ReceiveEngine(
        transfer_config=transfer_cfg,
        fec_config=fec_cfg,
        storage=storage,
        network_send_func=link.send_to_sender,
        node_id="nodeB",
        output_dir=str(tmp_path / "recv"),
    )

    content = b"Hello Courier! This is an end-to-end in-process test."
    file_path = tmp_path / "send_file.bin"
    file_path.write_bytes(content)

    bundle_id = sender.send_file(
        file_path=str(file_path),
        destination="nodeB",
        dest_addr=("127.0.0.1", 9999),
        fec_enabled=False,
    )

    for _ in range(200): 
        _drive_once(sender, receiver, link, bundle_id)
        state = sender.active_sends[bundle_id]
        if state.completed:
            break

    assert sender.active_sends[bundle_id].completed

    recv_dir = tmp_path / "recv"
    out_path = os.path.join(str(recv_dir), f"bundle_{bundle_id}.bin")
    assert os.path.exists(out_path)
    assert open(out_path, "rb").read() == content

    storage.close()