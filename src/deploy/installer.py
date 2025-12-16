"""
DolphinScheduler 3.2.x installation and deployment (Simplified)
Supports 3.2.0, 3.2.2 and other 3.2.x versions
"""
import os
import time
import tempfile
from src.deploy.ssh import connect_ssh, execute_remote_command, upload_file, execute_script
from src.deploy.config_generator import (
    generate_application_yaml_v320, 
    generate_install_env_v320, 
    generate_dolphinscheduler_env_v320,
    generate_common_properties_v320
)
from src.deploy.package_manager import (
    download_and_extract_remote,
    upload_and_extract_package,
    install_mysql_jdbc_driver,
    setup_package_permissions,
    check_s3_plugin_installed,
    install_s3_plugin,
    configure_s3_storage,
    check_hdfs_connectivity,
    configure_hdfs_storage
)
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


def initialize_database(ssh, config, extract_dir):
    """
    Initialize DolphinScheduler database schema (with duplicate check)
    
    Args:
        ssh: SSH connection
        config: Configuration dictionary
        extract_dir: DolphinScheduler extracted directory
    
    Returns:
        True if successful
    """
    logger.info("Checking database schema status...")
    db_config = config['database']
    deploy_user = config['deployment']['user']
    
    # Test database connectivity and check if schema exists
    try:
        import pymysql
        password = str(db_config['password']) if db_config['password'] is not None else ''
        
        conn = pymysql.connect(
            host=db_config['host'],
            port=db_config.get('port', 3306),
            user=db_config['username'],
            password=password,
            database=db_config['database'],
            connect_timeout=10,
            charset='utf8mb4'
        )
        cursor = conn.cursor()
        
        # Check if DolphinScheduler tables exist
        logger.info("Checking if DolphinScheduler tables already exist...")
        
        # Check for key DolphinScheduler tables
        key_tables = [
            't_ds_user',
            't_ds_project', 
            't_ds_process_definition',
            't_ds_task_definition',
            't_ds_worker_group'
        ]
        
        existing_tables = []
        for table in key_tables:
            cursor.execute(f"SHOW TABLES LIKE '{table}'")
            if cursor.fetchone():
                existing_tables.append(table)
        
        conn.close()
        
        if len(existing_tables) >= 3:  # If most key tables exist
            logger.info(f"✓ Database schema already initialized (found {len(existing_tables)}/{len(key_tables)} key tables)")
            logger.info("Skipping database initialization to avoid conflicts")
            return True
        else:
            logger.info(f"Found {len(existing_tables)}/{len(key_tables)} key tables, proceeding with initialization...")
        
        logger.info("✓ Database connectivity verified")
        
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        raise Exception(f"Cannot connect to database: {e}")
    
    # Run database initialization
    logger.info("Initializing database schema...")
    init_db_cmd = f"cd {extract_dir} && sudo -u {deploy_user} bash tools/bin/upgrade-schema.sh"
    
    try:
        db_output = execute_remote_command(ssh, init_db_cmd, timeout=300)
        logger.info(f"Database initialization output: {db_output}")
        
        if "successfully" in db_output.lower() or "completed" in db_output.lower():
            logger.info("✓ Database schema initialized successfully")
        elif "already exists" in db_output.lower() or "duplicate" in db_output.lower():
            logger.info("✓ Database schema already exists, initialization skipped")
        else:
            logger.warning("Database initialization completed but check output for any issues")
            
        # Verify initialization by checking tables again
        try:
            conn = pymysql.connect(
                host=db_config['host'],
                port=db_config.get('port', 3306),
                user=db_config['username'],
                password=password,
                database=db_config['database'],
                connect_timeout=10,
                charset='utf8mb4'
            )
            cursor = conn.cursor()
            cursor.execute("SHOW TABLES LIKE 't_ds_%'")
            tables = cursor.fetchall()
            conn.close()
            
            if len(tables) > 10:  # DolphinScheduler should have many tables
                logger.info(f"✓ Database verification passed ({len(tables)} DolphinScheduler tables found)")
            else:
                logger.warning(f"⚠ Only {len(tables)} DolphinScheduler tables found, may need manual verification")
                
        except Exception as verify_e:
            logger.warning(f"Could not verify database initialization: {verify_e}")
            
    except Exception as e:
        logger.warning(f"Database initialization may have failed: {e}")
        # Don't fail deployment, as database might already be initialized
        logger.info("Continuing with deployment...")
    
    return True


