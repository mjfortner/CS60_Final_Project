# Development Guide

This guide covers development workflows and architecture details for the Courier project.

## Development Setup

### Prerequisites

- Python 3.7+
- Git
- Docker (optional but recommended for testing)

### Environment Setup

```bash
# Clone the repository
git clone <repository>
cd CS60_Project

# Install in development mode
pip install -e .

# Verify installation
courier --help
```

### Development Dependencies

```bash
# Install development dependencies
pip install -e .[dev]

# This includes:
# - pytest for testing
# - pytest-cov for coverage
# - black for code formatting
# - flake8 for linting
# - mypy for type checking
```

## Project Architecture

### Module Organization

```
src/courier/
├── __init__.py          # Package exports
├── cli.py              # Command-line interface
├── config.py           # Configuration management
├── network_io.py       # UDP networking layer
├── node.py             # Node orchestrator
├── protocol.py         # Message protocols
├── send_engine.py      # Send engine with reliability
├── storage.py          # SQLite storage implementation
└── storage_api.py      # Storage interface definition
```

### Key Components

#### Node Orchestrator (`node.py`)
- Central coordinator for all components
- Event-driven architecture with 10ms tick loop
- Manages lifecycle of SendEngine and NetworkIO
- Handles message routing

#### SendEngine (`send_engine.py`)
- Implements Selective Repeat ARQ protocol
- File chunking with configurable size
- Sliding window flow control
- Exponential backoff retransmission
- RTT estimation (RFC 6298)
- FEC parity generation

#### NetworkIO (`network_io.py`)
- UDP socket management
- Binary message serialization
- Threaded receive loop
- Message dispatching

#### Storage Layer (`storage.py`)
- SQLite-based persistence
- Bundle and chunk storage
- Transfer state tracking
- Recovery support

### Message Protocol

The protocol supports 5 message types:

1. **DATA**: Chunk transfer with metadata
2. **SACK**: Selective acknowledgment
3. **DELIVERED**: Final delivery confirmation
4. **CUSTODY_REQ**: Custody transfer request (placeholder)
5. **CUSTODY_ACK**: Custody acceptance (placeholder)

Each message uses efficient binary serialization for network transmission.

## Development Workflows

### Running Tests

```bash
# Run all tests
python3 -m pytest tests/ -v

# Run with coverage
python3 -m pytest tests/ --cov=courier --cov-report=html

# Run specific test file
python3 tests/test_sending.py

# Run in Docker environment
cd docker/
docker-compose up --build -d
docker exec courier_node1 python -m pytest /app/tests/ -v
```

### Code Quality

```bash
# Format code
black src/ tests/

# Check linting
flake8 src/ tests/

# Type checking
mypy src/courier/
```

### Adding New Features

1. **Write tests first** in `tests/`
2. **Implement the feature** in appropriate module
3. **Update documentation** if needed
4. **Run full test suite** to ensure no regressions
5. **Test with Docker** for integration testing

### Testing Network Conditions

Use Docker for network simulation:

```bash
cd docker/
docker-compose up --build -d

# Add packet loss
docker exec courier_node1 tc qdisc add dev eth0 root netem loss 10%

# Add delay
docker exec courier_node1 tc qdisc add dev eth0 root netem delay 100ms

# Test file transfer with conditions
docker exec courier_node2 courier send --to node1 /shared/test.txt --port 5000 --wait

# Remove conditions
docker exec courier_node1 tc qdisc del dev eth0 root
```

## Adding Part 2 Components

When implementing the Receive/DTN Features Path:

### ReceiveEngine (`src/courier/receive_engine.py`)

```python
class ReceiveEngine:
    def __init__(self, config, storage, network_send_func):
        # Initialize receive state tracking
        # FEC decoding capabilities
        # File assembly logic
        
    def handle_data_chunk(self, msg, sender_addr):
        # Validate checksum
        # Store chunk
        # Attempt FEC reconstruction
        # Send SACK
        # Assemble file if complete
        
    def handle_fec_reconstruction(self, block_id):
        # XOR reconstruction from available chunks
        # Mark reconstructed chunks as received
```

### CustodyManager (`src/courier/custody_manager.py`)

```python
class CustodyManager:
    def __init__(self, config, storage, network_send_func):
        # Custody record management
        # Retry scheduling
        
    def handle_custody_request(self, msg, sender_addr):
        # Accept custody for bundle ranges
        # Send CUSTODY_ACK
        # Schedule retry timers
        
    def check_retry_timers(self):
        # Forward chunks for custody records
        # Exponential backoff
```

### Integration Points

1. **Update Node.py**: Add receive engine and custody manager
2. **Update CLI**: Add receive-specific commands and options
3. **Update Storage**: Add receive state and custody tables
4. **Update Tests**: Add comprehensive receive path testing

### Message Flow for Part 2

```
Sender -> DATA -> Receiver
Receiver -> SACK -> Sender
Receiver -> DELIVERED -> Sender (when complete)

Relay Path:
Sender -> CUSTODY_REQ -> Relay
Relay -> CUSTODY_ACK -> Sender
Relay -> DATA -> Receiver (with custody)
Receiver -> DELIVERED -> Relay
```

## Performance Optimization

### Profiling

```python
import cProfile
import pstats

# Profile send operations
cProfile.run('send_engine.send_file(...)', 'send_profile.stats')

# Analyze results
stats = pstats.Stats('send_profile.stats')
stats.sort_stats('cumtime')
stats.print_stats(10)
```

### Bottleneck Areas

1. **Chunk Serialization**: Use binary protocols
2. **Database Operations**: Batch inserts/updates
3. **Network I/O**: Minimize system calls
4. **FEC Computation**: Optimize XOR operations
5. **Timer Management**: Use efficient data structures

## Configuration Management

### Adding New Config Options

1. **Update config.py** dataclass
2. **Update example_config.yml**
3. **Use in relevant module**
4. **Add to documentation**

### Environment Variables

Priority order:
1. Command-line arguments
2. Environment variables
3. Configuration file
4. Default values

## Debugging

### Logging

```python
import logging

# Configure detailed logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Module-specific logging
logger = logging.getLogger(__name__)
logger.debug("Detailed debug message")
```

### Network Debugging

```bash
# Capture packets
tcpdump -i lo -n udp port 5000 -X

# Monitor socket usage
netstat -aun | grep 5000

# Check process network usage
lsof -i :5000
```

### Database Debugging

```bash
# Connect to SQLite database
sqlite3 courier_node1_5000.db

# Inspect tables
.tables
.schema bundles
SELECT * FROM bundles;
```

## Contributing Guidelines

1. **Follow PEP 8** style guidelines
2. **Write comprehensive tests** for new features
3. **Update documentation** for API changes
4. **Use type hints** for all function signatures
5. **Keep functions small** and focused
6. **Handle errors gracefully** with appropriate logging

## Release Process

1. **Update version** in `setup.py` and `src/courier/__init__.py`
2. **Run full test suite** including integration tests
3. **Update documentation** with new features
4. **Create Docker images** for deployment
5. **Tag release** in version control