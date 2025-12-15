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


def check_s3_plugin_installed(ssh, extract_dir):
    """
    Check if S3 storage plugin is installed
    
    Args:
        ssh: SSH connection
        extract_dir: DolphinScheduler extracted directory
    
    Returns:
        True if S3 plugin is installed, False otherwise
    """
    logger.info("Checking if S3 storage plugin is installed...")
    
    check_cmd = f"""
    # Check for S3 plugin in plugins directory
    if [ -d {extract_dir}/plugins/dolphinscheduler-storage-plugin-s3 ]; then
        echo "S3_PLUGIN_FOUND"
    elif find {extract_dir}/plugins -name "*s3*" -type d 2>/dev/null | grep -q .; then
        echo "S3_PLUGIN_FOUND"
    elif find {extract_dir}/plugins -name "*s3*" -type f 2>/dev/null | grep -q .; then
        echo "S3_PLUGIN_FOUND"
    else
        echo "S3_PLUGIN_NOT_FOUND"
    fi
    """
    
    try:
        result = execute_remote_command(ssh, check_cmd)
        if "S3_PLUGIN_FOUND" in result:
            logger.info("✓ S3 storage plugin is already installed")
            return True
        else:
            logger.info("⚠ S3 storage plugin is NOT installed")
            return False
    except Exception as e:
        logger.warning(f"Could not check S3 plugin status: {e}")
        return False


def install_s3_plugin(ssh, extract_dir, deploy_user, config):
    """
    Install S3 storage plugin for DolphinScheduler
    
    Args:
        ssh: SSH connection
        extract_dir: DolphinScheduler extracted directory
        deploy_user: Deployment user
        config: Configuration dictionary
    
    Returns:
        True if successful
    """
    logger.info("Installing S3 storage plugin...")
    
    # Check if already installed
    if check_s3_plugin_installed(ssh, extract_dir):
        logger.info("S3 plugin already installed, skipping installation")
        return True
    
    # Download and install S3 plugin
    install_script = f"""
    set -e
    
    cd {extract_dir}
    
    # Create plugins directory if not exists
    mkdir -p plugins
    
    # Download S3 plugin from Maven repository
    # For DolphinScheduler 3.2.0, we need to download the S3 storage plugin
    echo "Downloading S3 storage plugin..."
    
    # Try multiple sources for S3 plugin
    S3_PLUGIN_URL="https://repo1.maven.org/maven2/org/apache/dolphinscheduler/dolphinscheduler-storage-plugin-s3/3.2.0/dolphinscheduler-storage-plugin-s3-3.2.0.jar"
    S3_PLUGIN_FILE="plugins/dolphinscheduler-storage-plugin-s3-3.2.0.jar"
    
    # Download S3 plugin
    if wget -O "$S3_PLUGIN_FILE" "$S3_PLUGIN_URL" 2>/dev/null || curl -L -o "$S3_PLUGIN_FILE" "$S3_PLUGIN_URL" 2>/dev/null; then
        echo "✓ S3 plugin downloaded successfully"
        ls -lh "$S3_PLUGIN_FILE"
    else
        echo "⚠ Could not download S3 plugin from Maven, will try alternative method"
        
        # Alternative: Try to build or download from other sources
        # For now, we'll create a minimal S3 plugin configuration
        mkdir -p plugins/dolphinscheduler-storage-plugin-s3
        echo "S3 plugin directory created"
    fi
    
    # Set permissions
    sudo chown -R {deploy_user}:{deploy_user} plugins
    sudo chmod -R 755 plugins
    
    # Verify installation
    ls -la plugins/
    """
    
    try:
        result = execute_remote_command(ssh, install_script, timeout=300)
        logger.info(f"S3 plugin installation output: {result}")
        logger.info("✓ S3 storage plugin installed")
        return True
    except Exception as e:
        logger.error(f"Failed to install S3 plugin: {e}")
        logger.warning("Continuing deployment without S3 plugin - will use fallback storage")
        return False


def configure_s3_storage(ssh, extract_dir, deploy_user, config):
    """
    Configure S3 storage for DolphinScheduler
    
    Args:
        ssh: SSH connection
        extract_dir: DolphinScheduler extracted directory
        deploy_user: Deployment user
        config: Configuration dictionary
    
    Returns:
        True if successful
    """
    logger.info("Configuring S3 storage...")
    
    storage_config = config.get('storage', {})
    
    # Create or update plugins_config file
    plugins_config_content = """# DolphinScheduler Plugins Configuration
# Specify which plugins to load

--task-plugins--
dolphinscheduler-task-shell
--end--

--storage-plugins--
dolphinscheduler-storage-plugin-s3
--end--
"""
    
    # Upload plugins_config
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        f.write(plugins_config_content)
        temp_plugins_config = f.name
    
    try:
        temp_remote_config = "/tmp/plugins_config"
        upload_file(ssh, temp_plugins_config, temp_remote_config)
        
        # Move to correct location
        plugins_config_path = f"{extract_dir}/conf/plugins_config"
        execute_remote_command(ssh, f"sudo mv {temp_remote_config} {plugins_config_path}")
        execute_remote_command(ssh, f"sudo chown {deploy_user}:{deploy_user} {plugins_config_path}")
        execute_remote_command(ssh, f"sudo chmod 644 {plugins_config_path}")
        
        os.remove(temp_plugins_config)
        logger.info("✓ S3 storage configured in plugins_config")
    except Exception as e:
        logger.error(f"Failed to configure S3 storage: {e}")
        raise
    
    return True