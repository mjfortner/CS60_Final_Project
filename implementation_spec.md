# Implementation Specification: Courier

## Delay/Disruption-Tolerant Reliable File Transfer over UDP

**Authors:** Maxwell Fortner and Miftah Meky  
**Date:** November 10, 2025

---

## 1. Architecture

Seven modules communicating via Node orchestrator:

- **CLI** (`courier_cli.py`): Parse send, recv, status commands
- **Node** (`node.py`): Orchestrator; manages engines, storage, event loop (10ms ticks)
- **Send Engine** (`send_engine.py`): Chunking, windowing (Selective Repeat), ACK handling, retransmission with RTO backoff
- **Receive Engine** (`receive_engine.py`): Chunk reception, FEC decoding, SACK generation, file assembly
- **Custody Manager** (`custody_manager.py`): Relay custody acceptance, retry scheduling (exponential backoff)
- **Network I/O** (`network_io.py`): UDP socket, message serialization, dispatch
- **Storage** (`storage.py`): SQLite persistence (bundles, chunks, custody records)

---

## 2. Data Structures

### Bundle
```
bundle_id, src, dst, ttl, state, total_chunks, bytes_sent,
chunks_retransmitted, fec_enabled, k, r
```

### Chunk
```
bundle_id, chunk_id, is_parity, block_id, k, r, payload,
checksum (CRC32), flags
```

### SendState
```
window_start, window_end, window_size (64), acked_chunks (Set),
retransmit_queue, chunk_timers (Dict), timeout_interval,
smoothed_rtt, rtt_variance
```

### ReceiveState
```
received_chunks, chunk_data (Dict), fec_enabled,
block_recv_map, reconstructed_chunks
```

### CustodyRecord
```
bundle_id, owner_node, chunk_ranges, retry_timer,
retry_count, max_retries (10), state
```

### Message
```
{msg_type, bundle_id, chunk_id, total_chunks, block_id, k, r,
checksum, payload, [ack_bitmap|chunk_ranges|ack_nonce]}
```

---

## 3. Algorithms

### Send Flow

1. Read file → split into 1150-byte chunks, CRC32 each
2. If FEC: group k chunks/block, XOR-generate r parity chunks
3. SendState: window = [0, min(0+64, total_chunks))
4. Send chunk → set timer (now + RTO)
5. On SACK: parse bitmap, mark acked, advance window_start, send next batch
6. On timeout: add to retransmit queue, backoff RTO × 1.5 (cap 5s), flush queue

### Receive Flow

1. On DATA: validate checksum; if fail → log, skip; if duplicate → skip
2. Store chunk, track block_id
3. If FEC & block has k chunks → XOR-reconstruct missing chunks in block
4. Send SACK: bitmap of (received ∪ reconstructed) chunks
5. All chunks ready: assemble file, write to disk, send DELIVERED

### Custody Flow

1. Relay receives CUSTODY_REQ → create CustodyRecord, accept with CUSTODY_ACK
2. On retry timer: forward chunks toward destination, retry_count++, backoff: 2^retry_count
3. On DELIVERED: mark custody complete
4. On TTL expiry: mark failed

---

## 4. Wire Protocol

### UDP Datagram (max 1200 bytes)

| Message Type | Header (50 B) | Payload (~1150 B) |
|---|---|---|
| **DATA** | msg_type, bundle_id, chunk_id, total_chunks, block_id, k, r, flags, checksum | Chunk payload (≤1150 B) |
| **SACK** | msg_type, bundle_id | Bitmap of acked chunks (bit vector) |
| **CUSTODY_REQ** | msg_type, bundle_id, ranges, ttl_rem | — |
| **CUSTODY_ACK** | msg_type, bundle_id, ack_nonce | — |
| **DELIVERED** | msg_type, bundle_id | — |

---

## 5. Pseudocode: Core Flows

### SendEngine.handle_sack(msg)

```
acked ← parse_bitmap(msg.ack_bitmap)
send_state.acked_chunks ∪= acked

while send_state.window_start ∈ acked:
    send_state.window_start += 1
    send_state.window_end += 1

if send_state.window_start >= total_chunks:
    mark_bundle_delivered()
else:
    send_chunk_window(send_state)
```

### SendEngine.check_timeouts()

```
for each (chunk_id, expiry) in chunk_timers:
    if now ≥ expiry and chunk_id ∉ acked_chunks:
        retransmit_queue.append(chunk_id)
        timeout_interval = min(timeout_interval × 1.5, 5000)
        delete chunk_timers[chunk_id]

flush_retransmit_queue()
```

### ReceiveEngine.handle_data_chunk(msg)

```
if crc32(msg.payload) ≠ msg.checksum:
    log warning, skip
else:
    if msg.chunk_id ∉ received_chunks:
        received_chunks.add(msg.chunk_id)
        chunk_data[msg.chunk_id] = msg.payload

    if fec_enabled:
        block_recv_map[msg.block_id].append(msg.chunk_id)
        if len(block_recv_map[msg.block_id]) ≥ k:
            reconstruct_missing_in_block(msg.block_id)

    if len(received_chunks) + len(reconstructed_chunks) ≥ total_chunks:
        assemble_file()
        send_delivered_notification()
    else:
        send_sack()
```

### CustodyManager.check_retry_timers()

```
for each (bundle_id, custody) in custody_records:
    if now ≥ custody.retry_timer and custody.retry_count < 10:
        forward_chunks_to_destination(custody)
        custody.retry_count += 1
        custody.retry_timer = now + 2^custody.retry_count
```

---

## 6. Error Handling & Persistence

### Errors

- **Checksum mismatch** → skip chunk, request retransmit in SACK
- **Duplicate chunk** → suppress, ignore
- **TTL expiry** → mark bundle expired, cleanup after 1 hour
- **Custody retry limit exceeded** → mark failed

### Persistence (SQLite)

- Every bundle/chunk creation → save to DB immediately
- On node startup: load all "in_flight" bundles, resume sender/receiver state
- On shutdown: flush pending sends, close DB gracefully

---

## 7. Testing

### Unit Tests

- `test_crc32()`
- `test_fec_xor_encode_decode()`
- `test_message_serialize_deserialize()`

### Integration Tests (netem loss injection)

1. **Clean link (0% loss):** 100 MB → delivered, checksum
2. **10% loss:** 100 MB → delivered, retransmits logged, checksum valid
3. **30s break mid-transfer:** custody retry delivers file after reconnect
4. **FEC vs. no-FEC at 10% loss:** measure retransmit count ratio, FEC should reduce by ≥10%

### Acceptance (per Requirements)

- **Reliable UDP:** 10–20% loss, no corruption
- **Disruption:** 30–60 s partition, recovery via custody
- **FEC:** fewer retransmits, ≥80% goodput vs. baseline

---

## 8. Configuration

```yaml
node:
  port: 5000
  node_id: "hostname"

transfer:
  chunk_size: 1150
  window_size: 64
  base_rto_ms: 50
  ttl_sec: 300

fec:
  enabled: false  # Set true for FEC transfers
  k: 4
  r: 2

custody:
  max_retries: 10
  backoff_base_sec: 2
```

---

## 9. Deployment

### Node 1: Listen
```bash
courier recv --port 5000
```

### Node 2: Send
```bash
courier send --to node1 /path/to/file.bin --port 5001 --dst-port 5000
```

### Check status
```bash
courier status --port 5001
```