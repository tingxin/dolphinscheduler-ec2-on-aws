"""
Configuration management
"""
import yaml
import shutil
from pathlib import Path
from datetime import datetime
from deepdiff import DeepDiff
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


def load_config(config_file):
    """
    Load configuration from YAML file
    
    Args:
        config_file: Path to config file
    
    Returns:
        Configuration dictionary
    """
    config_path = Path(config_file)
    
    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_file}")
    
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)
    
    logger.info(f"Configuration loaded from: {config_file}")
    return config


def save_config(config_file, config):
    """
    Save configuration to YAML file
    
    Args:
        config_file: Path to config file
        config: Configuration dictionary
    """
    config_path = Path(config_file)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(config_path, 'w') as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)
    
    logger.info(f"Configuration saved to: {config_file}")


class ConfigVersionManager:
    """Configuration version manager"""
    
    def __init__(self, config_file):
        """
        Initialize version manager
        
        Args:
            config_file: Path to config file
        """
        self.config_file = Path(config_file)
        self.backup_dir = self.config_file.parent / '.config_backups'
        self.backup_dir.mkdir(exist_ok=True)
    
    def backup_current_config(self):
        """
        Backup current configuration
        
        Returns:
            Path to backup file
        """
        if not self.config_file.exists():
            logger.warning(f"Config file does not exist: {self.config_file}")
            return None
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_file = self.backup_dir / f"config_{timestamp}.yaml"
        shutil.copy2(self.config_file, backup_file)
        
        logger.info(f"Configuration backed up: {backup_file}")
        return backup_file
    
    def list_versions(self):
        """
        List all configuration versions
        
        Returns:
            List of version info dictionaries
        """
        backups = sorted(self.backup_dir.glob('config_*.yaml'), reverse=True)
        return [
            {
                'file': str(backup),
                'timestamp': backup.stem.replace('config_', ''),
                'size': backup.stat().st_size
            }
            for backup in backups
        ]
    
    def rollback_to_version(self, version_timestamp):
        """
        Rollback to specified version
        
        Args:
            version_timestamp: Version timestamp
        
        Returns:
            True if successful
        """
        backup_file = self.backup_dir / f"config_{version_timestamp}.yaml"
        
        if not backup_file.exists():
            raise ValueError(f"Version not found: {version_timestamp}")
        
        # Backup current config before rollback
        self.backup_current_config()
        
        # Restore specified version
        shutil.copy2(backup_file, self.config_file)
        logger.info(f"Rolled back to version: {version_timestamp}")
        return True


def analyze_config_diff(old_config, new_config):
    """
    Analyze configuration differences
    
    Args:
        old_config: Old configuration
        new_config: New configuration
    
    Returns:
        Dictionary with change information
    """
    diff = DeepDiff(old_config, new_config, ignore_order=True)
    
    changes = {
        'jvm_changes': [],
        'service_changes': [],
        'cluster_changes': [],
        'requires_restart': False
    }
    
    # Analyze changes
    if 'values_changed' in diff:
        for key, value in diff['values_changed'].items():
            change_info = {
                'path': key,
                'old': value['old_value'],
                'new': value['new_value']
            }
            
            if 'jvm' in key.lower():
                changes['jvm_changes'].append(change_info)
                changes['requires_restart'] = True
            elif 'service_config' in key.lower():
                changes['service_changes'].append(change_info)
                changes['requires_restart'] = True
            elif 'cluster' in key.lower():
                changes['cluster_changes'].append(change_info)
    
    return changes


def print_config_diff(changes):
    """
    Print configuration differences
    
    Args:
        changes: Changes dictionary from analyze_config_diff
    """
    print("\n" + "=" * 70)
    print("Configuration Changes Summary")
    print("=" * 70)
    
    if changes['jvm_changes']:
        print("\n📊 JVM Parameter Changes:")
        for change in changes['jvm_changes']:
            print(f"  • {change['path']}")
            print(f"    Old: {change['old']}")
            print(f"    New: {change['new']}")
    
    if changes['service_changes']:
        print("\n⚙️  Service Configuration Changes:")
        for change in changes['service_changes']:
            print(f"  • {change['path']}")
            print(f"    Old: {change['old']}")
            print(f"    New: {change['new']}")
    
    if changes['cluster_changes']:
        print("\n🖥️  Cluster Configuration Changes:")
        for change in changes['cluster_changes']:
            print(f"  • {change['path']}")
            print(f"    Old: {change['old']}")
            print(f"    New: {change['new']}")
    
    if not any([changes['jvm_changes'], changes['service_changes'], changes['cluster_changes']]):
        print("\n✓ No changes detected")
    
    if changes['requires_restart']:
        print("\n⚠️  These changes require service restart")
    
    print("=" * 70 + "\n")
