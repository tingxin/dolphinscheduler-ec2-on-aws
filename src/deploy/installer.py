"""
DolphinScheduler installation and deployment
"""
import os
import time
import tempfile
import urllib.request
from pathlib import Path
from src.deploy.ssh import connect_ssh, execute_remote_command, upload_file, execute_script
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


def initialize_node(host, username='ec2-user', key_file=None):
    """
    Initialize EC2 node with required dependencies
    
    Args:
        host: Host address
        username: SSH username
        key_file: SSH key file path
    
    Returns:
        True if successful
    """
    logger.debug(f"Initializing node: {host}")
    
    ssh = connect_ssh(host, username, key_file)
    
    try:
        # Detect OS
        os_info = execute_remote_command(ssh, "cat /etc/os-release")
        is_amazon_linux = 'Amazon Linux' in os_info
        is_ubuntu = 'Ubuntu' in os_info
        
        # Install dependencies
        logger.debug(f"Installing system dependencies on {host}...")
        
        if is_amazon_linux:
            install_script = """
            # Skip system update for faster deployment
            # sudo dnf update -y
            
            # Install all packages in one command (faster)
            sudo dnf install -y --skip-broken \
                java-1.8.0-amazon-corretto \
                java-1.8.0-amazon-corretto-devel \
                mariadb105 \
                psmisc tar gzip wget curl nc \
                python3 python3-pip
            
            # Verify installations
            java -version 2>&1 | head -1
            mysql --version 2>&1 | head -1
            """
        elif is_ubuntu:
            install_script = """
            # Update package list (required for Ubuntu)
            sudo apt-get update -qq
            
            # Install all packages in one command (faster)
            sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
                openjdk-8-jdk \
                mysql-client \
                psmisc tar gzip wget curl netcat \
                python3 python3-pip
            
            # Verify installations
            java -version 2>&1 | head -1
            mysql --version 2>&1 | head -1
            """
        else:
            raise Exception(f"Unsupported OS: {os_info}")
        
        execute_script(ssh, install_script, sudo=False)
        
        logger.debug(f"✓ System dependencies installed on {host}")
        return True
        
    finally:
        ssh.close()


def create_deployment_user(host, username='ec2-user', deploy_user='dolphinscheduler', key_file=None):
    """
    Create deployment user on node
    
    Args:
        host: Host address
        username: SSH username
        deploy_user: Deployment user to create
        key_file: SSH key file path
    
    Returns:
        True if successful
    """
    logger.info(f"Creating deployment user: {deploy_user}")
    
    ssh = connect_ssh(host, username, key_file)
    
    try:
        # Check if user exists (suppress error output)
        stdin, stdout, stderr = ssh.exec_command(f"id {deploy_user}")
        exit_code = stdout.channel.recv_exit_status()
        
        if exit_code == 0:
            logger.info(f"User {deploy_user} already exists on {host}")
            return True
        
        # User doesn't exist, create it
        logger.info(f"User {deploy_user} not found, creating...")
        
        # Create user
        create_user_script = f"""
        # Create user
        sudo useradd -m -s /bin/bash {deploy_user}
        
        # Add to sudoers
        echo "{deploy_user} ALL=(ALL) NOPASSWD: ALL" | sudo tee /etc/sudoers.d/{deploy_user}
        sudo chmod 0440 /etc/sudoers.d/{deploy_user}
        
        # Verify
        id {deploy_user}
        """
        
        execute_script(ssh, create_user_script, sudo=False)
        logger.info(f"✓ User {deploy_user} created")
        
        return True
        
    finally:
        ssh.close()


