import os
import yaml
from dataclasses import dataclass
from typing import Optional

@dataclass
class NodeConfig:
    port: int = 5000
    node_id: str = "localhost"

@dataclass 
class TransferConfig:
    chunk_size: int = 1150
    window_size: int = 64
    base_rto_ms: int = 50
    ttl_sec: int = 300
    max_rto_ms: int = 5000

@dataclass
class FECConfig:
    enabled: bool = False
    k: int = 4
    r: int = 2

@dataclass
class CustodyConfig:
    max_retries: int = 10
    backoff_base_sec: int = 2

@dataclass
class CourierConfig:
    node: NodeConfig
    transfer: TransferConfig
    fec: FECConfig
    custody: CustodyConfig

def load_config(config_path: Optional[str] = None) -> CourierConfig:
    """Load configuration from file or use defaults."""
    if config_path and os.path.exists(config_path):
        with open(config_path, 'r') as f:
            config_data = yaml.safe_load(f)
        
        node_config = NodeConfig(**config_data.get('node', {}))
        transfer_config = TransferConfig(**config_data.get('transfer', {}))
        fec_config = FECConfig(**config_data.get('fec', {}))
        custody_config = CustodyConfig(**config_data.get('custody', {}))
    else:
        # Use defaults
        node_config = NodeConfig()
        transfer_config = TransferConfig()
        fec_config = FECConfig()
        custody_config = CustodyConfig()
    
    # Set node_id to hostname if default
    if node_config.node_id == "localhost":
        import socket
        node_config.node_id = socket.gethostname()
    
    return CourierConfig(
        node=node_config,
        transfer=transfer_config,
        fec=fec_config,
        custody=custody_config
    )