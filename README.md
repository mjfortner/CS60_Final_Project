# Courier DTN: User Guide & Demo

**Courier** is a high-performance, reliable file transfer protocol built over UDP. It is designed for Delay-Tolerant Networking (DTN) scenarios.

**Key Features:**
1.  **Reliability:** Sliding Window flow control with Selective Acknowledgments (SACK).
2.  **Disruption Tolerance:** Forward Error Correction (FEC) to recover lost data without retransmission.
3.  **Custody Transfer:** "Store-and-Forward" logic where the receiver accepts responsibility for the data.

---

## Prerequisites

Ensure you are inside the `courier-project` folder.

1.  **Python 3.10+** required.
2.  **Install Dependencies:**
    ```bash
    # Create and activate virtual environment
    python3 -m venv .venv
    source .venv/bin/activate
    
    # Install the package in editable mode
    pip install -r requirements.txt
    pip install -e .
    ```

---

## Option 1: The Automated Demo (Recommended)

We provide a script that handles cleanup, file generation, background processes, and verification automatically.

**To run the 10MB Speed Test:**
```bash
./run_demo.sh 10
To run the 20MB Stress Test:

Bash

./run_demo.sh 20
What this script does:

Generates a random binary file of the requested size.

Starts the Receiver in the background (logging to courier_receiver.log).

Starts the Sender in the foreground with FEC enabled.

Verifies that the MD5 Checksum of the received file matches the original exactly.

Option 2: Manual Execution
If you prefer to run the commands yourself to see how they work, follow these steps.

1. Prepare the Environment
Open two terminal windows. In BOTH windows, navigate to the project folder and activate the environment:

Bash

cd courier-project
source .venv/bin/activate
2. Clean Old Data
Clear any database files from previous runs to avoid conflicts.

Bash

rm *.db
rm -rf received/
3. Generate a Test File
Create a 20MB file filled with random data:

Bash

dd if=/dev/urandom of=/tmp/courier_20mb.bin bs=1M count=20
4. Start the Receiver (Terminal 1)
Run the receiver. It will listen on port 5000 and write to courier_receiver_5000.db.

Bash

courier --port 5000 --node-id receiver recv
5. Start the Sender (Terminal 2)
Run the sender. We use the following flags:

--wait: Keeps the process alive until the transfer is confirmed complete.

--fec: Enables Forward Error Correction (Parity packets).

Bash

courier --port 5001 --node-id sender send \
  --to 127.0.0.1:5000 \
  /tmp/courier_20mb.bin \
  --wait \
  --fec
6. Verify Success
Sender Terminal: You should see:

Transfer completed successfully! INFO - Custody transfer CONFIRMED...

Verification: Check the file integrity manually:

Bash

# Hash of the original file
md5 /tmp/courier_20mb.bin

# Hash of the received file
md5 received/bundle_*.bin
Note: On Linux, use md5sum instead of md5.

##Some Screenshots for Proof