def setup_ssh_keys(nodes, username='ec2-user', key_file=None):
    """
    Setup SSH key-based authentication between nodes
    
    Args:
        nodes: List of node dictionaries
        username: SSH username
        key_file: SSH key file path
    
    Returns:
        True if successful
    """
    logger.info("Setting up SSH keys between nodes...")
    
    # Generate SSH key on first node
    first_node = nodes[0]['host']
    ssh = connect_ssh(first_node, username, key_file)
    
    try:
        # Generate key if not exists
        generate_key_script = """
        if [ ! -f ~/.ssh/id_rsa ]; then
            ssh-keygen -t rsa -b 4096 -f ~/.ssh/id_rsa -N ""
        fi
        cat ~/.ssh/id_rsa.pub
        """
        
        pub_key = execute_remote_command(ssh, generate_key_script).strip()
        logger.info("✓ SSH key generated")
        
    finally:
        ssh.close()
    
    # Distribute public key to all nodes in parallel
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from tqdm import tqdm
    
    def add_key_to_node(node):
        ssh = connect_ssh(node['host'], username, key_file)
        try:
            add_key_script = f"""
            mkdir -p ~/.ssh
            chmod 700 ~/.ssh
            echo "{pub_key}" >> ~/.ssh/authorized_keys
            chmod 600 ~/.ssh/authorized_keys
            """
            execute_remote_command(ssh, add_key_script)
            return node['host']
        finally:
            ssh.close()
    
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(add_key_to_node, node): node for node in nodes}
        
        with tqdm(total=len(nodes), desc="Distributing SSH keys") as pbar:
            for future in as_completed(futures):
                try:
                    host = future.result()
                    logger.info(f"✓ SSH key added to {host}")
                    pbar.update(1)
                except Exception as e:
                    logger.error(f"Failed to add SSH key: {e}")
                    raise
    
    return True


def configure_hosts_file(nodes, username='ec2-user', key_file=None):
    """
    Configure /etc/hosts on all nodes
    
    Args:
        nodes: List of node dictionaries with 'host' and 'hostname'
        username: SSH username
        key_file: SSH key file path
    
    Returns:
        True if successful
    """
    logger.info("Configuring /etc/hosts...")
    
    # Generate hosts entries
    hosts_entries = []
    for node in nodes:
        hostname = node.get('hostname', f"ds-{node.get('component', 'node')}-{node.get('index', 0)}")
        hosts_entries.append(f"{node['host']} {hostname}")
    
    hosts_content = "\n".join(hosts_entries)
    
    # Add to all nodes in parallel
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from tqdm import tqdm
    
    def update_hosts_on_node(node):
        ssh = connect_ssh(node['host'], username, key_file)
        try:
            # Backup and update hosts file
            update_hosts_script = f"""
            # Backup
            sudo cp /etc/hosts /etc/hosts.bak
            
            # Add entries if not exists
            cat << 'EOF' | sudo tee -a /etc/hosts
{hosts_content}
EOF
            """
            execute_script(ssh, update_hosts_script, sudo=False)
            return node['host']
        finally:
            ssh.close()
    
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(update_hosts_on_node, node): node for node in nodes}
        
        with tqdm(total=len(nodes), desc="Updating hosts files") as pbar:
            for future in as_completed(futures):
                try:
                    host = future.result()
                    logger.info(f"✓ Hosts file updated on {host}")
                    pbar.update(1)
                except Exception as e:
                    logger.error(f"Failed to update hosts file: {e}")
                    raise
    
    return True


