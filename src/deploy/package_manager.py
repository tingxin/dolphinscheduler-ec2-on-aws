"""
DolphinScheduler package download and management
"""
import os
import time
import tempfile
import urllib.request
from pathlib import Path
from src.deploy.ssh import execute_remote_command, execute_script, upload_file
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


def download_dolphinscheduler(version, cache_dir='/tmp/ds-cache', download_url=None):
    """
    Download DolphinScheduler package
    
    Args:
        version: DolphinScheduler version
        cache_dir: Cache directory
        download_url: Custom download URL (optional)
    
    Returns:
        Path to downloaded file
    """
    import hashlib
    
    cache_path = Path(cache_dir)
    cache_path.mkdir(parents=True, exist_ok=True)
    
    package_name = f"apache-dolphinscheduler-{version}-bin.tar.gz"
    local_file = cache_path / package_name
    
    # Check if cached file exists and is valid
    if local_file.exists():
        logger.info(f"Found cached package: {local_file}")
        
        # Verify file integrity by checking if it's a valid gzip
        try:
            import gzip
            with gzip.open(str(local_file), 'rb') as f:
                f.read(1024)  # Try to read first 1KB
            logger.info(f"✓ Cached package is valid")
            return str(local_file)
        except Exception as e:
            logger.warning(f"Cached package is corrupted: {e}")
            logger.info("Removing corrupted file and re-downloading...")
            local_file.unlink()
    
    # Determine download URL
    if download_url:
        url = download_url
    else:
        # Try available mirrors
        mirrors = [
            f"https://archive.apache.org/dist/dolphinscheduler/{version}/{package_name}",
            f"https://dlcdn.apache.org/dolphinscheduler/{version}/{package_name}",
            f"https://repo.huaweicloud.com/apache/dolphinscheduler/{version}/{package_name}"
        ]
        url = mirrors[0]
    
    logger.info(f"Downloading from: {url}")
    
    try:
        # Download with progress
        import urllib.request
        
        def download_progress(block_num, block_size, total_size):
            downloaded = block_num * block_size
            if total_size > 0:
                percent = min(downloaded * 100.0 / total_size, 100)
                print(f"\rDownload progress: {percent:.1f}%", end='', flush=True)
        
        urllib.request.urlretrieve(url, str(local_file), reporthook=download_progress)
        print()  # New line after progress
        
        # Verify downloaded file
        logger.info("Verifying downloaded file...")
        import gzip
        with gzip.open(str(local_file), 'rb') as f:
            f.read(1024)  # Try to read first 1KB
        
        file_size = local_file.stat().st_size
        logger.info(f"✓ Downloaded and verified: {local_file} ({file_size / 1024 / 1024:.1f} MB)")
        return str(local_file)
        
    except Exception as e:
        # Clean up partial download
        if local_file.exists():
            local_file.unlink()
        raise Exception(f"Download failed: {str(e)}")


def download_and_extract_remote(ssh, config):
    """
    Download and extract DolphinScheduler package on remote node
    
    Args:
        ssh: SSH connection
        config: Configuration dictionary
    
    Returns:
        Path to extracted directory
    """
    version = config['deployment']['version']
    remote_package = f"/tmp/apache-dolphinscheduler-{version}-bin.tar.gz"
    extract_dir = f"/tmp/apache-dolphinscheduler-{version}-bin"
    
    # Get download URL
    download_url = config.get('advanced', {}).get('download_url')
    if not download_url:
        download_url = f"https://archive.apache.org/dist/dolphinscheduler/{version}/apache-dolphinscheduler-{version}-bin.tar.gz"
    
    logger.info(f"Download URL: {download_url}")
    
    # Download and extract
    download_script = f"""
    # Check if already downloaded and extracted
    if [ -d {extract_dir} ]; then
        echo "Package already extracted, skipping download"
        exit 0
    fi
    
    # Download
    echo "Downloading from {download_url}..."
    cd /tmp
    wget -O {remote_package} {download_url} || curl -L -o {remote_package} {download_url}
    
    # Verify and extract
    if gzip -t {remote_package}; then
        echo "Download successful, extracting..."
        tar -xzf {remote_package} -C /tmp/
        echo "Extraction completed"
    else
        echo "Downloaded file is corrupted"
        rm -f {remote_package}
        exit 1
    fi
    """
    
    execute_script(ssh, download_script, sudo=False)
    logger.info("✓ Package downloaded and extracted")
    
    return extract_dir


