def make_data_msg(bundle_id, chunk_id, total_chunks,
                  block_id, k, r, checksum, payload_str):
    return {
        "msg_type": "DATA",
        "bundle_id": bundle_id,
        "chunk_id": chunk_id,
        "total_chunks": total_chunks,
        "block_id": block_id,
        "k": k,
        "r": r,
        "checksum": checksum,
        "payload": payload_str
    }

def make_sack_msg(bundle_id, acked_chunks_list):
    return {
        "msg_type": "SACK",
        "bundle_id": bundle_id,
        "acked_chunks": acked_chunks_list 
    }

def make_delivered_msg(bundle_id):
    return {
        "msg_type": "DELIVERED",
        "bundle_id": bundle_id
    }

def make_custody_req_msg(bundle_id, ranges, ttl_remaining):
    return {
        "msg_type": "CUSTODY_REQ",
        "bundle_id": bundle_id,
        "ranges": ranges,          
        "ttl_remaining": ttl_remaining
    }

def make_custody_ack_msg(bundle_id, ranges):
    return {
        "msg_type": "CUSTODY_ACK",
        "bundle_id": bundle_id,
        "ranges": ranges
    }