def generate_application_yaml(config, component='master'):
    """
    Generate application.yaml for DolphinScheduler 3.3.2+ component
    
    Args:
        config: Configuration dictionary
        component: Component name (master, worker, api, alert)
    
    Returns:
        Configuration content string (YAML format)
    """
    db_config = config['database']
    registry_config = config['registry']
    storage_config = config['storage']
    service_config = config.get('service_config', {}).get(component, {})
    
    # Build database URL
    db_url = f"jdbc:mysql://{db_config['host']}:{db_config.get('port', 3306)}/{db_config['database']}?{db_config.get('params', 'useUnicode=true&characterEncoding=UTF-8')}"
    
    # Build Zookeeper connection string
    zk_connect = ','.join(registry_config['servers'])
    
    # Base configuration for all components
    yaml_content = f"""spring:
  profiles:
    active: mysql
  banner:
    charset: UTF-8
  jackson:
    time-zone: UTC
    date-format: "yyyy-MM-dd HH:mm:ss"
  datasource:
    driver-class-name: com.mysql.cj.jdbc.Driver
    url: {db_url}
    username: {db_config['username']}
    password: {db_config['password']}
    hikari:
      connection-test-query: select 1
      pool-name: DolphinScheduler
"""
    
    # Add Quartz configuration for master
    if component == 'master':
        yaml_content += """  quartz:
    job-store-type: jdbc
    jdbc:
      initialize-schema: never
    properties:
      org.quartz.threadPool.threadPriority: 5
      org.quartz.jobStore.isClustered: true
      org.quartz.jobStore.class: org.springframework.scheduling.quartz.LocalDataSourceJobStore
      org.quartz.scheduler.instanceId: AUTO
      org.quartz.jobStore.tablePrefix: QRTZ_
      org.quartz.jobStore.acquireTriggersWithinLock: true
      org.quartz.scheduler.instanceName: DolphinScheduler
      org.quartz.threadPool.class: org.quartz.simpl.SimpleThreadPool
      org.quartz.jobStore.useProperties: false
      org.quartz.threadPool.makeThreadsDaemons: true
      org.quartz.threadPool.threadCount: 25
      org.quartz.jobStore.misfireThreshold: 60000
      org.quartz.scheduler.batchTriggerAcquisitionMaxCount: 25
      org.quartz.scheduler.makeSchedulerThreadDaemon: true
      org.quartz.jobStore.driverDelegateClass: org.quartz.impl.jdbcjobstore.StdJDBCDelegate
      org.quartz.jobStore.clusterCheckinInterval: 5000
"""
    
    # Add registry configuration
    yaml_content += f"""
registry:
  type: {registry_config['type']}
  zookeeper:
    namespace: {registry_config.get('namespace', 'dolphinscheduler')}
    connect-string: {zk_connect}
    retry-policy:
      base-sleep-time: {registry_config.get('retry', {}).get('base_sleep_time', 1000)}ms
      max-sleep: {registry_config.get('retry', {}).get('max_sleep_time', 3000)}ms
      max-retries: {registry_config.get('retry', {}).get('max_retries', 5)}
    session-timeout: {registry_config.get('session_timeout', 60000)}ms
    connection-timeout: {registry_config.get('connection_timeout', 30000)}ms
"""
    
    # Add component-specific configuration
    if component == 'master':
        listen_port = service_config.get('listen_port', 5678)
        yaml_content += f"""
master:
  listen-port: {listen_port}
  max-heartbeat-interval: 10s
  server-load-protection:
    enabled: true
    max-system-cpu-usage-percentage-thresholds: 0.8
    max-jvm-cpu-usage-percentage-thresholds: 0.8
    max-system-memory-usage-percentage-thresholds: 0.8
    max-disk-usage-percentage-thresholds: 0.8

server:
  port: {listen_port + 1}
"""
    
    elif component == 'worker':
        listen_port = service_config.get('listen_port', 1234)
        exec_threads = service_config.get('exec_threads', 100)
        yaml_content += f"""
worker:
  listen-port: {listen_port}
  exec-threads: {exec_threads}
  max-heartbeat-interval: 10s
  server-load-protection:
    enabled: true
    max-system-cpu-usage-percentage-thresholds: 0.8
    max-jvm-cpu-usage-percentage-thresholds: 0.8
    max-system-memory-usage-percentage-thresholds: 0.8
    max-disk-usage-percentage-thresholds: 0.8

server:
  port: {listen_port + 1}
"""
    
    elif component == 'api':
        api_port = service_config.get('port', 12345)
        yaml_content += f"""
server:
  port: {api_port}

management:
  endpoints:
    web:
      exposure:
        include: health,metrics,prometheus
"""
    
    elif component == 'alert':
        alert_port = service_config.get('port', 50052)
        yaml_content += f"""
server:
  port: {alert_port}

alert:
  wait-timeout: {service_config.get('wait_timeout', 5000)}
"""
    
    return yaml_content


def generate_install_config(config):
    """
    Generate DolphinScheduler install_config.conf
    
    Args:
        config: Configuration dictionary
    
    Returns:
        Configuration content string
    """
    # Collect node information
    all_ips = []
    master_ips = []
    worker_configs = []
    api_ips = []
    alert_ip = None
    
    for node in config['cluster']['master']['nodes']:
        ip = node['host']
        all_ips.append(ip)
        master_ips.append(ip)
    
    for node in config['cluster']['worker']['nodes']:
        ip = node['host']
        groups = ','.join(node.get('groups', ['default']))
        all_ips.append(ip)
        worker_configs.append(f"{ip}:{groups}")
    
    for node in config['cluster']['api']['nodes']:
        ip = node['host']
        all_ips.append(ip)
        api_ips.append(ip)
    
    alert_ip = config['cluster']['alert']['nodes'][0]['host']
    all_ips.append(alert_ip)
    
    # Generate configuration
    db_config = config['database']
    registry_config = config['registry']
    storage_config = config['storage']
    deployment_config = config['deployment']
    
    install_config = f"""# Database Configuration
DATABASE_TYPE={db_config['type']}
SPRING_DATASOURCE_URL="jdbc:mysql://{db_config['host']}:{db_config.get('port', 3306)}/{db_config['database']}?{db_config.get('params', 'useUnicode=true&characterEncoding=UTF-8')}"
SPRING_DATASOURCE_USERNAME={db_config['username']}
SPRING_DATASOURCE_PASSWORD={db_config['password']}

# Registry Configuration
REGISTRY_TYPE={registry_config['type']}
REGISTRY_ZOOKEEPER_CONNECT_STRING="{','.join(registry_config['servers'])}"

# Resource Storage Configuration
RESOURCE_STORAGE_TYPE={storage_config['type']}
RESOURCE_UPLOAD_PATH={storage_config.get('upload_path', '/dolphinscheduler')}
AWS_REGION={storage_config['region']}
RESOURCE_STORAGE_BUCKET_NAME={storage_config['bucket']}

# Node Configuration
ips="{','.join(all_ips)}"
masters="{','.join(master_ips)}"
workers="{','.join(worker_configs)}"
apiServers="{','.join(api_ips)}"
alertServer="{alert_ip}"

# Deployment Configuration
deployUser="{deployment_config['user']}"
installPath="{deployment_config['install_path']}"
"""
    
    return install_config


