# Courier: Delay/Disruption-Tolerant Reliable File Transfer

A reliable file transfer system built on UDP with support for disruption tolerance, Forward Error Correction (FEC), and custody transfer mechanisms.

## Overview

Courier implements part 1 of the CS60 project specification, focusing on the **Send/Control Path** which includes:

- ✅ CLI interface for send, recv, status commands
- ✅ Node orchestration with event loop management  
- ✅ SendEngine with chunking, windowing, ACK handling, and retransmissions
- ✅ NetworkIO for UDP socket communication and message serialization
- ✅ Storage layer with SQLite persistence
- ✅ Configuration management system
- ✅ Unit tests for sending functionality

## Project Structure

```
CS60_Project/
├── src/courier/           # Core package modules
│   ├── __init__.py       # Package initialization
│   ├── cli.py           # Command-line interface
│   ├── node.py          # Node orchestrator
│   ├── send_engine.py   # Send engine with reliability
│   ├── network_io.py    # UDP networking layer
│   ├── storage.py       # SQLite persistence
│   ├── config.py        # Configuration management
│   ├── protocol.py      # Message protocol
│   └── storage_api.py   # Storage interface
├── tests/               # Unit tests
│   ├── __init__.py
│   └── test_sending.py  # Send path tests
├── docker/              # Docker setup
│   ├── Dockerfile
│   ├── docker-compose.yml
│   └── shared/          # Shared files for containers
├── docs/                # Documentation
│   └── DOCKER_SETUP.md  # Docker instructions
├── config/              # Configuration files
│   └── example_config.yml
├── bin/                 # Executable scripts
│   └── courier          # CLI wrapper
├── setup.py            # Package setup
├── requirements.txt    # Python dependencies
└── README.md          # This file
```

## Quick Start

### Installation

#### Option 1: Development Installation
```bash
# Clone and install in development mode
git clone <repository>
cd CS60_Project
pip install -e .

# Now 'courier' command is available system-wide
courier recv --port 5000
```

#### Option 2: Direct Usage
```bash
# Use the wrapper script
./bin/courier recv --port 5000

# Or run the module directly
python -m courier.cli recv --port 5000
```

#### Option 3: Docker (Recommended for Linux)
```bash
cd docker/
docker-compose up --build -d
```

### Basic Usage

1. **Start a receiver:**
```bash
courier recv --port 5000
```

2. **Send a file (from another terminal):**
```bash
courier send --to localhost:5000 /path/to/file.txt --port 5001
```

3. **Check status:**
```bash
courier status --port 5001
```

### Docker Usage

See [docs/DOCKER_SETUP.md](docs/DOCKER_SETUP.md) for complete Docker instructions.

```bash
# Quick start with Docker
cd docker/
docker-compose up --build -d
mkdir -p shared
echo "Hello Courier!" > shared/test.txt

# Send file between containers
docker exec courier_node2 courier send --to node1 /shared/test.txt --port 5000
```

## Features Implemented

### Send Engine (send_engine.py)
- **File Chunking**: Splits files into configurable-size chunks (default 1150 bytes)
- **Selective Repeat ARQ**: Maintains sliding window of in-flight chunks
- **SACK Processing**: Handles Selective Acknowledgments from receivers
- **Timeout & Retransmission**: Implements exponential backoff for failed chunks
- **RTT Estimation**: Tracks round-trip times using RFC 6298 algorithm
- **FEC Generation**: Creates XOR parity chunks for error correction
- **Statistics Tracking**: Monitors bytes sent and retransmission counts

### Network I/O (network_io.py)
- **UDP Communication**: Handles all network socket operations
- **Message Serialization**: Efficient binary protocol for DATA/SACK/CUSTODY messages
- **Threading**: Non-blocking receive loop with timeout handling
- **Message Dispatch**: Routes incoming messages to appropriate handlers

