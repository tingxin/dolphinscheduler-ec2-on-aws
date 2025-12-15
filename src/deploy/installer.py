"""
DolphinScheduler 3.2.0 installation and deployment (Simplified)
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


def upload_common_properties(ssh, config, extract_dir):
    """
    Generate and upload common.properties configuration file
    
    This file is critical for resource center functionality.
    
    Args:
        ssh: SSH connection
        config: Configuration dictionary
        extract_dir: DolphinScheduler extracted directory
    
    Returns:
        True if successful
    """
    logger.info("Generating common.properties configuration...")
    deploy_user = config['deployment']['user']
    
    properties_content = generate_common_properties_v320(config)
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.properties', delete=False) as f:
        f.write(properties_content)
        temp_properties = f.name
    
    try:
        temp_remote_properties = "/tmp/common.properties"
        upload_file(ssh, temp_properties, temp_remote_properties)
        
        # Ensure conf directory exists
        execute_remote_command(ssh, f"sudo mkdir -p {extract_dir}/conf")
        
        # Move to correct location
        properties_path = f"{extract_dir}/conf/common.properties"
        execute_remote_command(ssh, f"sudo mv {temp_remote_properties} {properties_path}")
        execute_remote_command(ssh, f"sudo chown {deploy_user}:{deploy_user} {properties_path}")
        execute_remote_command(ssh, f"sudo chmod 644 {properties_path}")
        
        os.remove(temp_properties)
        logger.info("✓ common.properties configured")
    except Exception as e:
        logger.error(f"Failed to upload common.properties: {e}")
        raise
    
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


def prepare_package_on_bastion(ssh, config):
    """
    Download DolphinScheduler package on bastion host for distribution to nodes
    
    Args:
        ssh: SSH connection to first master (bastion)
        config: Configuration dictionary
    
    Returns:
        Path to package on bastion if successful, None if failed
    """
    try:
        package_path = "/tmp/apache-dolphinscheduler-3.2.0-bin.tar.gz"
        
        # Check if package already exists
        check_cmd = f"[ -f {package_path} ] && echo 'EXISTS' || echo 'NOT_EXISTS'"
        result = execute_remote_command(ssh, check_cmd, timeout=10)
        
        if "EXISTS" in result:
            # Verify file size
            size_cmd = f"stat -c%s {package_path} 2>/dev/null || echo '0'"
            size_result = execute_remote_command(ssh, size_cmd, timeout=10)
            file_size = int(size_result.strip())
            
            if file_size > 800 * 1024 * 1024:  # Should be around 859MB
                logger.info(f"✓ DolphinScheduler package already exists on bastion: {package_path}")
                return package_path
            else:
                logger.info(f"Package exists but size is wrong ({file_size} bytes), re-downloading...")
        
        # Priority 1: Try S3 download first (fastest) - from new package_distribution config
        pkg_dist_config = config.get('package_distribution', {})
        if pkg_dist_config.get('enabled', False):
            logger.info("Downloading DolphinScheduler package from S3 on bastion host...")
            s3_config = pkg_dist_config.get('s3', {})
            s3_bucket = s3_config.get('bucket')
            s3_key = s3_config.get('key')
            s3_region = s3_config.get('region', config.get('aws', {}).get('region', 'us-east-2'))
            
            if not s3_bucket or not s3_key:
                logger.warning("S3 package distribution enabled but bucket or key not configured")
            else:
                s3_download_cmd = f"""
                set -e
                echo "Starting S3 download from s3://{s3_bucket}/{s3_key}..."
                
                # Remove any partial downloads
                rm -f {package_path}*
                
                # Optimize S3 download settings
                aws configure set default.s3.max_concurrent_requests 20
                aws configure set default.s3.max_bandwidth 100MB/s
                aws configure set default.s3.multipart_threshold 64MB
                aws configure set default.s3.multipart_chunksize 16MB
                
                # Download from S3 with optimizations
                if aws s3 cp s3://{s3_bucket}/{s3_key} {package_path} --region {s3_region} --no-progress; then
                    echo "✓ S3 download completed successfully"
                else
                    echo "✗ S3 download failed"
                    exit 1
                fi
                
                # Verify download
                if [ ! -f {package_path} ] || [ ! -s {package_path} ]; then
                    echo "✗ S3 download verification failed"
                    exit 1
                fi
                
                file_size=$(stat -c%s {package_path})
                echo "✓ S3 download completed, size: ${{file_size}} bytes ($((${{file_size}} / 1024 / 1024))MB)"
                """
                
                try:
                    execute_remote_command(ssh, s3_download_cmd, timeout=300)
                    logger.info(f"✓ Package downloaded from S3 successfully on bastion: {package_path}")
                    return package_path
                except Exception as e:
                    logger.warning(f"S3 download failed: {e}, falling back to internet download")
        
        # Priority 2: Download from internet (fallback)
        download_url = config.get('advanced', {}).get('download_url', 
            'https://archive.apache.org/dist/dolphinscheduler/3.2.0/apache-dolphinscheduler-3.2.0-bin.tar.gz')
        
        logger.info("Downloading DolphinScheduler package from internet on bastion host...")
        download_cmd = f"""
        set -e
        echo "Starting download from {download_url}..."
        
        # Remove any partial downloads
        rm -f {package_path}*
        
        # Download with wget (faster and more reliable)
        wget --progress=dot:giga --timeout=600 --tries=3 --retry-connrefused \\
             "{download_url}" -O {package_path}
        
        # Verify download
        if [ ! -f {package_path} ] || [ ! -s {package_path} ]; then
            echo "✗ Download verification failed"
            exit 1
        fi
        
        file_size=$(stat -c%s {package_path})
        echo "✓ Download completed, size: ${{file_size}} bytes ($((${{file_size}} / 1024 / 1024))MB)"
        """
        
        execute_remote_command(ssh, download_cmd, timeout=800)
        logger.info(f"✓ Package downloaded successfully on bastion: {package_path}")
        return package_path
        
    except Exception as e:
        logger.warning(f"Failed to prepare package on bastion: {e}")
        logger.info("Will fall back to direct download on each node")
        return None


def deploy_dolphinscheduler_v320(config, package_file=None, username='ec2-user', key_file=None):
    """
    Deploy DolphinScheduler 3.2.0 to all nodes using the official install.sh script
    
    Args:
        config: Configuration dictionary
        package_file: Path to DolphinScheduler package (optional, will download on remote if not provided)
        username: SSH username
        key_file: SSH key file path
    
    Returns:
        True if successful
    """
    logger.info("Deploying DolphinScheduler 3.2.0...")
    
    # Get first master node for installation
    first_master = config['cluster']['master']['nodes'][0]['host']
    version = config['deployment']['version']
    deploy_user = config['deployment']['user']
    
    def get_ssh_connection():
        """Get SSH connection with retry mechanism"""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                ssh = connect_ssh(first_master, username, key_file, config=config)
                # Test connection
                ssh.exec_command('echo "connection test"')
                return ssh
            except Exception as e:
                logger.warning(f"SSH connection attempt {attempt + 1}/{max_retries} failed: {e}")
                if attempt < max_retries - 1:
                    time.sleep(5)
                else:
                    raise Exception(f"Failed to establish SSH connection after {max_retries} attempts")
    
    ssh = get_ssh_connection()
    
    try:
        # Step 0: Prepare package on bastion for faster distribution
        logger.info("Preparing package distribution...")
        bastion_package_path = prepare_package_on_bastion(ssh, config)
        
        # Step 1: Download and extract package on first master node
        if bastion_package_path:
            logger.info("Using package from bastion host for first master node...")
            extract_dir = f"/tmp/apache-dolphinscheduler-{version}-bin"
            
            # Extract package on first master
            extract_cmd = f"""
            cd /tmp
            rm -rf apache-dolphinscheduler-{version}-bin
            tar -xzf {bastion_package_path}
            echo "✓ Package extracted to {extract_dir}"
            """
            execute_remote_command(ssh, extract_cmd, timeout=120)
        elif package_file is None or config.get('deployment', {}).get('download_on_remote', True):
            logger.info("Downloading DolphinScheduler 3.2.0 directly on target node...")
            extract_dir = download_and_extract_remote(ssh, config)
        else:
            logger.info("Uploading DolphinScheduler package from local...")
            extract_dir = upload_and_extract_package(ssh, package_file, version)
        
        # Step 2: Set ownership and permissions
        setup_package_permissions(ssh, extract_dir, deploy_user)
        
        # Step 3: Create resource directories
        create_resource_directories(ssh, config)
        
        # Step 4: Upload configuration files
        upload_configuration_files(ssh, config, extract_dir)
        
        # Step 5: Upload common.properties (critical for resource center)
        upload_common_properties(ssh, config, extract_dir)
        
        # Step 5.5: Check and configure storage based on type
        storage_type = config.get('storage', {}).get('type', 'LOCAL').upper()
        if storage_type == 'S3':
            logger.info("S3 storage is configured, checking and installing S3 plugin...")
            if not check_s3_plugin_installed(ssh, extract_dir):
                logger.info("S3 plugin not found, installing...")
                install_s3_plugin(ssh, extract_dir, deploy_user, config)
                configure_s3_storage(ssh, extract_dir, deploy_user, config)
            else:
                logger.info("S3 plugin already installed")
        elif storage_type == 'HDFS':
            logger.info("HDFS storage is configured, checking HDFS connectivity...")
            if check_hdfs_connectivity(ssh, config):
                logger.info("HDFS is accessible, configuring HDFS storage...")
                configure_hdfs_storage(ssh, extract_dir, deploy_user, config)
            else:
                logger.error("HDFS is not accessible from the node")
                raise Exception("HDFS NameNode is not reachable. Please verify EMR cluster is running and network connectivity is correct.")
        else:
            logger.info("LOCAL storage is configured (default)")
        
        # Step 6: Install MySQL JDBC driver
        install_mysql_jdbc_driver(ssh, extract_dir, deploy_user)
        
        # Step 7: Initialize database schema
        initialize_database(ssh, config, extract_dir)
        
        # Step 8: Generate application.yaml files for all components
        configure_components(ssh, config, extract_dir)
        
        # Step 7: Deploy to all nodes manually
        logger.info("Deploying DolphinScheduler to all cluster nodes...")
        
        # Deploy to each node sequentially to avoid SSH key issues
        all_nodes = []
        for component in ['master', 'worker', 'api', 'alert']:
            for node in config['cluster'][component]['nodes']:
                all_nodes.append({
                    'host': node['host'],
                    'component': component
                })
        
        def deploy_to_node(node_info):
            host = node_info['host']
            component = node_info['component']
            
            logger.info(f"Deploying {component} to {host}...")
            
            # Connect with retry mechanism for better reliability
            max_retries = 3
            node_ssh = None
            for attempt in range(max_retries):
                try:
                    node_ssh = connect_ssh(host, username, key_file, config=config)
                    # Test connection
                    node_ssh.exec_command('echo "connection test"', timeout=10)
                    break
                except Exception as e:
                    logger.warning(f"SSH connection attempt {attempt + 1}/{max_retries} to {host} failed: {e}")
                    if attempt < max_retries - 1:
                        time.sleep(5)
                    else:
                        raise Exception(f"Failed to establish SSH connection to {host} after {max_retries} attempts")
            
            try:
                # Step 1: Create install directory
                logger.info(f"[{host}] Creating install directory...")
                execute_remote_command(node_ssh, f"sudo mkdir -p {config['deployment']['install_path']}")
                execute_remote_command(node_ssh, f"sudo chown {deploy_user}:{deploy_user} {config['deployment']['install_path']}")
                
                # Step 2: Deploy files to install directory
                if host == first_master:
                    logger.info(f"[{host}] Copying files from temp directory...")
                    copy_cmd = f"sudo -u {deploy_user} cp -r {extract_dir}/* {config['deployment']['install_path']}/"
                    execute_remote_command(node_ssh, copy_cmd, timeout=120)
                else:
                    # Priority 1: Download from S3 (most reliable) - from new package_distribution config
                    pkg_dist_config = config.get('package_distribution', {})
                    if pkg_dist_config.get('enabled', False):
                        logger.info(f"[{host}] Downloading DolphinScheduler from S3...")
                        
                        # Step 2a: Create temp directory (use home dir to avoid tmpfs limits)
                        execute_remote_command(node_ssh, """
                        TEMP_DIR="/home/ec2-user/ds_download_$(date +%s)"
                        mkdir -p $TEMP_DIR
                        cd $TEMP_DIR
                        echo "Created temp directory: $TEMP_DIR"
                        """, timeout=30)
                        
                        # Step 2b: Download from S3
                        s3_config = pkg_dist_config.get('s3', {})
                        s3_bucket = s3_config.get('bucket')
                        s3_key = s3_config.get('key')
                        s3_region = s3_config.get('region', config.get('aws', {}).get('region', 'us-east-2'))
                        
                        download_cmd = f"""
                        cd /home/ec2-user/ds_download_*
                        echo "Downloading from S3: s3://{s3_bucket}/{s3_key}..."
                        
                        # Optimize S3 download with multipart and higher concurrency
                        aws configure set default.s3.max_concurrent_requests 20
                        aws configure set default.s3.max_bandwidth 100MB/s
                        aws configure set default.s3.multipart_threshold 64MB
                        aws configure set default.s3.multipart_chunksize 16MB
                        
                        # Download from S3 using AWS CLI with optimizations
                        if aws s3 cp s3://{s3_bucket}/{s3_key} apache-dolphinscheduler-3.2.0-bin.tar.gz --region {s3_region} --no-progress; then
                            echo "✓ S3 download completed successfully"
                        else
                            echo "✗ S3 download failed"
                            exit 1
                        fi
                        
                        # Verify download
                        if [ ! -f apache-dolphinscheduler-3.2.0-bin.tar.gz ] || [ ! -s apache-dolphinscheduler-3.2.0-bin.tar.gz ]; then
                            echo "✗ S3 download verification failed"
                            exit 1
                        fi
                        
                        echo "✓ Package downloaded from S3, size: $(du -h apache-dolphinscheduler-3.2.0-bin.tar.gz | cut -f1)"
                        """
                        execute_remote_command(node_ssh, download_cmd, timeout=300)
                        
                    # Priority 2: Copy from bastion host (if S3 fails)
                    elif bastion_package_path:
                        logger.info(f"[{host}] Trying to copy DolphinScheduler from bastion host...")
                        
                        # Step 2a: Create temp directory (use home dir to avoid tmpfs limits)
                        execute_remote_command(node_ssh, """
                        TEMP_DIR="/home/ec2-user/ds_download_$(date +%s)"
                        mkdir -p $TEMP_DIR
                        cd $TEMP_DIR
                        echo "Created temp directory: $TEMP_DIR"
                        """, timeout=30)
                        
                        # Step 2b: Try to copy from bastion using scp (may fail due to SSH keys)
                        try:
                            copy_cmd = f"""
                            cd /home/ec2-user/ds_download_*
                            echo "Copying package from bastion host {first_master}..."
                            
                            # Copy from bastion using ec2-user (has SSH keys)
                            scp -o StrictHostKeyChecking=no -o ConnectTimeout=30 \\
                                ec2-user@{first_master}:{bastion_package_path} apache-dolphinscheduler-3.2.0-bin.tar.gz
                            
                            # Verify copy
                            if [ ! -f apache-dolphinscheduler-3.2.0-bin.tar.gz ] || [ ! -s apache-dolphinscheduler-3.2.0-bin.tar.gz ]; then
                                echo "✗ Copy verification failed"
                                exit 1
                            fi
                            
                            echo "✓ Package copied from bastion, size: $(du -h apache-dolphinscheduler-3.2.0-bin.tar.gz | cut -f1)"
                            """
                            execute_remote_command(node_ssh, copy_cmd, timeout=300)
                        except Exception as e:
                            logger.warning(f"[{host}] Failed to copy from bastion: {e}, falling back to internet download")
                            # Fall through to internet download
                            
                    # Priority 3: Download from internet (fallback)
                    else:

                            # Option 2: Download from internet (fallback)
                            logger.info(f"[{host}] Downloading DolphinScheduler from internet...")
                            download_url = config.get('advanced', {}).get('download_url', 
                                'https://archive.apache.org/dist/dolphinscheduler/3.2.0/apache-dolphinscheduler-3.2.0-bin.tar.gz')
                            
                            # Step 2a: Create temp directory (use home dir to avoid tmpfs limits)
                            execute_remote_command(node_ssh, """
                            TEMP_DIR="/home/ec2-user/ds_download_$(date +%s)"
                            mkdir -p $TEMP_DIR
                            cd $TEMP_DIR
                            echo "Created temp directory: $TEMP_DIR"
                            """, timeout=30)
                            
                            # Step 2b: Download package
                            logger.info(f"[{host}] Downloading package (this may take a few minutes)...")
                            download_cmd = f"""
                            cd /home/ec2-user/ds_download_*
                            echo "Starting download from {download_url}..."
                            
                            # Try wget first
                            if wget --progress=dot:giga --timeout=600 --tries=2 "{download_url}" -O apache-dolphinscheduler-3.2.0-bin.tar.gz 2>&1; then
                                echo "✓ Download completed with wget"
                            else
                                echo "wget failed, trying curl..."
                                if curl -L --connect-timeout 30 --max-time 600 --retry 2 "{download_url}" -o apache-dolphinscheduler-3.2.0-bin.tar.gz; then
                                    echo "✓ Download completed with curl"
                                else
                                    echo "✗ Download failed"
                                    exit 1
                                fi
                            fi
                            
                            # Verify download
                            if [ ! -f apache-dolphinscheduler-3.2.0-bin.tar.gz ] || [ ! -s apache-dolphinscheduler-3.2.0-bin.tar.gz ]; then
                                echo "✗ Download verification failed"
                                exit 1
                            fi
                            
                            echo "✓ Download verified, size: $(du -h apache-dolphinscheduler-3.2.0-bin.tar.gz | cut -f1)"
                            """
                            execute_remote_command(node_ssh, download_cmd, timeout=700)
                    
                    # Step 2c: Extract package
                    logger.info(f"[{host}] Extracting package...")
                    extract_cmd = """
                    cd /home/ec2-user/ds_download_*
                    echo "Extracting package..."
                    if tar -xzf apache-dolphinscheduler-3.2.0-bin.tar.gz; then
                        echo "✓ Package extracted successfully"
                    else
                        echo "✗ Extraction failed"
                        exit 1
                    fi
                    """
                    execute_remote_command(node_ssh, extract_cmd, timeout=120)
                    
                    # Step 2d: Install to final location
                    logger.info(f"[{host}] Installing to final location...")
                    install_cmd = f"""
                    cd /home/ec2-user/ds_download_*
                    echo "Installing to {config['deployment']['install_path']}..."
                    sudo mkdir -p {config['deployment']['install_path']}
                    sudo cp -r apache-dolphinscheduler-3.2.0-bin/* {config['deployment']['install_path']}/
                    sudo chown -R {deploy_user}:{deploy_user} {config['deployment']['install_path']}
                    
                    # Clean up
                    cd /
                    rm -rf /home/ec2-user/ds_download_*
                    echo "✓ Installation completed and temp files cleaned"
                    """
                    execute_remote_command(node_ssh, install_cmd, timeout=180)
                
                # Step 3: Set permissions
                logger.info(f"[{host}] Setting permissions...")
                execute_remote_command(node_ssh, f"sudo chown -R {deploy_user}:{deploy_user} {config['deployment']['install_path']}")
                execute_remote_command(node_ssh, f"sudo chmod +x {config['deployment']['install_path']}/bin/*.sh")
                
                # Step 4: Create resource directories
                logger.info(f"[{host}] Creating resource directories...")
                create_resource_directories(node_ssh, config)
                
                # Step 5: Upload configuration files
                logger.info(f"[{host}] Uploading configuration files...")
                upload_configuration_files(node_ssh, config, config['deployment']['install_path'])
                
                # Step 6: Upload common.properties (critical for resource center)
                logger.info(f"[{host}] Uploading common.properties...")
                upload_common_properties(node_ssh, config, config['deployment']['install_path'])
                
                # Step 6.5: Check and configure storage based on type
                storage_type = config.get('storage', {}).get('type', 'LOCAL').upper()
                if storage_type == 'S3':
                    logger.info(f"[{host}] S3 storage is configured, checking and installing S3 plugin...")
                    if not check_s3_plugin_installed(node_ssh, config['deployment']['install_path']):
                        logger.info(f"[{host}] S3 plugin not found, installing...")
                        install_s3_plugin(node_ssh, config['deployment']['install_path'], deploy_user, config)
                        configure_s3_storage(node_ssh, config['deployment']['install_path'], deploy_user, config)
                    else:
                        logger.info(f"[{host}] S3 plugin already installed")
                elif storage_type == 'HDFS':
                    logger.info(f"[{host}] HDFS storage is configured, checking HDFS connectivity...")
                    if check_hdfs_connectivity(node_ssh, config):
                        logger.info(f"[{host}] HDFS is accessible, configuring HDFS storage...")
                        configure_hdfs_storage(node_ssh, config['deployment']['install_path'], deploy_user, config)
                    else:
                        logger.warning(f"[{host}] HDFS is not accessible, but continuing deployment")
                else:
                    logger.info(f"[{host}] LOCAL storage is configured (default)")
                
                # Step 7: Install MySQL JDBC driver
                logger.info(f"[{host}] Installing MySQL JDBC driver...")
                install_mysql_jdbc_driver(node_ssh, config['deployment']['install_path'], deploy_user)
                
                # Step 8: Configure components
                logger.info(f"[{host}] Configuring components...")
                configure_components(node_ssh, config, config['deployment']['install_path'])
                
                logger.info(f"[{host}] ✓ Deployment completed successfully")
                return f"✓ Deployed {component} to {host}"
                
            except Exception as e:
                logger.error(f"[{host}] Deployment failed: {e}")
                raise Exception(f"Failed to deploy {component} to {host}: {e}")
            finally:
                if node_ssh:
                    node_ssh.close()
        
        # Deploy to all nodes sequentially (more reliable)
        logger.info(f"Deploying to {len(all_nodes)} nodes sequentially...")
        
        for i, node in enumerate(all_nodes):
            try:
                result = deploy_to_node(node)
                logger.info(f"[{i+1}/{len(all_nodes)}] {result}")
            except Exception as e:
                logger.error(f"Deployment failed: {e}")
                raise
        
        logger.info("✓ DolphinScheduler 3.2.0 installation completed")
        
        logger.info("✓ DolphinScheduler 3.2.0 deployed successfully")
        return True
        
    finally:
        ssh.close()


# Re-export functions from other modules for backward compatibility
from src.deploy.package_manager import download_dolphinscheduler
from src.deploy.node_initializer import (
    initialize_node, create_deployment_user, setup_ssh_keys, configure_hosts_file
)
from src.deploy.service_manager import start_services, stop_services, check_service_status