def deploy_dolphinscheduler(config, package_file=None, username='ec2-user', key_file=None):
    """
    Deploy DolphinScheduler to all nodes
    
    Args:
        config: Configuration dictionary
        package_file: Path to DolphinScheduler package (optional, will download on remote if not provided)
        username: SSH username
        key_file: SSH key file path
    
    Returns:
        True if successful
    """
    logger.info("Deploying DolphinScheduler...")
    
    # Get first master node for installation
    first_master = config['cluster']['master']['nodes'][0]['host']
    install_path = config['deployment']['install_path']
    version = config['deployment']['version']
    
    ssh = connect_ssh(first_master, username, key_file)
    
    try:
        remote_package = f"/tmp/apache-dolphinscheduler-{version}-bin.tar.gz"
        
        # Option 1: Download directly on remote node (faster, recommended)
        if package_file is None or config.get('deployment', {}).get('download_on_remote', True):
            logger.info("Downloading DolphinScheduler directly on target node...")
            
            # Get download URL
            download_url = config.get('advanced', {}).get('download_url')
            if not download_url:
                # Use Apache official archive
                download_url = f"https://archive.apache.org/dist/dolphinscheduler/{version}/apache-dolphinscheduler-{version}-bin.tar.gz"
            
            logger.info(f"Download URL: {download_url}")
            
            # Download on remote
            download_script = f"""
            # Check if already downloaded
            if [ -f {remote_package} ]; then
                echo "Package already exists, verifying..."
                if gzip -t {remote_package} 2>/dev/null; then
                    echo "Package is valid, skipping download"
                    exit 0
                else
                    echo "Package is corrupted, re-downloading..."
                    rm -f {remote_package}
                fi
            fi
            
            # Download
            echo "Downloading from {download_url}..."
            wget -O {remote_package} {download_url} || curl -L -o {remote_package} {download_url}
            
            # Verify
            if gzip -t {remote_package}; then
                echo "Download successful and verified"
            else
                echo "Downloaded file is corrupted"
                rm -f {remote_package}
                exit 1
            fi
            """
            
            execute_script(ssh, download_script, sudo=False)
            logger.info("✓ Package downloaded on remote node")
            
        # Option 2: Upload from local (fallback)
        else:
            logger.info("Uploading DolphinScheduler package from local...")
            upload_file(ssh, package_file, remote_package, show_progress=True)
            
            # Verify uploaded file
            logger.info("Verifying uploaded file...")
            local_size = os.path.getsize(package_file)
            remote_size_output = execute_remote_command(ssh, f"stat -c %s {remote_package}")
            remote_size = int(remote_size_output.strip())
            
            if local_size != remote_size:
                raise Exception(f"File size mismatch: local={local_size}, remote={remote_size}")
            
            logger.info(f"✓ File verified ({remote_size / 1024 / 1024:.1f} MB)")
            
            # Test if file is valid gzip
            logger.info("Testing archive integrity...")
            test_result = execute_remote_command(ssh, f"gzip -t {remote_package} && echo 'OK' || echo 'FAILED'")
            if 'FAILED' in test_result:
                raise Exception("Uploaded file is not a valid gzip archive")
            logger.info("✓ Archive integrity verified")
        
        # Extract package
        logger.info("Extracting package...")
        extract_dir = f"/tmp/dolphinscheduler-install"
        execute_remote_command(ssh, f"rm -rf {extract_dir}")
        execute_remote_command(ssh, f"mkdir -p {extract_dir}")
        execute_remote_command(ssh, f"tar -xzf {remote_package} -C {extract_dir} --strip-components=1")
        
        # No need to generate install_config.conf for 3.3.2 (binary distribution)
        logger.info("Preparing configuration files for DolphinScheduler 3.3.2...")
        
        # DolphinScheduler 3.3.2+ uses binary distribution (no install script needed)
        # Just copy to install path and configure
        logger.info(f"Installing DolphinScheduler to {install_path}...")
        
        deploy_user = config['deployment']['user']
        
        install_script = f"""
        # Create install directory
        sudo mkdir -p {install_path}
        
        # Copy files to install path
        sudo cp -r {extract_dir}/* {install_path}/
        
        # Set ownership
        sudo chown -R {deploy_user}:{deploy_user} {install_path}
        
        # Make scripts executable
        sudo chmod +x {install_path}/bin/*.sh
        
        # Create necessary directories
        sudo -u {deploy_user} mkdir -p {install_path}/logs
        
        # Verify installation
        ls -la {install_path}
        echo ""
        echo "Installation completed to {install_path}"
        """
        
        output = execute_script(ssh, install_script, sudo=False)
        logger.info("Installation output:")
        logger.info(output)
        
        # Initialize database first (only once, before configuring components)
        logger.info("Preparing database initialization...")
        db_config = config['database']
        
        # First, configure tools/conf/application.yaml for database initialization
        logger.info("Configuring tools/conf/application.yaml for database initialization...")
        tools_yaml_content = f"""spring:
  profiles:
    active: mysql
  banner:
    charset: UTF-8
  datasource:
    driver-class-name: com.mysql.cj.jdbc.Driver
    url: jdbc:mysql://{db_config['host']}:{db_config.get('port', 3306)}/{db_config['database']}?{db_config.get('params', 'useUnicode=true&characterEncoding=UTF-8')}
    username: {db_config['username']}
    password: {db_config['password']}
    hikari:
      connection-test-query: select 1
      pool-name: DolphinScheduler

mybatis-plus:
  mapper-locations: classpath:org/apache/dolphinscheduler/dao/mapper/*Mapper.xml
  type-aliases-package: org.apache.dolphinscheduler.dao.entity
  configuration:
    cache-enabled: false
    call-setters-on-nulls: true
    map-underscore-to-camel-case: true
    jdbc-type-for-null: NULL
  global-config:
    db-config:
      id-type: auto
    banner: false
"""
        
        # Upload tools application.yaml
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(tools_yaml_content)
            temp_tools_yaml = f.name
        
        try:
            temp_remote_tools_yaml = "/tmp/tools_application.yaml"
            upload_file(ssh, temp_tools_yaml, temp_remote_tools_yaml)
            
            # Move to final location
            tools_config_path = f"{install_path}/tools/conf/application.yaml"
            move_cmd = f"sudo mv {temp_remote_tools_yaml} {tools_config_path} && sudo chown {deploy_user}:{deploy_user} {tools_config_path}"
            execute_remote_command(ssh, move_cmd)
            
            os.remove(temp_tools_yaml)
            logger.info("✓ Tools configuration uploaded")
        except Exception as e:
            logger.error(f"Failed to upload tools configuration: {e}")
            raise
        
        # Check if database is already initialized
        logger.info("Checking if database needs initialization...")
        try:
            import pymysql
            conn = pymysql.connect(
                host=db_config['host'],
                port=db_config.get('port', 3306),
                user=db_config['username'],
                password=db_config['password'],
                database=db_config['database'],
                connect_timeout=10
            )
            cursor = conn.cursor()
            cursor.execute("SHOW TABLES LIKE 't_ds_version'")
            result = cursor.fetchone()
            conn.close()
            
            if result:
                logger.info("✓ Database already initialized, skipping schema upgrade")
            else:
                logger.info("Database not initialized, running schema upgrade...")
                # Run upgrade-schema.sh from tools/bin directory
                # Set DATABASE environment variable for the script
                upgrade_cmd = f"cd {install_path}/tools && DATABASE=mysql bash bin/upgrade-schema.sh"
                output = execute_remote_command(ssh, upgrade_cmd, timeout=600)
                logger.info(f"Schema upgrade completed")
                logger.info("✓ Database initialized successfully")
        except Exception as e:
            logger.warning(f"Could not check database status: {e}")
            logger.info("Attempting to initialize database anyway...")
            try:
                upgrade_cmd = f"cd {install_path}/tools && DATABASE=mysql bash bin/upgrade-schema.sh"
                output = execute_remote_command(ssh, upgrade_cmd, timeout=600)
                logger.info(f"Schema upgrade completed")
                logger.info("✓ Database initialized")
            except Exception as init_error:
                logger.error(f"Failed to initialize database: {init_error}")
                raise
        
        # Generate and upload application.yaml for each component
        logger.info("Configuring components...")
        import tempfile
        
        component_map = {
            'master-server': 'master',
            'worker-server': 'worker',
            'api-server': 'api',
            'alert-server': 'alert'
        }
        
        for component_dir, component_name in component_map.items():
            try:
                logger.info(f"Generating application.yaml for {component_name}...")
                yaml_content = generate_application_yaml(config, component_name)
                
                # Create temp file locally
                with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
                    f.write(yaml_content)
                    temp_yaml = f.name
                
                # Upload to temporary location first (to avoid permission issues)
                temp_remote_path = f"/tmp/application_{component_name}.yaml"
                logger.info(f"Uploading application.yaml for {component_dir}...")
                upload_file(ssh, temp_yaml, temp_remote_path)
                
                # Move to final location with sudo and set correct ownership
                final_config_path = f"{install_path}/{component_dir}/conf/application.yaml"
                move_cmd = f"sudo mv {temp_remote_path} {final_config_path} && sudo chown {deploy_user}:{deploy_user} {final_config_path}"
                execute_remote_command(ssh, move_cmd)
                
                # Clean up local temp file
                os.remove(temp_yaml)
                logger.info(f"✓ {component_name} configured")
                
            except Exception as e:
                logger.error(f"Failed to configure {component_name}: {e}")
                raise
        
        # Configure dolphinscheduler_env.sh with JAVA_HOME
        logger.info("Configuring dolphinscheduler_env.sh...")
        
        # Detect JAVA_HOME on remote node
        detect_java_script = """
        # Try to find Java installation
        if [ -n "$JAVA_HOME" ] && [ -x "$JAVA_HOME/bin/java" ]; then
            echo "$JAVA_HOME"
        elif [ -x /usr/lib/jvm/java-1.8.0-amazon-corretto/bin/java ]; then
            echo "/usr/lib/jvm/java-1.8.0-amazon-corretto"
        elif [ -x /usr/lib/jvm/java-1.8.0/bin/java ]; then
            echo "/usr/lib/jvm/java-1.8.0"
        elif [ -x /usr/lib/jvm/java-8-openjdk-amd64/bin/java ]; then
            echo "/usr/lib/jvm/java-8-openjdk-amd64"
        else
            # Try to find using alternatives
            java_path=$(readlink -f $(which java) 2>/dev/null || echo "")
            if [ -n "$java_path" ]; then
                # Remove /bin/java from path
                echo "${java_path%/bin/java}"
            else
                echo "NOT_FOUND"
            fi
        fi
        """
        
        java_home = execute_script(ssh, detect_java_script, sudo=False).strip()
        
        if java_home == "NOT_FOUND" or not java_home:
            raise Exception("Could not detect JAVA_HOME on remote node")
        
        logger.info(f"Detected JAVA_HOME: {java_home}")
        
        # Generate dolphinscheduler_env.sh content
        env_sh_content = f"""#!/bin/bash
#
# Licensed to the Apache Software Foundation (ASF) under one or more
# contributor license agreements.  See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.
# The ASF licenses this file to You under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with
# the License.  You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

# JAVA_HOME configuration
export JAVA_HOME={java_home}

# DolphinScheduler home
export DOLPHINSCHEDULER_HOME={install_path}

# Never put sensitive config such as database password here in your production environment,
# this file will be sourced everytime a new task is executed.
"""
        
        # Create temp env file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as f:
            f.write(env_sh_content)
            temp_env_file = f.name
        
        # Upload dolphinscheduler_env.sh to bin/env/ (main location)
        # Note: The dolphinscheduler-daemon.sh script will automatically copy this file
        # to each component's conf directory when starting services
        try:
            # Upload to temp location first
            temp_remote_env = "/tmp/dolphinscheduler_env.sh"
            logger.info(f"Uploading dolphinscheduler_env.sh...")
            upload_file(ssh, temp_env_file, temp_remote_env)
            
            # Move to final location with sudo and set correct ownership
            final_env_path = f"{install_path}/bin/env/dolphinscheduler_env.sh"
            move_cmd = f"sudo mv {temp_remote_env} {final_env_path} && sudo chown {deploy_user}:{deploy_user} {final_env_path} && sudo chmod +x {final_env_path}"
            execute_remote_command(ssh, move_cmd)
            
            logger.info(f"✓ dolphinscheduler_env.sh configured at bin/env/")
            logger.info(f"  (Will be auto-copied to each component's conf/ when services start)")
        except Exception as e:
            logger.error(f"Failed to upload env config: {e}")
            raise
        
        # Clean up temp file
        try:
            if os.path.exists(temp_env_file):
                os.remove(temp_env_file)
        except Exception as e:
            logger.warning(f"Failed to clean up temp file: {e}")
        
        logger.info("✓ DolphinScheduler deployed")
        return True
        
    finally:
        ssh.close()


