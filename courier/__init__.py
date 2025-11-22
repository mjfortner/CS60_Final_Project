"""
Courier: Delay/Disruption-Tolerant Reliable File Transfer over UDP

This package implements a reliable file transfer system with support for:
- TCP-like reliability over UDP (sequencing, ACKs, timeouts, sliding window)
- Forward Error Correction (FEC) using XOR parity
- Custody transfer for disruption tolerance
- SQLite persistence for transfer state
"""

__version__ = "0.1.0"
__author__ = "Maxwell Fortner and Miftah Meky"

from .node import Node, create_node
from .config import CourierConfig, load_config

__all__ = ['Node', 'create_node', 'CourierConfig', 'load_config']