def configure_components(ssh, config, extract_dir):
    """
    Generate and upload configuration files for all components
    
    Args:
        ssh: SSH connection
        config: Configuration dictionary
        extract_dir: DolphinScheduler extracted directory
    
    Returns:
        True if successful
    """
    logger.info("Generating component configuration files...")
    deploy_user = config['deployment']['user']
    
    components = ['master', 'worker', 'api', 'alert']
    component_dirs = {
        'master': 'master-server',
        'worker': 'worker-server', 
        'api': 'api-server',
        'alert': 'alert-server'
    }
    
    for component in components:
        try:
            logger.info(f"Configuring {component} component...")
            yaml_content = generate_application_yaml_v320(config, component)
            
            with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
                f.write(yaml_content)
                temp_yaml = f.name
            
            # Upload to component directory
            temp_remote_yaml = f"/tmp/application_{component}.yaml"
            upload_file(ssh, temp_yaml, temp_remote_yaml)
            
            # Move to final location
            component_dir = component_dirs[component]
            # Ensure conf directory exists
            execute_remote_command(ssh, f"sudo mkdir -p {extract_dir}/{component_dir}/conf")
            
            final_yaml_path = f"{extract_dir}/{component_dir}/conf/application.yaml"
            execute_remote_command(ssh, f"sudo mv {temp_remote_yaml} {final_yaml_path}")
            execute_remote_command(ssh, f"sudo chown {deploy_user}:{deploy_user} {final_yaml_path}")
            
            os.remove(temp_yaml)
            logger.info(f"✓ {component} configuration completed")
            
        except Exception as e:
            logger.error(f"Failed to configure {component}: {e}")
            raise
    
    # Also configure tools/conf/application.yaml for database operations
    logger.info("Configuring tools component...")
    tools_yaml_content = generate_application_yaml_v320(config, 'master')  # Tools uses same config as master
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        f.write(tools_yaml_content)
        temp_tools_yaml = f.name
    
    try:
        temp_remote_tools_yaml = "/tmp/application_tools.yaml"
        upload_file(ssh, temp_tools_yaml, temp_remote_tools_yaml)
        
        # Ensure conf directory exists
        execute_remote_command(ssh, f"sudo mkdir -p {extract_dir}/tools/conf")
        
        tools_yaml_path = f"{extract_dir}/tools/conf/application.yaml"
        execute_remote_command(ssh, f"sudo mv {temp_remote_tools_yaml} {tools_yaml_path}")
        execute_remote_command(ssh, f"sudo chown {deploy_user}:{deploy_user} {tools_yaml_path}")
        
        os.remove(temp_tools_yaml)
        logger.info("✓ Tools configuration completed")
    except Exception as e:
        logger.error(f"Failed to configure tools: {e}")
        raise
    
    return True


def upload_configuration_files(ssh, config, extract_dir):
    """
    Upload install_env.sh and dolphinscheduler_env.sh configuration files
    
    Args:
        ssh: SSH connection
        config: Configuration dictionary
        extract_dir: DolphinScheduler extracted directory
    
    Returns:
        True if successful
    """
    deploy_user = config['deployment']['user']
    
    # Generate and upload install_env.sh
    logger.info("Generating install_env.sh configuration...")
    install_env_content = generate_install_env_v320(config)
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as f:
        f.write(install_env_content)
        temp_install_env = f.name
    
    try:
        temp_remote_install_env = "/tmp/install_env.sh"
        upload_file(ssh, temp_install_env, temp_remote_install_env)
        
        # Ensure bin/env directory exists
        execute_remote_command(ssh, f"sudo mkdir -p {extract_dir}/bin/env")
        
        # Move to correct location
        install_env_path = f"{extract_dir}/bin/env/install_env.sh"
        execute_remote_command(ssh, f"sudo mv {temp_remote_install_env} {install_env_path}")
        execute_remote_command(ssh, f"sudo chown {deploy_user}:{deploy_user} {install_env_path}")
        execute_remote_command(ssh, f"sudo chmod +x {install_env_path}")
        
        os.remove(temp_install_env)
        logger.info("✓ install_env.sh configured")
    except Exception as e:
        logger.error(f"Failed to upload install_env.sh: {e}")
        raise
    
    # Generate and upload dolphinscheduler_env.sh
    logger.info("Generating dolphinscheduler_env.sh configuration...")
    ds_env_content = generate_dolphinscheduler_env_v320(config)
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as f:
        f.write(ds_env_content)
        temp_ds_env = f.name
    
    try:
        temp_remote_ds_env = "/tmp/dolphinscheduler_env.sh"
        upload_file(ssh, temp_ds_env, temp_remote_ds_env)
        
        # Ensure bin/env directory exists
        execute_remote_command(ssh, f"sudo mkdir -p {extract_dir}/bin/env")
        
        # Move to correct location
        ds_env_path = f"{extract_dir}/bin/env/dolphinscheduler_env.sh"
        execute_remote_command(ssh, f"sudo mv {temp_remote_ds_env} {ds_env_path}")
        execute_remote_command(ssh, f"sudo chown {deploy_user}:{deploy_user} {ds_env_path}")
        execute_remote_command(ssh, f"sudo chmod +x {ds_env_path}")
        
        os.remove(temp_ds_env)
        logger.info("✓ dolphinscheduler_env.sh configured")
    except Exception as e:
        logger.error(f"Failed to upload dolphinscheduler_env.sh: {e}")
        raise
    
    return True