def start_services(config, username='ec2-user', key_file=None):
    """
    Start DolphinScheduler services on all nodes
    
    Args:
        config: Configuration dictionary
        username: SSH username
        key_file: SSH key file path
    
    Returns:
        True if successful
    """
    logger.info("Starting DolphinScheduler services...")
    
    install_path = config['deployment']['install_path']
    deploy_user = config['deployment']['user']
    
    # Start Master services
    logger.info("Starting Master services...")
    for i, node in enumerate(config['cluster']['master']['nodes']):
        ssh = connect_ssh(node['host'], username, key_file)
        try:
            # For 3.3.2, use dolphinscheduler-daemon.sh from main bin directory
            start_cmd = f"cd {install_path} && sudo -u {deploy_user} bash bin/dolphinscheduler-daemon.sh start master-server"
            output = execute_remote_command(ssh, start_cmd)
            logger.debug(f"Master start output: {output}")
            logger.info(f"✓ Master {i+1} started on {node['host']}")
            time.sleep(5)
        except Exception as e:
            logger.error(f"Failed to start Master on {node['host']}: {e}")
            raise
        finally:
            ssh.close()
    
    # Wait for Masters to be ready
    logger.info("Waiting for Masters to initialize...")
    time.sleep(10)
    
    # Start Worker services
    logger.info("Starting Worker services...")
    for i, node in enumerate(config['cluster']['worker']['nodes']):
        ssh = connect_ssh(node['host'], username, key_file)
        try:
            start_cmd = f"cd {install_path} && sudo -u {deploy_user} bash bin/dolphinscheduler-daemon.sh start worker-server"
            output = execute_remote_command(ssh, start_cmd)
            logger.debug(f"Worker start output: {output}")
            logger.info(f"✓ Worker {i+1} started on {node['host']}")
            time.sleep(3)
        except Exception as e:
            logger.error(f"Failed to start Worker on {node['host']}: {e}")
            raise
        finally:
            ssh.close()
    
    # Start API services
    logger.info("Starting API services...")
    for i, node in enumerate(config['cluster']['api']['nodes']):
        ssh = connect_ssh(node['host'], username, key_file)
        try:
            start_cmd = f"cd {install_path} && sudo -u {deploy_user} bash bin/dolphinscheduler-daemon.sh start api-server"
            output = execute_remote_command(ssh, start_cmd)
            logger.debug(f"API start output: {output}")
            logger.info(f"✓ API {i+1} started on {node['host']}")
            time.sleep(3)
        except Exception as e:
            logger.error(f"Failed to start API on {node['host']}: {e}")
            raise
        finally:
            ssh.close()
    
    # Start Alert service
    logger.info("Starting Alert service...")
    alert_node = config['cluster']['alert']['nodes'][0]
    ssh = connect_ssh(alert_node['host'], username, key_file)
    try:
        start_cmd = f"cd {install_path} && sudo -u {deploy_user} bash bin/dolphinscheduler-daemon.sh start alert-server"
        output = execute_remote_command(ssh, start_cmd)
        logger.debug(f"Alert start output: {output}")
        logger.info(f"✓ Alert started on {alert_node['host']}")
    except Exception as e:
        logger.error(f"Failed to start Alert on {alert_node['host']}: {e}")
        raise
    finally:
        ssh.close()
    
    logger.info("✓ All services started")
    logger.info("Waiting for services to fully initialize (30 seconds)...")
    time.sleep(30)
    
    return True


