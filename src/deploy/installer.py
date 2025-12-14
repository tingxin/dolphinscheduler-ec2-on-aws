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
    generate_dolphinscheduler_env_v320
)
from src.deploy.package_manager import (
    download_and_extract_remote,
    upload_and_extract_package,
    install_mysql_jdbc_driver,
    setup_package_permissions
)
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


def initialize_database(ssh, config, extract_dir):
    """
    Initialize DolphinScheduler database schema
    
    Args:
        ssh: SSH connection
        config: Configuration dictionary
        extract_dir: DolphinScheduler extracted directory
    
    Returns:
        True if successful
    """
    logger.info("Initializing database schema...")
    db_config = config['database']
    deploy_user = config['deployment']['user']
    
    # Test database connectivity first
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
        cursor.execute("SELECT 1")
        conn.close()
        logger.info("✓ Database connectivity verified")
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        raise Exception(f"Cannot connect to database: {e}")
    
    # Run database initialization
    init_db_cmd = f"cd {extract_dir} && sudo -u {deploy_user} bash tools/bin/upgrade-schema.sh"
    
    try:
        db_output = execute_remote_command(ssh, init_db_cmd, timeout=300)
        logger.info(f"Database initialization output: {db_output}")
        
        if "successfully" in db_output.lower() or "completed" in db_output.lower():
            logger.info("✓ Database schema initialized successfully")
        else:
            logger.warning("Database initialization completed but check output for any issues")
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
        # Step 1: Download and extract package
        if package_file is None or config.get('deployment', {}).get('download_on_remote', True):
            logger.info("Downloading DolphinScheduler 3.2.0 directly on target node...")
            extract_dir = download_and_extract_remote(ssh, config)
        else:
            logger.info("Uploading DolphinScheduler package from local...")
            extract_dir = upload_and_extract_package(ssh, package_file, version)
        
        # Step 2: Set ownership and permissions
        setup_package_permissions(ssh, extract_dir, deploy_user)
        
        # Step 3: Upload configuration files
        upload_configuration_files(ssh, config, extract_dir)
        
        # Step 4: Install MySQL JDBC driver
        install_mysql_jdbc_driver(ssh, extract_dir, deploy_user)
        
        # Step 5: Initialize database schema
        initialize_database(ssh, config, extract_dir)
        
        # Step 6: Generate application.yaml files for all components
        configure_components(ssh, config, extract_dir)
        
        # Step 7: Run the install.sh script to deploy to all nodes
        logger.info("Running install.sh to deploy to all cluster nodes...")
        
        install_cmd = f"cd {extract_dir} && sudo -u {deploy_user} bash bin/install.sh"
        
        try:
            install_output = execute_remote_command(ssh, install_cmd, timeout=600)
            logger.info(f"Install script output: {install_output}")
            logger.info("✓ DolphinScheduler 3.2.0 installation completed")
        except Exception as e:
            logger.error(f"Installation script failed: {e}")
            raise Exception(f"DolphinScheduler installation failed: {e}")
        
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