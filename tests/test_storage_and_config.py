import os
import zlib
from courier.storage import SQLiteStorage


def test_crc32_known_value():
    """
    Sanity check: CRC32 of 'hello world' should be stable, so our
    checksum logic matches what we expect.
    """
    data = b"hello world"
    crc = zlib.crc32(data) & 0xFFFFFFFF
    assert crc == 0x0D4A1185 


def test_storage_bundle_chunk_roundtrip(tmp_path):
    """
    Ensure bundles and chunks can be saved and loaded from SQLiteStorage.
    """
    db_path = tmp_path / "courier_test.db"
    storage = SQLiteStorage(str(db_path))

    bundle = {
        "bundle_id": "bundle-1",
        "src": "nodeA",
        "dst": "nodeB",
        "ttl": 300,
        "state": "sending",
        "total_chunks": 2,
        "bytes_sent": 123,
        "chunks_retransmitted": 1,
        "fec_enabled": False,
        "k": 0,
        "r": 0,
        "file_path": "/tmp/fake.bin",
        "file_size": 999,
    }
    storage.save_bundle(bundle)

    chunk0 = {
        "bundle_id": "bundle-1",
        "chunk_id": 0,
        "is_parity": False,
        "block_id": 0,
        "k": 0,
        "r": 0,
        "payload": b"abc",
        "checksum": "deadbeef",
        "flags": 0,
    }
    chunk1 = {
        "bundle_id": "bundle-1",
        "chunk_id": 1,
        "is_parity": False,
        "block_id": 0,
        "k": 0,
        "r": 0,
        "payload": b"def",
        "checksum": "cafebabe",
        "flags": 0,
    }
    storage.save_chunk(chunk0)
    storage.save_chunk(chunk1)

    loaded_bundle = storage.load_bundle("bundle-1")
    assert loaded_bundle is not None
    assert loaded_bundle["bundle_id"] == "bundle-1"
    assert loaded_bundle["src"] == "nodeA"
    assert loaded_bundle["dst"] == "nodeB"
    assert loaded_bundle["total_chunks"] == 2

    chunks = storage.load_chunks_for_bundle("bundle-1")
    assert len(chunks) == 2
    assert chunks[0]["chunk_id"] == 0
    assert chunks[1]["chunk_id"] == 1

    # List API
    all_bundles = storage.list_bundles()
    assert any(b["bundle_id"] == "bundle-1" for b in all_bundles)

    storage.close()
    assert os.path.exists(db_path)