def patch_hdfs_config_post_deploy(ssh, config, install_path, hdfs_address_override=None):
    """
    Patch HDFS configuration in common.properties after deployment
    
    This function is called AFTER the cluster is deployed and services are started.
    It only modifies the HDFS-related settings in api-server's common.properties,
    then restarts the API server.
    
    This approach avoids issues with jar-embedded default configurations.
    
    Args:
        ssh: SSH connection to API server node
        config: Configuration dictionary
        install_path: DolphinScheduler installation path
        hdfs_address_override: HDFS address from core-site.xml
    
    Returns:
        True if successful
    """
    logger.info("Patching HDFS configuration in api-server...")
    deploy_user = config['deployment']['user']
    
    storage_config = config.get('storage', {})
    storage_type = storage_config.get('type', 'LOCAL').upper()
    
    if storage_type != 'HDFS':
        logger.info("Storage type is not HDFS, skipping HDFS config patch")
        return True
    
    hdfs_config = storage_config.get('hdfs', {})
    hdfs_user = hdfs_config.get('user', 'hadoop')
    hdfs_path = hdfs_config.get('upload_path', '/dolphinscheduler')
    
    # Use hdfs_address_override if provided (from core-site.xml)
    if hdfs_address_override:
        hdfs_address = hdfs_address_override
    else:
        namenode_host = hdfs_config.get('namenode_host', 'localhost')
        namenode_port = hdfs_config.get('namenode_port', 8020)
        hdfs_address = f"hdfs://{namenode_host}:{namenode_port}"
    
    logger.info(f"Configuring HDFS: address={hdfs_address}, user={hdfs_user}, path={hdfs_path}")
    
    # Only patch api-server's common.properties (the one that matters for resource upload)
    conf_file = f"{install_path}/api-server/conf/common.properties"
    
    try:
        # Build sed commands - use | as delimiter to avoid issues with /
        patch_script = f"""
        # Patch HDFS configuration
        sudo sed -i 's|resource.storage.type=.*|resource.storage.type=HDFS|g' {conf_file}
        sudo sed -i 's|resource.storage.upload.base.path=.*|resource.storage.upload.base.path={hdfs_path}|g' {conf_file}
        sudo sed -i 's|resource.hdfs.root.user=.*|resource.hdfs.root.user={hdfs_user}|g' {conf_file}
        sudo sed -i 's|resource.hdfs.fs.defaultFS=.*|resource.hdfs.fs.defaultFS={hdfs_address}|g' {conf_file}
        
        # Verify changes
        echo "=== Verifying HDFS configuration ==="
        grep -E "resource.storage.type|resource.hdfs.fs.defaultFS|resource.hdfs.root.user" {conf_file}
        """
        
        output = execute_remote_command(ssh, patch_script, timeout=30)
        logger.info(f"HDFS config patched:\n{output}")
        
        # Restart API server to apply changes
        logger.info("Restarting API server to apply HDFS configuration...")
        restart_script = f"""
        sudo -u {deploy_user} {install_path}/bin/dolphinscheduler-daemon.sh stop api-server
        sleep 3
        sudo -u {deploy_user} {install_path}/bin/dolphinscheduler-daemon.sh start api-server
        """
        execute_remote_command(ssh, restart_script, timeout=60)
        
        logger.info("✓ HDFS configuration applied and API server restarted")
        return True
        
    except Exception as e:
        logger.error(f"Failed to patch HDFS config: {e}")
        raise


def upload_common_properties(ssh, config, extract_dir, hdfs_address_override=None):
    """
    Skip common.properties modification during initial deployment
    
    The original common.properties from the package will be used.
    HDFS configuration will be applied later via patch_hdfs_config_post_deploy().
    """
    logger.info("Skipping common.properties modification (will be patched post-deployment)")
    return True


def create_resource_directories(ssh, config):
    """
    Create resource directories on the node
    
    DolphinScheduler needs /tmp/dolphinscheduler directory for local resource storage.
    
    Args:
        ssh: SSH connection
        config: Configuration dictionary
    
    Returns:
        True if successful
    """
    logger.info("Creating resource directories...")
    deploy_user = config['deployment']['user']
    
    create_dirs_script = f"""
    # Create resource directory for local storage
    sudo mkdir -p /tmp/dolphinscheduler
    sudo chown {deploy_user}:{deploy_user} /tmp/dolphinscheduler
    sudo chmod 755 /tmp/dolphinscheduler
    
    # Create subdirectories
    sudo mkdir -p /tmp/dolphinscheduler/resources
    sudo mkdir -p /tmp/dolphinscheduler/exec
    sudo mkdir -p /tmp/dolphinscheduler/process_exec_dir
    
    # Set permissions
    sudo chown -R {deploy_user}:{deploy_user} /tmp/dolphinscheduler
    sudo chmod -R 755 /tmp/dolphinscheduler
    
    # Verify
    ls -la /tmp/dolphinscheduler
    """
    
    try:
        execute_script(ssh, create_dirs_script, sudo=False)
        logger.info("✓ Resource directories created")
    except Exception as e:
        logger.error(f"Failed to create resource directories: {e}")
        raise
    
    return True


