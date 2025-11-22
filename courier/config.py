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
    window_size: int = 1024
    base_rto_ms: int = 900
    ttl_sec: int = 300
    max_rto_ms: int = 500
    pacing_delay_ms: int = 0

@dataclass
class FECConfig:
    enabled: bool = True
    k: int = 4
    r: int = 2

@dataclass
class CustodyConfig:
    max_retries: int = 10
    backoff_base_sec: int = 2

@dataclass
class StorageConfig:
    db_path: Optional[str] = None
    cleanup_interval_sec: int = 60
    max_bytes: Optional[int] = None 

@dataclass
class CourierConfig:
    node: NodeConfig
    transfer: TransferConfig
    fec: FECConfig
    custody: CustodyConfig
    storage: StorageConfig  

def load_config(config_path: Optional[str] = None) -> CourierConfig:
    """Load configuration from file or use defaults."""
    if config_path and os.path.exists(config_path):
        with open(config_path, 'r') as f:
            config_data = yaml.safe_load(f) or {}
        
        node_config = NodeConfig(**config_data.get('node', {}))
        transfer_config = TransferConfig(**config_data.get('transfer', {}))
        fec_config = FECConfig(**config_data.get('fec', {}))
        custody_config = CustodyConfig(**config_data.get('custody', {}))
        storage_config = StorageConfig(**config_data.get('storage', {}))
    else:
        node_config = NodeConfig()
        transfer_config = TransferConfig()
        fec_config = FECConfig()
        custody_config = CustodyConfig()
        storage_config = StorageConfig()
    
    if node_config.node_id == "localhost":
        import socket
        node_config.node_id = socket.gethostname()
    
    return CourierConfig(
        node=node_config,
        transfer=transfer_config,
        fec=fec_config,
        custody=custody_config,
        storage=storage_config,
    )