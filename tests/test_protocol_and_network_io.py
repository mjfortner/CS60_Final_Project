# tests/test_protocol_and_network_io.py

from courier.protocol import (
    make_data_msg,
    make_sack_msg,
    make_custody_req_msg,
    make_custody_ack_msg,
    make_delivered_msg,
)
from courier.network_io import NetworkIO


def _dummy_handler(msg, addr):
    pass


def test_data_serialize_deserialize_roundtrip():
    nio = NetworkIO(port=0, message_handler=_dummy_handler)

    msg = make_data_msg(
        bundle_id="bundle123",
        chunk_id=5,
        total_chunks=10,
        block_id=1,
        k=4,
        r=2,
        checksum=0xDEADBEEF,
        payload_str=b"test-payload",
    )

    data = nio._serialize_message(msg)
    decoded = nio._deserialize_message(data)

    assert decoded["msg_type"] == "DATA"
    assert decoded["bundle_id"] == "bundle123"
    assert decoded["chunk_id"] == 5
    assert decoded["total_chunks"] == 10
    assert decoded["block_id"] == 1
    assert decoded["k"] == 4
    assert decoded["r"] == 2
    assert decoded["checksum"] == 0xDEADBEEF
    assert decoded["payload"] == b"test-payload"


def test_sack_serialize_deserialize_roundtrip():
    nio = NetworkIO(port=0, message_handler=_dummy_handler)

    acked = [0, 1, 2, 5, 7, 10]
    msg = make_sack_msg("bundleS", acked)

    data = nio._serialize_message(msg)
    decoded = nio._deserialize_message(data)

    assert decoded["msg_type"] == "SACK"
    assert decoded["bundle_id"] == "bundleS"
    assert sorted(decoded["acked_chunks"]) == sorted(acked)


def test_custody_and_delivered_serialize_deserialize_roundtrip():
    nio = NetworkIO(port=0, message_handler=_dummy_handler)

    ranges = [(0, 10), (20, 30)]
    creq = make_custody_req_msg("bundleC", ranges, ttl_remaining=123.4)
    data_creq = nio._serialize_message(creq)
    decoded_creq = nio._deserialize_message(data_creq)

    assert decoded_creq["msg_type"] == "CUSTODY_REQ"
    assert decoded_creq["bundle_id"] == "bundleC"
    assert decoded_creq["ranges"] == ranges
    # ttl_remaining is packed as int
    assert isinstance(decoded_creq["ttl_remaining"], int)

    cack = make_custody_ack_msg("bundleC", ranges)
    data_cack = nio._serialize_message(cack)
    decoded_cack = nio._deserialize_message(data_cack)

    assert decoded_cack["msg_type"] == "CUSTODY_ACK"
    assert decoded_cack["bundle_id"] == "bundleC"
    assert decoded_cack["ranges"] == ranges

    dmsg = make_delivered_msg("bundleC")
    data_d = nio._serialize_message(dmsg)
    decoded_d = nio._deserialize_message(data_d)

    assert decoded_d["msg_type"] == "DELIVERED"
    assert decoded_d["bundle_id"] == "bundleC"