def create_hdfs_directories(ssh, config):
    """
    Create HDFS directories for DolphinScheduler resource storage
    
    Connects to EMR master node to create HDFS directories
    
    Args:
        ssh: SSH connection (can be any node, used to get config)
        config: Configuration dictionary
    
    Returns:
        True if successful
    """
    logger.info("Creating HDFS directories for resource storage...")
    
    hdfs_config = config.get('storage', {}).get('hdfs', {})
    hdfs_path = hdfs_config.get('upload_path', '/dolphinscheduler')
    
    # Get EMR master node info
    emr_config = config.get('emr', {})
    emr_master_host = emr_config.get('master_host', hdfs_config.get('namenode_host', 'localhost'))
    emr_master_user = emr_config.get('master_user', 'hadoop')
    emr_master_key = emr_config.get('master_key_file')
    
    logger.info(f"Connecting to EMR master node: {emr_master_host}")
    
    # Connect to EMR master node
    try:
        emr_ssh = connect_ssh(emr_master_host, emr_master_user, emr_master_key, config=config)
    except Exception as e:
        logger.error(f"Failed to connect to EMR master node {emr_master_host}: {e}")
        raise Exception(f"Cannot connect to EMR master node: {e}")
    
    try:
        # Create HDFS directories
        create_hdfs_dirs_script = f"""
        # Create HDFS directories
        hdfs dfs -mkdir -p {hdfs_path}
        hdfs dfs -chmod 755 {hdfs_path}
        
        # Create subdirectories
        hdfs dfs -mkdir -p {hdfs_path}/default
        hdfs dfs -mkdir -p {hdfs_path}/default/resources
        hdfs dfs -chmod 755 {hdfs_path}/default
        hdfs dfs -chmod 755 {hdfs_path}/default/resources
        
        # Verify directories were created
        echo "HDFS directories created:"
        hdfs dfs -ls {hdfs_path}
        hdfs dfs -ls {hdfs_path}/default
        """
        
        output = execute_remote_command(emr_ssh, create_hdfs_dirs_script, timeout=60)
        logger.info(f"HDFS directories created successfully:\n{output}")
        
        emr_ssh.close()
        return True
        
    except Exception as e:
        logger.error(f"Failed to create HDFS directories: {e}")
        emr_ssh.close()
        raise Exception(f"Failed to create HDFS directories: {e}")


def setup_hadoop_config_on_node(node_ssh, config, host, hadoop_config_files=None):
    """
    Setup Hadoop configuration files on a single node
    
    Uploads pre-downloaded core-site.xml and hdfs-site.xml to:
    1. /etc/hadoop/conf (system-wide)
    2. DolphinScheduler component conf directories (api-server, master-server, worker-server, alert-server, tools)
    
    This is critical for HDFS connectivity - without these files, DolphinScheduler
    defaults to local filesystem instead of HDFS.
    
    Args:
        node_ssh: SSH connection to the node
        config: Configuration dictionary
        host: Host address (for logging)
        hadoop_config_files: Dict with 'core_site' and 'hdfs_site' local file paths
    
    Returns:
        True if successful
    """
    logger.debug(f"Setting up Hadoop config on {host}...")
    
    install_path = config.get('deployment', {}).get('install_path', '/opt/dolphinscheduler')
    deploy_user = config.get('deployment', {}).get('user', 'dolphinscheduler')
    
    if not hadoop_config_files:
        logger.warning(f"No Hadoop config files provided for {host}, skipping")
        return False
    
    try:
        # Upload Hadoop config files to the node
        core_site_local = hadoop_config_files.get('core_site')
        hdfs_site_local = hadoop_config_files.get('hdfs_site')
        
        if not core_site_local or not hdfs_site_local:
            logger.warning(f"Missing Hadoop config files for {host}")
            return False
        
        # Upload files to /tmp on the node
        upload_file(node_ssh, core_site_local, '/tmp/core-site.xml')
        upload_file(node_ssh, hdfs_site_local, '/tmp/hdfs-site.xml')
        
        logger.debug(f"Hadoop config files uploaded to {host}")
        
        # Copy to /etc/hadoop/conf and DolphinScheduler component directories
        install_cmd = f"""
        # Create system-wide Hadoop config directory
        sudo mkdir -p /etc/hadoop/conf
        sudo cp /tmp/core-site.xml /etc/hadoop/conf/
        sudo cp /tmp/hdfs-site.xml /etc/hadoop/conf/
        sudo chmod 644 /etc/hadoop/conf/*.xml
        
        echo "✓ Hadoop config installed to /etc/hadoop/conf"
        
        # Copy to DolphinScheduler component conf directories
        # This is critical for DolphinScheduler to find HDFS configuration
        COMPONENTS="api-server master-server worker-server alert-server tools"
        
        for component in $COMPONENTS; do
            CONF_DIR="{install_path}/$component/conf"
            if [ -d "{install_path}/$component" ]; then
                sudo mkdir -p "$CONF_DIR"
                sudo cp /tmp/core-site.xml "$CONF_DIR/"
                sudo cp /tmp/hdfs-site.xml "$CONF_DIR/"
                sudo chown -R {deploy_user}:{deploy_user} "$CONF_DIR"
                sudo chmod 644 "$CONF_DIR"/*.xml
                echo "✓ Hadoop config copied to $CONF_DIR"
            fi
        done
        
        # Also copy to root conf directory
        sudo mkdir -p {install_path}/conf
        sudo cp /tmp/core-site.xml {install_path}/conf/
        sudo cp /tmp/hdfs-site.xml {install_path}/conf/
        sudo chown -R {deploy_user}:{deploy_user} {install_path}/conf
        sudo chmod 644 {install_path}/conf/*.xml
        echo "✓ Hadoop config copied to {install_path}/conf"
        
        # Clean up
        rm -f /tmp/core-site.xml /tmp/hdfs-site.xml
        
        # Verify installation
        echo "Verifying Hadoop config installation:"
        ls -la /etc/hadoop/conf/*.xml 2>/dev/null || echo "No files in /etc/hadoop/conf"
        ls -la {install_path}/api-server/conf/*.xml 2>/dev/null || echo "No files in api-server/conf"
        """
        
        output = execute_remote_command(node_ssh, install_cmd, timeout=60)
        logger.debug(f"Hadoop config installed on {host}:\n{output}")
        
        return True
        
    except Exception as e:
        logger.error(f"Failed to setup Hadoop config on {host}: {e}")
        raise


