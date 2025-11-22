#!/bin/bash

# ==============================================================================
# Courier DTN - Automated Demo Runner
# ==============================================================================
#
# Usage: ./run_demo.sh [size_in_mb]
# Example for 20MB: ./run_demo.sh 20 
# ==============================================================================

# 1. Configuration
# ------------------------------------------------------------------------------
SIZE=${1:-10}  
FILE_PATH="/tmp/courier_${SIZE}mb.bin"
RECEIVER_LOG="courier_receiver.log"
SENDER_HOST="127.0.0.1"
SENDER_PORT="5000"

if [[ "$OSTYPE" == "darwin"* ]]; then
    MD5_CMD="md5 -q"
else
    MD5_CMD="md5sum | cut -d' ' -f1"
fi

# 2. Environment Setup
# ------------------------------------------------------------------------------
echo "[1/6] Setting up environment..."

if [ -d ".venv" ]; then
    source .venv/bin/activate
else
    echo "❌ Error: .venv folder not found."
    echo "   Please run: python3 -m venv .venv && source .venv/bin/activate && pip install -e ."
    exit 1
fi

# 3. Cleanup
# ------------------------------------------------------------------------------
echo "🧹 [2/6] Cleaning up previous run state..."
pkill -f "courier"               # Kill any running Courier processes
rm -f *.db *.log                 
rm -rf received/                 # Remove old received files
rm -rf __pycache__ */__pycache__ 

# 4. Generate Test Data
# ------------------------------------------------------------------------------
echo "[3/6] Generating ${SIZE}MB random test file at $FILE_PATH..."
dd if=/dev/urandom of="$FILE_PATH" bs=1M count="$SIZE" status=none

# 5. Start Receiver
# ------------------------------------------------------------------------------
echo "[4/6] Starting Receiver on port 5000 (Background)..."
courier --port 5000 --node-id receiver recv > "$RECEIVER_LOG" 2>&1 &
RECEIVER_PID=$!

sleep 2

# 6. Start Sender
# ------------------------------------------------------------------------------
echo "🚀 [5/6] Starting Sender on port 5001..."
echo "   Target: $SENDER_HOST:$SENDER_PORT"
echo "   File Size: ${SIZE}MB"
echo "   Features Enabled: Reliability (ARQ), FEC (XOR), Custody Transfer"
echo "----------------------------------------------------------------"

time courier --port 5001 --node-id sender send \
    --to $SENDER_HOST:$SENDER_PORT \
    "$FILE_PATH" \
    --wait \
    --fec

SENDER_EXIT_CODE=$?
echo "----------------------------------------------------------------"

# 7. Verification and Shutdown
# ------------------------------------------------------------------------------
echo "[6/6] Shutting down..."
kill $RECEIVER_PID

if [ $SENDER_EXIT_CODE -eq 0 ]; then
    echo "✅ Transfer reported success."
    
    echo "🔍 Verifying Integrity (MD5 Hash Comparison)..."
    
    HASH_SRC=$(eval $MD5_CMD "$FILE_PATH")
    
    REC_FILE=$(find received -name "bundle_*.bin" | head -n 1)
    
    if [ -f "$REC_FILE" ]; then
        HASH_DST=$(eval $MD5_CMD "$REC_FILE")
        
        echo "   Source Hash:   $HASH_SRC"
        echo "   Received Hash: $HASH_DST"
        
        if [ "$HASH_SRC" == "$HASH_DST" ]; then
            echo "🎉 SUCCESS: INTEGRITY CONFIRMED."
        else
            echo "❌ FAILURE: Hashes do not match!"
            exit 1
        fi
    else
        echo "❌ Error: No output file found in 'received/' directory."
        exit 1
    fi
else
    echo "❌ Sender exited with error code $SENDER_EXIT_CODE."
    exit 1
fi

# Clean exit
deactivate
exit 0