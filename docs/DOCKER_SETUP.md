# Docker Setup Instructions for Courier

This document provides instructions for setting up and running Courier in Docker containers on Linux systems.

## Prerequisites

- Docker Engine 20.10+
- Docker Compose 1.29+
- At least 2GB RAM available
- Linux system (Ubuntu 20.04+ recommended)

## Quick Start

### 1. Build and Start Nodes

```bash
# Navigate to docker directory
cd docker/

# Create shared directory for test files
mkdir -p shared

# Build and start all nodes
docker-compose up --build -d

# Check that all nodes are running
docker-compose ps
```

### 2. Basic File Transfer Test

```bash
# Create a test file
echo "Hello from Courier!" > shared/test.txt

# Send file from node2 to node1
docker exec courier_node2 courier send --to node1 --dst-port 5000 /shared/test.txt --port 5000

# Check status on node2
docker exec courier_node2 courier status

# Verify file received on node1 (in a real setup, received files would be stored)
docker exec courier_node1 courier status
```

### 3. Test with Network Simulation

```bash
# Install network tools (if not already available)
sudo apt-get update
sudo apt-get install iproute2 tc

# Add 10% packet loss to test reliability
docker exec courier_node1 tc qdisc add dev eth0 root netem loss 10%

# Send a larger file to test reliability
dd if=/dev/urandom of=shared/largefile.bin bs=1M count=10

# Send with FEC enabled
docker exec courier_node2 courier send --to node1 --dst-port 5000 /shared/largefile.bin --port 5000 --fec --wait

# Remove packet loss
docker exec courier_node1 tc qdisc del dev eth0 root netem
```

## Advanced Usage

### Custom Configuration

Create a config file `config/courier_config.yml`:

```yaml
node:
  port: 5000
  node_id: "custom_node"

transfer:
  chunk_size: 1150
  window_size: 64
  base_rto_ms: 50
  ttl_sec: 300

fec:
  enabled: false
  k: 4
  r: 2

custody:
  max_retries: 10
  backoff_base_sec: 2
```

Mount it in docker-compose.yml:

```yaml
volumes:
  - ./shared:/shared
  - ../config/courier_config.yml:/app/config.yml
```

Then use it:

```bash
docker exec courier_node1 courier recv --config /app/config.yml
```

### Running Individual Nodes

```bash
# Build the image first
cd docker/
docker build -t courier:latest -f Dockerfile ..

# Start a single receiver node
docker run -it --rm \
  -p 5000:5000/udp \
  -v $(pwd)/shared:/shared \
  -e PYTHONPATH=/app/src \
  courier:latest \
  courier recv --port 5000 --node-id receiver1

# Start a sender node (in another terminal)
docker run -it --rm \
  -v $(pwd)/shared:/shared \
  -e PYTHONPATH=/app/src \
  --network host \
  courier:latest \
  courier send --to receiver1 --dst-port 5000 /shared/test.txt --port 5001
```

### Network Loss Simulation

For testing reliability features:

```bash
# Add packet loss to specific node
docker exec courier_node1 tc qdisc add dev eth0 root netem loss 15%

# Add delay
docker exec courier_node1 tc qdisc add dev eth0 root netem delay 100ms 20ms

# Add both loss and delay
docker exec courier_node1 tc qdisc add dev eth0 root netem loss 10% delay 50ms

# Remove all network conditions
docker exec courier_node1 tc qdisc del dev eth0 root
```

### Partition Simulation

To test disruption tolerance:

```bash
# Block traffic between two nodes temporarily
docker exec courier_node1 iptables -A INPUT -s 172.20.0.11 -j DROP
docker exec courier_node1 iptables -A OUTPUT -d 172.20.0.11 -j DROP

# Wait 30 seconds, then restore connectivity
sleep 30
docker exec courier_node1 iptables -D INPUT -s 172.20.0.11 -j DROP
docker exec courier_node1 iptables -D OUTPUT -d 172.20.0.11 -j DROP
```

## Monitoring and Debugging

### View Logs

```bash
# View logs from all services
docker-compose logs -f

# View logs from specific node
docker-compose logs -f node1

# View logs with timestamps
docker-compose logs -f -t
```

### Debug Network Traffic

```bash
# Capture UDP traffic on port 5000
docker exec courier_node1 tcpdump -i eth0 -n udp port 5000

# Monitor network interfaces
docker exec courier_node1 netstat -u -l
```

### Performance Testing

```bash
# Create large test file (100MB)
dd if=/dev/urandom of=shared/bigfile.bin bs=1M count=100

# Time the transfer
time docker exec courier_node2 courier send --to node1 --dst-port 5000 /shared/bigfile.bin --port 5000 --wait --timeout 300

# Test with FEC
time docker exec courier_node2 courier send --to node1 --dst-port 5000 /shared/bigfile.bin --port 5000 --fec --wait --timeout 300
```

## Development

### Running Tests

```bash
# Run tests inside container
docker exec courier_node1 python -m pytest /app/tests/ -v

# Run tests on host (if you have Python environment)
cd ..
python -m pytest tests/ -v
```

### Installing Package

```bash
# Install in development mode
pip install -e .

# Or install from source
python setup.py install
```

## Cleanup

```bash
# Stop all services
docker-compose down

# Remove containers and networks
docker-compose down --volumes --remove-orphans

# Clean up images
docker image rm courier:latest
```

## Troubleshooting

### Port Already in Use

```bash
# Check what's using the port
sudo lsof -i :5000

# Or use different ports in docker-compose.yml
```

### Permission Issues

```bash
# Fix shared directory permissions
sudo chown -R $USER:$USER shared
chmod 755 shared
```

### Network Connectivity Issues

```bash
# Check container networking
docker network ls
docker network inspect docker_courier_net

# Test connectivity between containers
docker exec courier_node1 ping 172.20.0.11
```

### Log Analysis

```bash
# Check for errors in logs
docker-compose logs | grep -i error

# Increase logging verbosity
docker exec courier_node1 courier recv --debug
```

## Building for Production

For production deployments:

1. Use multi-stage builds to reduce image size
2. Set proper resource limits in docker-compose.yml
3. Use Docker secrets for any sensitive configuration
4. Consider using Docker Swarm or Kubernetes for orchestration
5. Implement proper health checks
6. Use persistent volumes for database storage

Example production docker-compose.yml additions:

```yaml
services:
  node1:
    # ... existing config ...
    deploy:
      resources:
        limits:
          memory: 512M
        reservations:
          memory: 256M
    healthcheck:
      test: ["CMD", "python", "-c", "import socket; socket.socket(socket.AF_INET, socket.SOCK_DGRAM).bind(('', 5000))"]
      interval: 30s
      timeout: 10s
      retries: 3
    restart: unless-stopped
```