def stop_services(config, username='ec2-user', key_file=None):
    """
    Stop DolphinScheduler services on all nodes
    
    Args:
        config: Configuration dictionary
        username: SSH username
        key_file: SSH key file path
    
    Returns:
        True if successful
    """
    logger.info("Stopping DolphinScheduler services...")
    
    install_path = config['deployment']['install_path']
    
    # Stop in reverse order: Alert -> API -> Worker -> Master
    
    # Stop Alert
    alert_node = config['cluster']['alert']['nodes'][0]
    ssh = connect_ssh(alert_node['host'], username, key_file)
    try:
        execute_remote_command(
            ssh,
            f"cd {install_path} && bash bin/dolphinscheduler-daemon.sh stop alert-server"
        )
        logger.info(f"✓ Alert stopped")
    finally:
        ssh.close()
    
    # Stop API services
    for node in config['cluster']['api']['nodes']:
        ssh = connect_ssh(node['host'], username, key_file)
        try:
            execute_remote_command(
                ssh,
                f"cd {install_path} && bash bin/dolphinscheduler-daemon.sh stop api-server"
            )
            logger.info(f"✓ API stopped on {node['host']}")
        finally:
            ssh.close()
    
    # Stop Worker services
    for node in config['cluster']['worker']['nodes']:
        ssh = connect_ssh(node['host'], username, key_file)
        try:
            execute_remote_command(
                ssh,
                f"cd {install_path} && bash bin/dolphinscheduler-daemon.sh stop worker-server"
            )
            logger.info(f"✓ Worker stopped on {node['host']}")
        finally:
            ssh.close()
    
    # Stop Master services
    for node in config['cluster']['master']['nodes']:
        ssh = connect_ssh(node['host'], username, key_file)
        try:
            execute_remote_command(
                ssh,
                f"cd {install_path} && bash bin/dolphinscheduler-daemon.sh stop master-server"
            )
            logger.info(f"✓ Master stopped on {node['host']}")
        finally:
            ssh.close()
    
    logger.info("✓ All services stopped")
    return True


