#!/usr/bin/env python3

import argparse
import logging
import sys
import os
import json

from .node import create_node


def setup_logging(debug: bool = False) -> None:
    """Setup logging configuration."""
    level = logging.DEBUG if debug else logging.INFO
    
    filename = "courier_debug.log" if debug else None
    
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        filename=filename,
        filemode='w' 
    )
    
    if debug:
        print(f"Debug logging enabled. Writing to {filename}...")


# ---------------------------------------------------------------------------

def cmd_send(args) -> int:
    """Handle send command."""
    try:
        node = create_node(
            port=args.port,
            node_id=args.node_id,
            config_path=args.config,
        )
        setup_logging(args.debug)
        node.start()

        if not os.path.exists(args.file):
            print(f"Error: File '{args.file}' not found")
            node.stop()
            return 1

        dest_parts = args.to.split(":")
        if len(dest_parts) == 1:
            dest_node = dest_parts[0]
            dest_host = dest_parts[0]  
            dest_port = args.dst_port
        elif len(dest_parts) == 2:
            dest_node = dest_parts[0]
            dest_host = dest_parts[0]
            dest_port = int(dest_parts[1])
        else:
            print("Error: Invalid destination format. Use 'node_id' or 'node_id:port'")
            node.stop()
            return 1

        print(f"Sending {args.file} to {dest_node} at {dest_host}:{dest_port}")

        bundle_id = node.send_file(
            file_path=args.file,
            destination_node=dest_node,
            dest_host=dest_host,
            dest_port=dest_port,
            fec_enabled=args.fec,
        )

        print(f"Bundle ID: {bundle_id}")

        if args.wait:
            print("Waiting for completion...")
        completed = node.wait_for_completion(bundle_id, timeout=args.timeout)

        if completed:
            print("Transfer completed successfully!")
            status = node.get_send_status(bundle_id)
            if status:
                print(f"Bytes sent: {status.get('bytes_sent', 0)}")
                print(
                    f"Chunks retransmitted: {status.get('chunks_retransmitted', 0)}"
                )
        else:
            print("Transfer did not complete within timeout")
            node.stop()
            return 1

        node.stop()
        return 0

    except Exception as e:
        print(f"Error: {e}")
        return 1


def cmd_recv(args) -> int:
    """Handle recv command."""
    try:
        node = create_node(
            port=args.port,
            node_id=args.node_id,
            config_path=args.config,
        )
        setup_logging(args.debug)
        node.start()

        print(f"Courier receiver started on port {args.port}")
        print(f"Node ID: {node.node_id}")
        print("Listening for incoming transfers... (Press Ctrl+C to stop)")

        node.run_receiver()
        return 0

    except KeyboardInterrupt:
        print("\nShutting down receiver...")
        return 0
    except Exception as e:
        print(f"Error: {e}")
        return 1


def cmd_status(args) -> int:
    """Handle status command."""
    try:
        node = create_node(
            port=args.port,
            node_id=args.node_id,
            config_path=args.config,
        )
        setup_logging(args.debug)

        if args.bundle_id:
            status = node.get_send_status(args.bundle_id)
            if not status:
                print(f"Bundle {args.bundle_id} not found")
                return 1

            print(f"Bundle ID: {args.bundle_id}")
            print(f"Source: {status.get('src', 'N/A')}")
            print(f"Destination: {status.get('dst', 'N/A')}")
            print(f"File: {status.get('file_path', 'N/A')}")
            print(f"File Size: {status.get('file_size', 0)} bytes")
            print(f"State: {status.get('state', 'unknown')}")
            print(f"FEC Enabled: {status.get('fec_enabled', False)}")
            print(f"Total Chunks: {status.get('total_chunks', 0)}")
            print(f"Acknowledged: {status.get('acked_chunks', 0)}")
            print(f"Progress: {status.get('progress', 0):.1%}")
            print(f"Bytes Sent: {status.get('bytes_sent', 0)}")
            print(
                f"Chunks Retransmitted: {status.get('chunks_retransmitted', 0)}"
            )
            print(f"Completed: {status.get('completed', False)}")
            if not status.get("completed", False):
                print(
                    f"Window: [{status.get('window_start', 0)}, "
                    f"{status.get('window_end', 0)})"
                )
                print(f"RTT: {status.get('smoothed_rtt', 0):.1f} ms")
                print(f"Timeout: {status.get('timeout_interval', 0):.1f} ms")
        else:
            status = node.get_send_status()
            bundles = status.get("bundles", [])

            if not bundles:
                print("No bundles found")
                return 0

            print(f"Found {len(bundles)} bundle(s):\n")
            for bundle in bundles:
                print(f"Bundle ID: {bundle.get('bundle_id', 'N/A')[:8]}...")
                print(f"  File: {bundle.get('file_path', 'N/A')}")
                print(f"  Destination: {bundle.get('dst', 'N/A')}")
                print(f"  State: {bundle.get('state', 'unknown')}")
                print(f"  Progress: {bundle.get('progress', 0):.1%}")
                print(f"  Size: {bundle.get('file_size', 0)} bytes")
                print(f"  Created: {bundle.get('created_at', 'N/A')}")
                print()

        if args.json:
            print("\nJSON:")
            print(json.dumps(status, indent=2))

        return 0

    except Exception as e:
        print(f"Error: {e}")
        return 1


def main() -> int:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Courier: Delay/Disruption-Tolerant Reliable File Transfer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Start receiver
  courier --port 5000 --node-id receiver recv 

  # Send file
  courier --port 5001 --node-id sender send --to 127.0.0.1:5000 /tmp/courier_128kb.bin --wait      

  # Check status
  courier status --port 5001

  # Send with FEC and wait for completion
  courier --port 5001 --node-id sender send --to 127.0.0.1:5000 /tmp/courier_20mb.bin --wait --fec
""",
    )

    parser.add_argument(
        "--port",
        type=int,
        default=5000,
        help="Local port to bind to (default: 5000)",
    )
    parser.add_argument(
        "--node-id",
        type=str,
        help="Node ID (default: hostname)",
    )
    parser.add_argument(
        "--config",
        type=str,
        help="Configuration file path",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Send command
    send_parser = subparsers.add_parser("send", help="Send a file")
    send_parser.add_argument(
        "--to",
        required=True,
        help="Destination node (format: node_id or node_id:port)",
    )
    send_parser.add_argument("file", help="File to send")
    send_parser.add_argument(
        "--dst-port",
        type=int,
        default=5000,
        help="Destination port (default: 5000)",
    )
    send_parser.add_argument(
        "--fec",
        action="store_true",
        help="Enable Forward Error Correction",
    )
    send_parser.add_argument(
        "--wait",
        action="store_true",
        help="Wait explicitly for transfer completion",
    )
    send_parser.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        help="Timeout for completion in seconds (default: 300)",
    )

    subparsers.add_parser("recv", help="Start receiver")

    status_parser = subparsers.add_parser("status", help="Show transfer status")
    status_parser.add_argument(
        "--bundle-id",
        type=str,
        help="Show status for specific bundle",
    )
    status_parser.add_argument(
        "--json",
        action="store_true",
        help="Output status in JSON format",
    )

    args = parser.parse_args()

    if args.command == "send":
        return cmd_send(args)
    elif args.command == "recv":
        return cmd_recv(args)
    elif args.command == "status":
        return cmd_status(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())