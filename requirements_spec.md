# Requirements Spec

## Courier: Delay/Disruption-Tolerant Reliable File Transfer over UDP

**Course:** CS60: Computer Networks  
**Team:** Maxwell Fortner and Miftah Meky  
**Date:** Nov. 10, 2025

---

## 1. Big-picture description

Courier is a small transport system that delivers files and messages reliably over UDP even when end-to-end connectivity is intermittent. It adds TCP-like reliability (sequencing, ACKs, timeouts, sliding window, checksums, pacing) and extends it with custody transfer (relays promise to keep trying) and, if time allows, a minimal parity Forward Error Correction (FEC) option. The system will be driven by:

- `courier send --to <nodeID> <path>`
- `courier recv`
- `courier status`

The MVP targets laptop/VM nodes on a campus LAN where links may be lossy or temporarily down (e.g., Wi-Fi toggled, moving between APs).

---

## 2. Stakeholders & primary users

- We, as students, can run controlled experiments (loss, delay, partitions) and produce plots for the presentation.
- Anyone needing dependable transfer on flaky links (e.g., field teams, labs, robotics).

---

## 3. Definitions, assumptions, and scope

- **Node:** a process identified by nodeID (string) bound to a UDP port on localhost/LAN.
- **Bundle:** an application message or file submitted for delivery.
- **Chunk:** a fixed-size fragment of a bundle with headers.
- **Custody:** a relay's promise to persist chunks and retry forwarding until delivered or TTL expires.
- **Contact:** a period when two nodes can exchange UDP packets.
- **Selective ACK (SACK):** receiver reports received chunk ranges/bitmaps.

---

## 4. Functional requirements

### i. Application interface

- The system shall provide a Command Line Interface (CLI) to submit a local file or message to a destination nodeID, check status, and run a receiver.
- The system shall persist in-flight data, so progress survives process restarts.

### ii. Reliability over UDP

- **Packet Sequencing:** data will be split into numbered chunks per bundle to allow for in-order reconstruction.
- **ACKs / SACKs:** receivers will send acknowledgments specifying exact chunks received; senders shall mark them done.
- **Timeouts & Retransmissions:** senders shall start per-chunk (or per-flight) timers and shall retransmit un-ACKed chunks on timeout with backoff.
- **Sliding Window:** the sender shall maintain a Selective-Repeat window to keep multiple chunks in flight.
- **Checksum/Error Detection:** each chunk will include a checksum to detect corruption; bad chunks shall be discarded.
- **Congestion Pacing (if time allows):** the sender may pace sends with a token bucket and back off on loss bursts.

### iii. Delay/Disruption tolerance

- **Custody transfer:** a relay will accept custody for a bundle (or a range of its chunks) and shall retry forwarding until delivery or TTL expiry, independent of the original sender's liveness.
- **Duplicate suppression:** nodes will avoid storing or forwarding duplicate chunks using bundle/chunk IDs.
- **Expiration:** bundles shall carry a TTL; expired data shall be deleted.

### iv. Minimal parity FEC (forward-error correction)

- For each block of k data chunks, the sender shall be able to generate r parity chunks (simple XOR stripes).
- The receiver will reconstruct a block from any k of k+r chunks.
- FEC shall be configurable per transfer (off by default is acceptable).

### v. Observability & operability

- The system shall log per-bundle metrics: bytes sent, chunks retransmitted, completed time, and final status (delivered/expired).
- The system shall expose a simple status view of in-flight/delivered bundles.

---

## 5. Non-functional requirements

- **Performance:** Support at least 10 MB/s goodput on a clean LAN without FEC, and ≥80% of that with FEC enabled.
- **Compatibility:** Runs on macOS and Linux using UDP sockets.
- **Security:** Integrity via checksums is required.
- **Cost:** Uses standard laptops/VMs and free libraries; no new hardware.

---

## 6. Major entities (data & processes)

- **Node process:** owns a Bundle Store, Send Engine, Receive Engine, Custody Manager, and Logger.
- **Bundle:** `{bundle_id, src, dst, ttl, length, created_at, state}`
- **Chunk:** `{bundle_id, chunk_id, total_chunks, block_id, k, r, flags, checksum, payload}`
- **Custody record:** `{bundle_id, owner_node, ranges, acquired_at, retry_timer}`

---

## 7. Business rules (policy constraints)

- A node will not forward a chunk to a neighbor that already advertises it as present.
- A node shall free custody only after receiving a Custody-ACK from a downstream node or final DELIVERED from the destination.
- A sender shall limit in-flight chunks to the window size; window growth beyond a safe default is optional.
- A node shall refuse to store bundles that exceed a configurable per-node storage cap and shall report an error.

---

## 8. Plan for network communications (wire behavior)

### Transport & MTU
- **Transport:** UDP datagrams on a configurable port.
- **MTU:** payload target ≤1200 B.

### Message types (minimum set)

#### DATA: chunk transfer
- **Headers:** msg_type, version, bundle_id, chunk_id, total_chunks, block_id, k, r, flags, checksum.

#### SACK
- **Headers:** msg_type, bundle_id, ack_ranges_or_bitmap, recv_watermark.

#### CUSTODY_REQ: request downstream custody for a bundle/range
- **Headers:** msg_type, bundle_id, ranges, ttl_remaining.

#### CUSTODY_ACK: accept custody for specific ranges
- **Headers:** msg_type, bundle_id, ranges, ack_nonce.

#### DELIVERED: destination notification to upstream
- **Headers:** msg_type, bundle_id.

### Flows

#### Send path
Split file → enqueue chunks → send first window of DATA → receive SACK → slide window; on timeout, retransmit missing chunks.

#### Custody path
Upon first hop success, upstream issues CUSTODY_REQ; downstream replies CUSTODY_ACK. Upstream may delete local copy or keep until further confirmation per policy.

#### FEC path
Transmit k data + r parity per block; receiver reconstructs when any k arrive; still SACKs missing indices so sender can help if parity insufficient.

---

## 9. Acceptance tests (what we will demo)

- **Reliable-UDP core:** with 10–20% random loss and no partitions, a 100 MB file is delivered correctly; retransmissions occur only for missing chunks; checksum errors are detected and corrected via retransmit.
- **Disruption tolerance:** break connectivity for 30–60 s mid-transfer; delivery eventually completes due to custody.
- **FEC advantage:** under 10–20% loss, baseline vs. baseline+FEC comparison shows fewer retransmits and equal or faster completion with FEC.