def download_hadoop_config_from_emr(config):
    """
    Download Hadoop configuration files from EMR master to local temp directory
    
    Args:
        config: Configuration dictionary
    
    Returns:
        Dict with 'core_site' and 'hdfs_site' local file paths, or None if failed
    """
    logger.info("Downloading Hadoop configuration files from EMR master...")
    
    emr_config = config.get('emr', {})
    hdfs_config = config.get('storage', {}).get('hdfs', {})
    emr_master_host = emr_config.get('master_host', hdfs_config.get('namenode_host', 'localhost'))
    emr_master_user = emr_config.get('master_user', 'hadoop')
    emr_master_key = emr_config.get('master_key_file')
    
    logger.info(f"Connecting to EMR master: {emr_master_host}")
    
    try:
        emr_ssh = connect_ssh(emr_master_host, emr_master_user, emr_master_key, config=config)
    except Exception as e:
        logger.error(f"Failed to connect to EMR master {emr_master_host}: {e}")
        return None
    
    try:
        # Create local temp directory
        import tempfile
        temp_dir = tempfile.mkdtemp(prefix='hadoop_config_')
        core_site_local = os.path.join(temp_dir, 'core-site.xml')
        hdfs_site_local = os.path.join(temp_dir, 'hdfs-site.xml')
        
        # Download files using SFTP
        sftp = emr_ssh.open_sftp()
        
        # Try different paths for Hadoop config
        hadoop_conf_paths = [
            '/etc/hadoop/conf',
            '/opt/hadoop/etc/hadoop',
            '/usr/local/hadoop/etc/hadoop'
        ]
        
        hadoop_conf_dir = None
        for path in hadoop_conf_paths:
            try:
                sftp.stat(f"{path}/core-site.xml")
                hadoop_conf_dir = path
                break
            except FileNotFoundError:
                continue
        
        if not hadoop_conf_dir:
            logger.error("Could not find Hadoop configuration directory on EMR master")
            sftp.close()
            emr_ssh.close()
            return None
        
        logger.info(f"Found Hadoop config at: {hadoop_conf_dir}")
        
        # Download files
        sftp.get(f"{hadoop_conf_dir}/core-site.xml", core_site_local)
        sftp.get(f"{hadoop_conf_dir}/hdfs-site.xml", hdfs_site_local)
        
        sftp.close()
        emr_ssh.close()
        
        # Extract fs.defaultFS from core-site.xml
        hdfs_address = None
        try:
            import xml.etree.ElementTree as ET
            tree = ET.parse(core_site_local)
            root = tree.getroot()
            for prop in root.findall('property'):
                name = prop.find('name')
                value = prop.find('value')
                if name is not None and name.text == 'fs.defaultFS':
                    hdfs_address = value.text if value is not None else None
                    logger.info(f"Extracted HDFS address from core-site.xml: {hdfs_address}")
                    break
        except Exception as e:
            logger.warning(f"Could not parse core-site.xml to extract HDFS address: {e}")
        
        logger.info(f"✓ Hadoop config files downloaded to {temp_dir}")
        
        return {
            'core_site': core_site_local,
            'hdfs_site': hdfs_site_local,
            'temp_dir': temp_dir,
            'hdfs_address': hdfs_address
        }
        
    except Exception as e:
        logger.error(f"Failed to download Hadoop config from EMR: {e}")
        emr_ssh.close()
        return None


def copy_hadoop_config_to_nodes(ssh, config):
    """
    Download Hadoop config from EMR master to local (bastion)
    
    This function downloads core-site.xml and hdfs-site.xml from EMR master
    to the local machine (bastion), which can then be uploaded to each node.
    
    Args:
        ssh: SSH connection (not used, kept for compatibility)
        config: Configuration dictionary
    
    Returns:
        Dict with hadoop config file paths, or None if failed
    """
    return download_hadoop_config_from_emr(config)