def check_service_status(config, username='ec2-user', key_file=None):
    """
    Check status of all services
    
    Args:
        config: Configuration dictionary
        username: SSH username
        key_file: SSH key file path
    
    Returns:
        Dictionary with service status
    """
    status = {
        'master': [],
        'worker': [],
        'api': [],
        'alert': []
    }
    
    install_path = config['deployment']['install_path']
    
    # Check Masters
    for node in config['cluster']['master']['nodes']:
        ssh = connect_ssh(node['host'], username, key_file)
        try:
            output = execute_remote_command(
                ssh,
                f"cd {install_path} && bash bin/dolphinscheduler-daemon.sh status master-server"
            )
            is_running = 'running' in output.lower()
            status['master'].append({
                'host': node['host'],
                'running': is_running
            })
        except:
            status['master'].append({
                'host': node['host'],
                'running': False
            })
        finally:
            ssh.close()
    
    # Check Workers
    for node in config['cluster']['worker']['nodes']:
        ssh = connect_ssh(node['host'], username, key_file)
        try:
            output = execute_remote_command(
                ssh,
                f"cd {install_path} && bash bin/dolphinscheduler-daemon.sh status worker-server"
            )
            is_running = 'running' in output.lower()
            status['worker'].append({
                'host': node['host'],
                'running': is_running
            })
        except:
            status['worker'].append({
                'host': node['host'],
                'running': False
            })
        finally:
            ssh.close()
    
    # Check APIs
    for node in config['cluster']['api']['nodes']:
        ssh = connect_ssh(node['host'], username, key_file)
        try:
            output = execute_remote_command(
                ssh,
                f"cd {install_path} && bash bin/dolphinscheduler-daemon.sh status api-server"
            )
            is_running = 'running' in output.lower()
            status['api'].append({
                'host': node['host'],
                'running': is_running
            })
        except:
            status['api'].append({
                'host': node['host'],
                'running': False
            })
        finally:
            ssh.close()
    
    # Check Alert
    alert_node = config['cluster']['alert']['nodes'][0]
    ssh = connect_ssh(alert_node['host'], username, key_file)
    try:
        output = execute_remote_command(
            ssh,
            f"cd {install_path} && bash bin/dolphinscheduler-daemon.sh status alert-server"
        )
        is_running = 'running' in output.lower()
        status['alert'].append({
            'host': alert_node['host'],
            'running': is_running
        })
    except:
        status['alert'].append({
            'host': alert_node['host'],
            'running': False
        })
    finally:
        ssh.close()
    
    return status