### Storage Layer (storage.py)
- **SQLite Persistence**: Stores bundles, chunks, and custody records
- **Bundle Management**: Tracks transfer state and metadata
- **Recovery Support**: Enables resuming interrupted transfers
- **Cleanup**: Automatic removal of expired bundles

### Node Orchestrator (node.py)
- **Event Loop**: 10ms tick-based event processing
- **Engine Coordination**: Manages SendEngine and NetworkIO interactions
- **Timeout Management**: Periodic checks for chunk retransmissions
- **Status Reporting**: Provides comprehensive transfer status

### CLI Interface (courier_cli.py)
- **Send Command**: Initiate file transfers with various options
- **Recv Command**: Start receiver daemon
- **Status Command**: Query transfer status and statistics
- **Configuration**: Support for YAML config files
- **Logging**: Configurable debug output

## Architecture

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   CLI Interface │    │  Node Orchestrator │    │  Configuration  │
│  (courier_cli)  │◄──►│    (node.py)      │◄──►│   (config.py)   │
└─────────────────┘    └──────────┬───────┘    └─────────────────┘
                                  │
                    ┌─────────────┼─────────────┐
                    ▼             ▼             ▼
         ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
         │   Send Engine   │ │   Network I/O   │ │     Storage     │
         │ (send_engine.py)│ │ (network_io.py) │ │  (storage.py)   │
         └─────────────────┘ └─────────────────┘ └─────────────────┘
                    │             │                       │
                    └─────────────┼───────────────────────┘
                                  │
                            ┌─────────────────┐
                            │     Protocol    │
                            │  (protocol.py)  │
                            └─────────────────┘
```

## Protocol Messages

The implementation supports the following message types:

- **DATA**: Chunk transfer with headers and payload
- **SACK**: Selective acknowledgment with received chunk bitmap
- **DELIVERED**: Final delivery confirmation
- **CUSTODY_REQ**: Request custody transfer (placeholder)
- **CUSTODY_ACK**: Accept custody transfer (placeholder)

## Configuration

Default configuration in `config.py`:

```python
@dataclass
class TransferConfig:
    chunk_size: int = 1150        # Chunk payload size
    window_size: int = 64         # Sliding window size
    base_rto_ms: int = 50         # Base retransmission timeout
    ttl_sec: int = 300            # Bundle time-to-live
    max_rto_ms: int = 5000        # Maximum timeout

@dataclass  
class FECConfig:
    enabled: bool = False         # Enable Forward Error Correction
    k: int = 4                    # Data chunks per block
    r: int = 2                    # Parity chunks per block
```

## Testing

Run unit tests:

```bash
# If installed with pip install -e .
python -m pytest tests/ -v

# Or run directly
python tests/test_sending.py

# In Docker
docker exec courier_node1 python -m pytest /app/tests/ -v
```

The test suite covers:
- File chunking with and without FEC
- SACK message handling
- Timeout and retransmission logic
- Message serialization/deserialization
- Storage operations

## Limitations & Future Work

This implementation focuses on the **Send/Control Path** (Part 1). The following components are planned for Part 2:

- **ReceiveEngine**: Handle incoming data chunks and file assembly
- **CustodyManager**: Implement relay custody and forwarding
- **FEC Decoding**: Reconstruct missing chunks from parity data
- **Integration Testing**: End-to-end transfer validation

## Performance Characteristics

Based on the specification requirements:

- **Target Performance**: 10 MB/s goodput on clean LAN
- **FEC Overhead**: ≥80% of baseline performance when enabled
- **MTU Compliance**: 1200-byte UDP payload limit
- **Memory Usage**: Configurable window size limits in-flight data
- **Persistence**: All transfer state survives process restarts

## Contributing

This is a CS60 course project. For questions or issues:

1. Check the implementation specification
2. Review the requirements specification  
3. Run unit tests to verify functionality
4. Use Docker setup for multi-node testing

## License

Academic project - see course guidelines for usage restrictions.