def download_package_to_local(config):
    """
    Download DolphinScheduler package to local machine (bastion/control node)
    
    Priority:
    1. Check if local package already exists
    2. Download from internet (Apache archive)
    
    Args:
        config: Configuration dictionary
    
    Returns:
        Path to local package file if successful, None if failed
    """
    import subprocess
    import shutil
    
    version = config.get('deployment', {}).get('version', '3.2.2')
    local_temp_dir = tempfile.mkdtemp(prefix='dolphinscheduler_deploy_')
    package_filename = f"apache-dolphinscheduler-{version}-bin.tar.gz"
    local_package_path = os.path.join(local_temp_dir, package_filename)
    
    logger.info(f"Preparing DolphinScheduler {version} package on local machine...")
    
    # Check if package exists in current directory or common locations
    search_paths = [
        os.path.join(os.getcwd(), package_filename),
        os.path.join(os.getcwd(), 'dolphinscheduler', package_filename),
        f"/tmp/{package_filename}",
        os.path.expanduser(f"~/Downloads/{package_filename}")
    ]
    
    for path in search_paths:
        if os.path.exists(path):
            file_size = os.path.getsize(path)
            if file_size > 800 * 1024 * 1024:  # Should be around 859MB
                logger.info(f"✓ Found existing package at: {path} ({file_size // 1024 // 1024}MB)")
                # Copy to temp dir
                shutil.copy2(path, local_package_path)
                return {'package_path': local_package_path, 'temp_dir': local_temp_dir}
            else:
                logger.warning(f"Found package at {path} but size is wrong ({file_size} bytes)")
    
    # Download from internet
    download_url = config.get('advanced', {}).get('download_url', 
        f'https://archive.apache.org/dist/dolphinscheduler/{version}/apache-dolphinscheduler-{version}-bin.tar.gz')
    
    logger.info(f"Downloading DolphinScheduler from: {download_url}")
    logger.info("This may take several minutes depending on your network speed...")
    
    try:
        # Try wget first
        wget_cmd = [
            'wget', '--progress=dot:giga', '--timeout=600', '--tries=3',
            download_url, '-O', local_package_path
        ]
        
        result = subprocess.run(wget_cmd, capture_output=True, text=True, timeout=900)
        
        if result.returncode != 0:
            logger.warning(f"wget failed: {result.stderr}, trying curl...")
            # Try curl as fallback
            curl_cmd = [
                'curl', '-L', '--connect-timeout', '30', '--max-time', '900',
                '--retry', '3', download_url, '-o', local_package_path
            ]
            result = subprocess.run(curl_cmd, capture_output=True, text=True, timeout=900)
            
            if result.returncode != 0:
                raise Exception(f"Both wget and curl failed: {result.stderr}")
        
        # Verify download
        if not os.path.exists(local_package_path):
            raise Exception("Download completed but file not found")
        
        file_size = os.path.getsize(local_package_path)
        if file_size < 800 * 1024 * 1024:
            raise Exception(f"Downloaded file too small: {file_size} bytes")
        
        logger.info(f"✓ Package downloaded successfully: {local_package_path} ({file_size // 1024 // 1024}MB)")
        return {'package_path': local_package_path, 'temp_dir': local_temp_dir}
        
    except subprocess.TimeoutExpired:
        logger.error("Download timed out after 15 minutes")
        shutil.rmtree(local_temp_dir, ignore_errors=True)
        return None
    except Exception as e:
        logger.error(f"Failed to download package: {e}")
        shutil.rmtree(local_temp_dir, ignore_errors=True)
        return None


def extract_and_configure_local(config, package_info):
    """
    Extract package and configure on local machine before distribution
    
    Args:
        config: Configuration dictionary
        package_info: Dict with 'package_path' and 'temp_dir'
    
    Returns:
        Path to configured package directory
    """
    import subprocess
    import tarfile
    
    version = config.get('deployment', {}).get('version', '3.2.2')
    temp_dir = package_info['temp_dir']
    package_path = package_info['package_path']
    extract_dir = os.path.join(temp_dir, f"apache-dolphinscheduler-{version}-bin")
    
    logger.info("Extracting package on local machine...")
    
    # Extract package
    try:
        with tarfile.open(package_path, 'r:gz') as tar:
            tar.extractall(path=temp_dir)
        logger.info(f"✓ Package extracted to: {extract_dir}")
    except Exception as e:
        logger.error(f"Failed to extract package: {e}")
        raise
    
    # Generate and write configuration files
    logger.info("Generating configuration files...")
    
    deploy_user = config['deployment']['user']
    components = ['master', 'worker', 'api', 'alert']
    component_dirs = {
        'master': 'master-server',
        'worker': 'worker-server', 
        'api': 'api-server',
        'alert': 'alert-server'
    }
    
    # Generate application.yaml for each component
    for component in components:
        yaml_content = generate_application_yaml_v320(config, component)
        component_dir = component_dirs[component]
        conf_dir = os.path.join(extract_dir, component_dir, 'conf')
        os.makedirs(conf_dir, exist_ok=True)
        
        yaml_path = os.path.join(conf_dir, 'application.yaml')
        with open(yaml_path, 'w') as f:
            f.write(yaml_content)
        logger.info(f"✓ Generated {component} application.yaml")
    
    # Generate tools application.yaml
    tools_conf_dir = os.path.join(extract_dir, 'tools', 'conf')
    os.makedirs(tools_conf_dir, exist_ok=True)
    tools_yaml_content = generate_application_yaml_v320(config, 'master')
    with open(os.path.join(tools_conf_dir, 'application.yaml'), 'w') as f:
        f.write(tools_yaml_content)
    logger.info("✓ Generated tools application.yaml")
    
    # Generate install_env.sh
    bin_env_dir = os.path.join(extract_dir, 'bin', 'env')
    os.makedirs(bin_env_dir, exist_ok=True)
    
    install_env_content = generate_install_env_v320(config)
    install_env_path = os.path.join(bin_env_dir, 'install_env.sh')
    with open(install_env_path, 'w') as f:
        f.write(install_env_content)
    os.chmod(install_env_path, 0o755)
    logger.info("✓ Generated install_env.sh")
    
    # Generate dolphinscheduler_env.sh
    ds_env_content = generate_dolphinscheduler_env_v320(config)
    ds_env_path = os.path.join(bin_env_dir, 'dolphinscheduler_env.sh')
    with open(ds_env_path, 'w') as f:
        f.write(ds_env_content)
    os.chmod(ds_env_path, 0o755)
    logger.info("✓ Generated dolphinscheduler_env.sh")
    
    # Generate common.properties for S3/HDFS storage
    common_props_content = generate_common_properties_v320(config)
    
    # Write to all component conf directories
    for component in components:
        component_dir = component_dirs[component]
        conf_dir = os.path.join(extract_dir, component_dir, 'conf')
        props_path = os.path.join(conf_dir, 'common.properties')
        with open(props_path, 'w') as f:
            f.write(common_props_content)
    
    # Also write to tools/conf
    tools_props_path = os.path.join(tools_conf_dir, 'common.properties')
    with open(tools_props_path, 'w') as f:
        f.write(common_props_content)
    logger.info("✓ Generated common.properties for all components")
    
    # Make all shell scripts executable
    for root, dirs, files in os.walk(os.path.join(extract_dir, 'bin')):
        for file in files:
            if file.endswith('.sh'):
                os.chmod(os.path.join(root, file), 0o755)
    
    logger.info(f"✓ Package configured at: {extract_dir}")
    return extract_dir