def upload_and_extract_package(ssh, package_file, version):
    """
    Upload and extract DolphinScheduler package to remote node
    
    Args:
        ssh: SSH connection
        package_file: Local package file path
        version: DolphinScheduler version
    
    Returns:
        Path to extracted directory
    """
    remote_package = f"/tmp/apache-dolphinscheduler-{version}-bin.tar.gz"
    extract_dir = f"/tmp/apache-dolphinscheduler-{version}-bin"
    
    logger.info("Uploading DolphinScheduler package from local...")
    upload_file(ssh, package_file, remote_package, show_progress=True)
    
    # Extract
    execute_remote_command(ssh, f"cd /tmp && tar -xzf {remote_package}")
    logger.info("✓ Package uploaded and extracted")
    
    return extract_dir


def install_mysql_jdbc_driver(ssh, extract_dir, deploy_user):
    """
    Install MySQL JDBC driver to all component libs directories
    
    Args:
        ssh: SSH connection
        extract_dir: DolphinScheduler extracted directory
        deploy_user: Deployment user
    
    Returns:
        True if successful
    """
    logger.info("Installing MySQL JDBC driver...")
    
    # Download MySQL JDBC driver
    mysql_drivers = [
        {
            "url": "https://repo1.maven.org/maven2/com/mysql/mysql-connector-j/8.0.33/mysql-connector-j-8.0.33.jar",
            "filename": "mysql-connector-j-8.0.33.jar"
        },
        {
            "url": "https://repo1.maven.org/maven2/mysql/mysql-connector-java/8.0.26/mysql-connector-java-8.0.26.jar",
            "filename": "mysql-connector-java-8.0.26.jar"
        }
    ]
    
    driver_installed = False
    for driver in mysql_drivers:
        try:
            logger.info(f"Downloading MySQL driver: {driver['url']}")
            download_driver_cmd = f"""
            cd /tmp && \
            rm -f {driver['filename']} && \
            (wget -O {driver['filename']} {driver['url']} || curl -L -o {driver['filename']} {driver['url']}) && \
            # Copy to all component libs directories
            sudo cp {driver['filename']} {extract_dir}/master-server/libs/ && \
            sudo cp {driver['filename']} {extract_dir}/worker-server/libs/ && \
            sudo cp {driver['filename']} {extract_dir}/api-server/libs/ && \
            sudo cp {driver['filename']} {extract_dir}/alert-server/libs/ && \
            sudo cp {driver['filename']} {extract_dir}/tools/libs/ && \
            sudo chown {deploy_user}:{deploy_user} {extract_dir}/*/libs/{driver['filename']} && \
            ls -la {extract_dir}/master-server/libs/{driver['filename']}
            """
            
            driver_output = execute_remote_command(ssh, download_driver_cmd)
            logger.info(f"✓ MySQL JDBC driver installed: {driver_output}")
            driver_installed = True
            break
            
        except Exception as driver_e:
            logger.warning(f"Failed to download from {driver['url']}: {driver_e}")
            continue
    
    if not driver_installed:
        logger.error("Failed to install MySQL JDBC driver from all sources")
        raise Exception("Could not install MySQL JDBC driver. Please check network connectivity.")
    
    return True


def setup_package_permissions(ssh, extract_dir, deploy_user):
    """
    Set up package permissions and ownership
    
    Args:
        ssh: SSH connection
        extract_dir: DolphinScheduler extracted directory
        deploy_user: Deployment user
    
    Returns:
        True if successful
    """
    logger.info("Setting up package permissions...")
    setup_script = f"""
    # Set ownership to deploy user
    sudo chown -R {deploy_user}:{deploy_user} {extract_dir}
    
    # Make scripts executable
    sudo chmod +x {extract_dir}/bin/*.sh
    
    # Verify extraction
    ls -la {extract_dir}
    """
    
    execute_script(ssh, setup_script, sudo=False)
    logger.info("✓ Package permissions configured")
    
    return True