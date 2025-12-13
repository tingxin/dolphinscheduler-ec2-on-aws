"""
Test configuration management
"""
import pytest
import tempfile
from pathlib import Path
from src.config import load_config, save_config, ConfigVersionManager


def test_load_config():
    """Test loading configuration"""
    # Create temp config file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        f.write("""
database:
  host: localhost
  port: 3306
cluster:
  master:
    count: 2
""")
        temp_file = f.name
    
    try:
        config = load_config(temp_file)
        assert config['database']['host'] == 'localhost'
        assert config['cluster']['master']['count'] == 2
    finally:
        Path(temp_file).unlink()


def test_save_config():
    """Test saving configuration"""
    config = {
        'database': {'host': 'localhost'},
        'cluster': {'master': {'count': 2}}
    }
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        temp_file = f.name
    
    try:
        save_config(temp_file, config)
        loaded = load_config(temp_file)
        assert loaded['database']['host'] == 'localhost'
    finally:
        Path(temp_file).unlink()


def test_config_version_manager():
    """Test configuration version management"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        f.write("test: value")
        temp_file = f.name
    
    try:
        manager = ConfigVersionManager(temp_file)
        
        # Backup
        backup_file = manager.backup_current_config()
        assert backup_file is not None
        assert Path(backup_file).exists()
        
        # List versions
        versions = manager.list_versions()
        assert len(versions) >= 1
        
    finally:
        Path(temp_file).unlink()
        # Cleanup backup dir
        backup_dir = Path(temp_file).parent / '.config_backups'
        if backup_dir.exists():
            for f in backup_dir.glob('*'):
                f.unlink()
            backup_dir.rmdir()