def create_distribution_tarball(extract_dir, temp_dir, version):
    """
    Create a tarball of the configured package for distribution
    
    Args:
        extract_dir: Path to configured package directory
        temp_dir: Temporary directory
        version: DolphinScheduler version
    
    Returns:
        Path to distribution tarball
    """
    import tarfile
    
    tarball_path = os.path.join(temp_dir, f"dolphinscheduler-{version}-configured.tar.gz")
    
    logger.info("Creating distribution tarball...")
    
    with tarfile.open(tarball_path, 'w:gz') as tar:
        tar.add(extract_dir, arcname=os.path.basename(extract_dir))
    
    file_size = os.path.getsize(tarball_path)
    logger.info(f"✓ Distribution tarball created: {tarball_path} ({file_size // 1024 // 1024}MB)")
    
    return tarball_path


def distribute_to_node(node_ssh, host, tarball_path, config, deploy_user, version):
    """
    Distribute configured package to a single node via SCP/SFTP
    
    Args:
        node_ssh: SSH connection to the node
        host: Node hostname/IP
        tarball_path: Path to local tarball
        config: Configuration dictionary
        deploy_user: Deployment user
        version: DolphinScheduler version
    
    Returns:
        True if successful
    """
    install_path = config['deployment']['install_path']
    remote_tarball = f"/tmp/dolphinscheduler-{version}-configured.tar.gz"
    
    logger.info(f"[{host}] Uploading configured package...")
    
    # Upload tarball via SFTP
    upload_file(node_ssh, tarball_path, remote_tarball)
    logger.info(f"[{host}] ✓ Package uploaded")
    
    # Extract and install
    logger.info(f"[{host}] Installing package...")
    install_cmd = f"""
    set -e
    
    # Create install directory
    sudo mkdir -p {install_path}
    
    # Extract to temp location
    cd /tmp
    rm -rf apache-dolphinscheduler-{version}-bin
    tar -xzf {remote_tarball}
    
    # Copy to install path
    sudo cp -r apache-dolphinscheduler-{version}-bin/* {install_path}/
    
    # Set ownership
    sudo chown -R {deploy_user}:{deploy_user} {install_path}
    
    # Make scripts executable
    sudo chmod +x {install_path}/bin/*.sh
    sudo find {install_path}/bin -name "*.sh" -exec chmod +x {{}} \\;
    
    # Clean up
    rm -rf /tmp/apache-dolphinscheduler-{version}-bin
    rm -f {remote_tarball}
    
    echo "✓ Installation completed"
    """
    
    output = execute_remote_command(node_ssh, install_cmd, timeout=180)
    logger.info(f"[{host}] ✓ Package installed")
    
    return True


def deploy_dolphinscheduler_v320(config, package_file=None, username='ec2-user', key_file=None):
    """
    Deploy DolphinScheduler 3.2.x to all nodes
    
    New deployment flow:
    1. Download package to local machine (bastion/control node)
    2. Extract and configure on local machine
    3. Create distribution tarball
    4. Distribute to all nodes via SSH/SCP
    5. Initialize database on first node
    6. Install MySQL JDBC driver on all nodes
    
    Args:
        config: Configuration dictionary
        package_file: Path to DolphinScheduler package (optional, will download if not provided)
        username: SSH username
        key_file: SSH key file path
    
    Returns:
        True if successful
    """
    import shutil
    
    version = config.get('deployment', {}).get('version', '3.2.2')
    deploy_user = config['deployment']['user']
    install_path = config['deployment']['install_path']
    
    logger.info(f"=" * 60)
    logger.info(f"Deploying DolphinScheduler {version}")
    logger.info(f"=" * 60)
    
    # Collect all unique nodes (using selector to deduplicate)
    all_nodes = []
    seen_hosts = set()
    
    for component in ['master', 'worker', 'api', 'alert']:
        for node in config['cluster'][component]['nodes']:
            host = node['host']
            if host not in seen_hosts:
                seen_hosts.add(host)
                all_nodes.append({
                    'host': host,
                    'components': [component]
                })
            else:
                # Add component to existing node
                for n in all_nodes:
                    if n['host'] == host:
                        n['components'].append(component)
                        break
    
    logger.info(f"Target nodes: {len(all_nodes)}")
    for node in all_nodes:
        logger.info(f"  - {node['host']}: {', '.join(node['components'])}")
    
    first_node = all_nodes[0]['host']
    temp_dir = None
    
    try:
        # ============================================================
        # Step 1: Download package to local machine
        # ============================================================
        logger.info("")
        logger.info("Step 1: Downloading package to local machine...")
        
        if package_file and os.path.exists(package_file):
            logger.info(f"Using provided package: {package_file}")
            temp_dir = tempfile.mkdtemp(prefix='dolphinscheduler_deploy_')
            package_info = {'package_path': package_file, 'temp_dir': temp_dir}
        else:
            package_info = download_package_to_local(config)
            if not package_info:
                raise Exception("Failed to download DolphinScheduler package")
            temp_dir = package_info['temp_dir']
        
        # ============================================================
        # Step 2: Extract and configure on local machine
        # ============================================================
        logger.info("")
        logger.info("Step 2: Extracting and configuring package locally...")
        
        extract_dir = extract_and_configure_local(config, package_info)
        
        # ============================================================
        # Step 3: Create distribution tarball
        # ============================================================
        logger.info("")
        logger.info("Step 3: Creating distribution tarball...")
        
        tarball_path = create_distribution_tarball(extract_dir, temp_dir, version)
        
        # ============================================================
        # Step 4: Distribute to all nodes
        # ============================================================
        logger.info("")
        logger.info(f"Step 4: Distributing to {len(all_nodes)} nodes...")
        
        for i, node in enumerate(all_nodes):
            host = node['host']
            logger.info(f"")
            logger.info(f"[{i+1}/{len(all_nodes)}] Deploying to {host}...")
            
            # Connect to node
            max_retries = 3
            node_ssh = None
            for attempt in range(max_retries):
                try:
                    node_ssh = connect_ssh(host, username, key_file, config=config)
                    node_ssh.exec_command('echo "connection test"', timeout=10)
                    break
                except Exception as e:
                    logger.warning(f"SSH connection attempt {attempt + 1}/{max_retries} to {host} failed: {e}")
                    if attempt < max_retries - 1:
                        time.sleep(5)
                    else:
                        raise Exception(f"Failed to connect to {host} after {max_retries} attempts")
            
            try:
                # Distribute configured package
                distribute_to_node(node_ssh, host, tarball_path, config, deploy_user, version)
                
                # Create resource directories
                logger.info(f"[{host}] Creating resource directories...")
                create_resource_directories(node_ssh, config)
                
                # Install MySQL JDBC driver
                logger.info(f"[{host}] Installing MySQL JDBC driver...")
                install_mysql_jdbc_driver(node_ssh, install_path, deploy_user)
                
                logger.info(f"[{host}] ✓ Deployment completed")
                
            finally:
                if node_ssh:
                    node_ssh.close()
        
        # ============================================================
        # Step 5: Initialize database (only on first node)
        # ============================================================
        logger.info("")
        logger.info("Step 5: Initializing database schema...")
        
        first_ssh = connect_ssh(first_node, username, key_file, config=config)
        try:
            initialize_database(first_ssh, config, install_path)
        finally:
            first_ssh.close()
        
        # ============================================================
        # Step 6: Handle storage-specific configuration
        # ============================================================
        storage_type = config.get('storage', {}).get('type', 'LOCAL').upper()
        
        if storage_type == 'HDFS':
            logger.info("")
            logger.info("Step 6: Configuring HDFS storage...")
            
            # Download Hadoop config from EMR
            hadoop_config_files = download_hadoop_config_from_emr(config)
            
            if hadoop_config_files:
                # Create HDFS directories
                logger.info("Creating HDFS directories...")
                first_ssh = connect_ssh(first_node, username, key_file, config=config)
                try:
                    create_hdfs_directories(first_ssh, config)
                finally:
                    first_ssh.close()
                
                # Setup Hadoop config on all nodes
                for node in all_nodes:
                    host = node['host']
                    logger.info(f"[{host}] Setting up Hadoop configuration...")
                    node_ssh = connect_ssh(host, username, key_file, config=config)
                    try:
                        setup_hadoop_config_on_node(node_ssh, config, host, hadoop_config_files)
                    finally:
                        node_ssh.close()
                
                # Clean up temp files
                if 'temp_dir' in hadoop_config_files:
                    shutil.rmtree(hadoop_config_files['temp_dir'], ignore_errors=True)
            else:
                logger.warning("⚠ Failed to download Hadoop config, HDFS may not work correctly")
        
        elif storage_type == 'S3':
            logger.info("")
            logger.info("Step 6: S3 storage configured (no additional setup needed)")
        
        else:
            logger.info("")
            logger.info("Step 6: LOCAL storage configured (default)")
        
        # ============================================================
        # Complete
        # ============================================================
        logger.info("")
        logger.info("=" * 60)
        logger.info(f"✓ DolphinScheduler {version} deployed successfully!")
        logger.info("=" * 60)
        logger.info("")
        logger.info("Next steps:")
        logger.info("  1. Start services: python cli.py start")
        logger.info("  2. Check status: python cli.py status")
        logger.info(f"  3. Access UI: http://<api-server-ip>:12345/dolphinscheduler")
        logger.info("")
        
        return True
        
    except Exception as e:
        logger.error(f"Deployment failed: {e}")
        raise
        
    finally:
        # Clean up temp directory
        if temp_dir and os.path.exists(temp_dir):
            logger.info("Cleaning up temporary files...")
            shutil.rmtree(temp_dir, ignore_errors=True)


# Re-export functions from other modules for backward compatibility
from src.deploy.package_manager import download_dolphinscheduler
from src.deploy.node_initializer import (
    initialize_node, create_deployment_user, setup_ssh_keys, configure_hosts_file
)
from src.deploy.service_manager import start_services, stop_services, check_service_status