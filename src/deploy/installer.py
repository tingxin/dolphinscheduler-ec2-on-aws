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
            
            # Install Java with proper error handling
            echo "Installing Java on Amazon Linux 2023..."
            
            # Try different Java versions in order of preference
            JAVA_INSTALLED=false
            
            # Try Java 8 first (most compatible with DolphinScheduler)
            if sudo dnf install -y java-1.8.0-amazon-corretto java-1.8.0-amazon-corretto-devel 2>/dev/null; then
                echo "✓ Java 8 (Amazon Corretto) installed successfully"
                JAVA_INSTALLED=true
            elif sudo dnf install -y java-8-amazon-corretto java-8-amazon-corretto-devel 2>/dev/null; then
                echo "✓ Java 8 (Amazon Corretto alternative) installed successfully"
                JAVA_INSTALLED=true
            # Try Java 11 as fallback
            elif sudo dnf install -y java-11-amazon-corretto java-11-amazon-corretto-devel 2>/dev/null; then
                echo "✓ Java 11 (Amazon Corretto) installed successfully"
                JAVA_INSTALLED=true
            # Try Java 17 as last resort
            elif sudo dnf install -y java-17-amazon-corretto java-17-amazon-corretto-devel 2>/dev/null; then
                echo "✓ Java 17 (Amazon Corretto) installed successfully"
                JAVA_INSTALLED=true
            # Try OpenJDK as final fallback
            elif sudo dnf install -y java-1.8.0-openjdk java-1.8.0-openjdk-devel 2>/dev/null; then
                echo "✓ OpenJDK 8 installed successfully"
                JAVA_INSTALLED=true
            else
                echo "✗ Failed to install Java with dnf, trying alternatives..."
                # Try with yum as fallback (some AMIs might have yum)
                if command -v yum >/dev/null 2>&1; then
                    if sudo yum install -y java-1.8.0-amazon-corretto java-1.8.0-amazon-corretto-devel; then
                        echo "✓ Java 8 installed with yum"
                        JAVA_INSTALLED=true
                    fi
                fi
            fi
            
            # Verify Java installation
            if [ "$JAVA_INSTALLED" = "true" ]; then
                echo "Verifying Java installation..."
                java -version 2>&1 | head -3
                which java
                echo "JAVA_HOME candidates:"
                ls -la /usr/lib/jvm/ 2>/dev/null || echo "No /usr/lib/jvm directory"
            else
                echo "✗ CRITICAL: Java installation failed completely"
                exit 1
            fi
            
            # Install other packages
            echo "Installing other dependencies..."
            sudo dnf install -y --skip-broken \
                mysql \
                psmisc tar gzip wget curl nc \
                python3 python3-pip \
                sudo procps-ng
            
            # If mysql package not available, try mariadb as fallback
            if ! command -v mysql >/dev/null 2>&1; then
                echo "MySQL not found, installing MariaDB as fallback..."
                sudo dnf install -y mariadb105
                # Create mysql symlink for compatibility
                if [ -f /usr/bin/mariadb ] && [ ! -f /usr/bin/mysql ]; then
                    sudo ln -sf /usr/bin/mariadb /usr/bin/mysql
                fi
            fi
            
            # Verify MySQL client
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
        
        # Add to sudoers with proper permissions
        echo "{deploy_user} ALL=(ALL) NOPASSWD: ALL" | sudo tee /etc/sudoers.d/{deploy_user}
        sudo chmod 0440 /etc/sudoers.d/{deploy_user}
        
        # Set up user environment
        sudo -u {deploy_user} bash -c 'echo "export PATH=\\$PATH:/opt/dolphinscheduler/bin" >> ~/.bashrc'
        
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


def generate_application_yaml_v320(config, component='master'):
    """
    Generate application.yaml for DolphinScheduler 3.2.0 component
    
    Args:
        config: Configuration dictionary
        component: Component name (master, worker, api, alert)
    
    Returns:
        Configuration content string (YAML format)
    """
    db_config = config['database']
    registry_config = config['registry']
    service_config = config.get('service_config', {}).get(component, {})
    
    # Build database URL with 3.2.0 compatible parameters
    db_params = "useUnicode=true&characterEncoding=UTF-8&useSSL=false&allowPublicKeyRetrieval=true"
    db_url = f"jdbc:mysql://{db_config['host']}:{db_config.get('port', 3306)}/{db_config['database']}?{db_params}"
    
    # Build Zookeeper connection string
    zk_connect = ','.join(registry_config['servers'])
    
    # Base configuration for components that need database access
    if component in ['master', 'api', 'alert']:
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
      minimum-idle: 5
      maximum-pool-size: 50
      auto-commit: true
      idle-timeout: 600000
      pool-prepared-statements: true
      max-prepared-statements-per-connection: 20
      connection-timeout: 30000
      connection-init-sql: SELECT 1
      validation-timeout: 3000

"""
    else:
        # Worker doesn't need database configuration in 3.2.0
        yaml_content = f"""spring:
  profiles:
    active: mysql
  banner:
    charset: UTF-8
  jackson:
    time-zone: UTC
    date-format: "yyyy-MM-dd HH:mm:ss"

"""
    
    # Add registry configuration for all components
    yaml_content += f"""registry:
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
    
    # Add component-specific configuration for 3.2.0
    if component == 'master':
        max_cpu_load = service_config.get('max_cpu_load_avg', 3)
        reserved_memory = service_config.get('reserved_memory', 0.1)
        max_waiting_time = service_config.get('max_waiting_time', '150s')
        
        yaml_content += f"""master:
  listen-port: 5678
  max-cpu-load-avg: {max_cpu_load}
  reserved-memory: {reserved_memory}
  max-waiting-time: {max_waiting_time}
  heartbeat-interval: 10s
  task-commit-retry-times: 5
  task-commit-interval: 1000
  state-wheel-interval: 5s
  process-task-cleanup-time: 120s

"""
    
    elif component == 'worker':
        max_cpu_load = service_config.get('max_cpu_load_avg', 3)
        reserved_memory = service_config.get('reserved_memory', 0.1)
        max_waiting_time = service_config.get('max_waiting_time', '150s')
        
        yaml_content += f"""worker:
  listen-port: 1234
  max-cpu-load-avg: {max_cpu_load}
  reserved-memory: {reserved_memory}
  max-waiting-time: {max_waiting_time}
  heartbeat-interval: 10s
  host-weight: 100
  tenant-auto-create: true
  exec-threads: 100

"""
    
    elif component == 'api':
        api_port = service_config.get('port', 12345)
        yaml_content += f"""server:
  port: {api_port}
  servlet:
    session:
      timeout: 7200s
    context-path: /dolphinscheduler
  compression:
    enabled: true
    mime-types: text/html,text/xml,text/plain,text/css,text/javascript,application/javascript,application/json,application/xml
  jetty:
    max-http-post-size: 5000000

management:
  endpoints:
    web:
      exposure:
        include: health,metrics,prometheus
  endpoint:
    health:
      enabled: true
      show-details: always
  health:
    db:
      enabled: true
    defaults:
      enabled: false

"""
    
    elif component == 'alert':
        yaml_content += f"""server:
  port: 50052

alert:
  port: 50052
  wait-timeout: 5000

"""
    
    return yaml_content


def generate_install_env_v320(config):
    """
    Generate install_env.sh for DolphinScheduler 3.2.0
    
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
    
    # Generate install_env.sh content for 3.2.0
    deployment_config = config['deployment']
    registry_config = config['registry']
    
    install_env = f"""#!/bin/bash
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

# ---------------------------------------------------------
# INSTALL MACHINE
# ---------------------------------------------------------
# A comma separated list of machine hostname or IP would be installed DolphinScheduler,
# including master, worker, api, alert. If you want to deploy in pseudo-distributed
# mode, just write a pseudo-distributed hostname
# Example for hostnames: ips="ds1,ds2,ds3,ds4,ds5", Example for IPs: ips="192.168.8.1,192.168.8.2,192.168.8.3,192.168.8.4,192.168.8.5"
ips="{','.join(all_ips)}"

# Port of SSH protocol, default value is 22. For now we only support same port in all `ips` machine
# modify it if you use different ssh port
sshPort="22"

# A comma separated list of machine hostname or IP would be installed Master server, it
# must be a subset of configuration `ips`.
# Example for hostnames: masters="ds1,ds2", Example for IPs: masters="192.168.8.1,192.168.8.2"
masters="{','.join(master_ips)}"

# A comma separated list of machine <hostname>:<workerGroup> or <IP>:<workerGroup>.All hostname or IP must be a
# subset of configuration `ips`, And workerGroup have default value as `default`, but we recommend you declare behind the hosts
# Example for hostnames: workers="ds1:default,ds2:default,ds3:default", Example for IPs: workers="192.168.8.1:default,192.168.8.2:default,192.168.8.3:default"
workers="{','.join(worker_configs)}"

# A comma separated list of machine hostname or IP would be installed Alert server, it
# must be a subset of configuration `ips`.
# Example for hostname: alertServer="ds3", Example for IP: alertServer="192.168.8.3"
alertServer="{alert_ip}"

# A comma separated list of machine hostname or IP would be installed API server, it
# must be a subset of configuration `ips`.
# Example for hostnames: apiServers="ds1", Example for IPs: apiServers="192.168.8.1"
apiServers="{','.join(api_ips)}"

# The directory to install DolphinScheduler for all machine we config above. It will automatically be created by `install.sh` script if not exists.
# Do not set this configuration same as the current path (pwd). Do not add quotes to it if you using related path.
installPath="{deployment_config['install_path']}"

# The user to deploy DolphinScheduler for all machine we config above. For now user must create by yourself before running `install.sh`
# script. The user needs to have sudo privileges and permissions to operate hdfs. If hdfs is enabled than the root directory needs
# to be created by this user
deployUser="{deployment_config['user']}"

# The root of zookeeper, for now DolphinScheduler default registry server is zookeeper.
zkRoot="{registry_config.get('namespace', '/dolphinscheduler')}"
"""
    
    return install_env


def generate_dolphinscheduler_env_v320(config):
    """
    Generate dolphinscheduler_env.sh for DolphinScheduler 3.2.0
    
    Args:
        config: Configuration dictionary
    
    Returns:
        Configuration content string
    """
    db_config = config['database']
    registry_config = config['registry']
    
    # Build database URL with 3.2.0 compatible parameters
    db_params = "useUnicode=true&characterEncoding=UTF-8&useSSL=false&allowPublicKeyRetrieval=true"
    db_url = f"jdbc:mysql://{db_config['host']}:{db_config.get('port', 3306)}/{db_config['database']}?{db_params}"
    
    # Build Zookeeper connection string
    zk_connect = ','.join(registry_config['servers'])
    
    env_content = f"""#!/bin/bash
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

# JAVA_HOME, will use it to start DolphinScheduler server
export JAVA_HOME=${{JAVA_HOME:-/usr/lib/jvm/java-1.8.0}}

# Database related configuration, set database type, username and password
export DATABASE=${{DATABASE:-mysql}}
export SPRING_PROFILES_ACTIVE=${{DATABASE}}
export SPRING_DATASOURCE_URL="{db_url}"
export SPRING_DATASOURCE_USERNAME={db_config['username']}
export SPRING_DATASOURCE_PASSWORD={db_config['password']}

# DolphinScheduler server related configuration
export SPRING_CACHE_TYPE=${{SPRING_CACHE_TYPE:-none}}
export SPRING_JACKSON_TIME_ZONE=${{SPRING_JACKSON_TIME_ZONE:-UTC}}
export MASTER_FETCH_COMMAND_NUM=${{MASTER_FETCH_COMMAND_NUM:-10}}

# Registry center configuration, determines the type and link of the registry center
export REGISTRY_TYPE=${{REGISTRY_TYPE:-zookeeper}}
export REGISTRY_ZOOKEEPER_CONNECT_STRING=${{REGISTRY_ZOOKEEPER_CONNECT_STRING:-{zk_connect}}}

# Tasks related configurations, need to change the configuration if you use the related tasks.
export HADOOP_HOME=${{HADOOP_HOME:-/opt/soft/hadoop}}
export HADOOP_CONF_DIR=${{HADOOP_CONF_DIR:-/opt/soft/hadoop/etc/hadoop}}
export SPARK_HOME=${{SPARK_HOME:-/opt/soft/spark}}
export PYTHON_LAUNCHER=${{PYTHON_LAUNCHER:-/usr/bin/python}}
export HIVE_HOME=${{HIVE_HOME:-/opt/soft/hive}}
export FLINK_HOME=${{FLINK_HOME:-/opt/soft/flink}}
export DATAX_LAUNCHER=${{DATAX_LAUNCHER:-/opt/soft/datax/bin/datax.py}}

export PATH=$HADOOP_HOME/bin:$SPARK_HOME/bin:$PYTHON_LAUNCHER:$JAVA_HOME/bin:$HIVE_HOME/bin:$FLINK_HOME/bin:$DATAX_LAUNCHER:$PATH
"""
    
    return env_content


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
    install_path = config['deployment']['install_path']
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
        remote_package = f"/tmp/apache-dolphinscheduler-{version}-bin.tar.gz"
        extract_dir = f"/tmp/apache-dolphinscheduler-{version}-bin"
        
        # Step 1: Download and extract package
        if package_file is None or config.get('deployment', {}).get('download_on_remote', True):
            logger.info("Downloading DolphinScheduler 3.2.0 directly on target node...")
            
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
        else:
            logger.info("Uploading DolphinScheduler package from local...")
            upload_file(ssh, package_file, remote_package, show_progress=True)
            
            # Extract
            execute_remote_command(ssh, f"cd /tmp && tar -xzf {remote_package}")
            logger.info("✓ Package uploaded and extracted")
        
        # Step 2: Set ownership and permissions
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
        
        # Step 3: Generate and upload install_env.sh
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
        
        # Step 4: Generate and upload dolphinscheduler_env.sh
        logger.info("Generating dolphinscheduler_env.sh configuration...")
        ds_env_content = generate_dolphinscheduler_env_v320(config)
        
        # ========================================================================
        # STEP 1: Configure dolphinscheduler_env.sh FIRST (contains JAVA_HOME)
        # This is required for database initialization script to work
        # ========================================================================
        logger.info("Configuring dolphinscheduler_env.sh (required for database initialization)...")
        
        # Detect JAVA_HOME on remote node
        logger.info("Detecting JAVA_HOME...")
        
        # First verify Java is installed
        try:
            java_version = execute_remote_command(ssh, "java -version 2>&1 | head -1")
            logger.info(f"Java version: {java_version.strip()}")
        except Exception as e:
            logger.error(f"Java not accessible: {e}")
            raise Exception("Java is not installed or not in PATH. Check node initialization.")
        
        # Try to find JAVA_HOME using multiple methods
        java_home = None
        
        # Method 1: Try common paths
        common_paths = [
            "/usr/lib/jvm/java-1.8.0-amazon-corretto",
            "/usr/lib/jvm/java-1.8.0-amazon-corretto.x86_64",
            "/usr/lib/jvm/java-11-amazon-corretto",
            "/usr/lib/jvm/java-1.8.0",
            "/usr/lib/jvm/java-8-openjdk-amd64",
        ]
        
        for path in common_paths:
            try:
                result = execute_remote_command(ssh, f"[ -x {path}/bin/java ] && echo '{path}' || echo ''")
                if result.strip() and result.strip() != "":
                    java_home = result.strip()
                    logger.info(f"Found JAVA_HOME at: {java_home}")
                    break
            except:
                continue
        
        # Method 2: Use readlink on 'which java'
        if not java_home:
            try:
                result = execute_remote_command(ssh, "readlink -f $(which java) 2>/dev/null || which java")
                java_bin = result.strip()
                if java_bin and "/bin/java" in java_bin:
                    java_home = java_bin.replace("/bin/java", "")
                    if java_home.endswith("/jre"):
                        java_home = java_home[:-4]
                    logger.info(f"Found JAVA_HOME via readlink: {java_home}")
            except Exception as e:
                logger.warning(f"readlink method failed: {e}")
        
        # Method 3: List /usr/lib/jvm and find first valid Java
        if not java_home:
            try:
                jvm_list = execute_remote_command(ssh, "ls /usr/lib/jvm/ 2>/dev/null || echo ''")
                for jvm_dir in jvm_list.strip().split('\n'):
                    if jvm_dir and 'java' in jvm_dir.lower():
                        test_path = f"/usr/lib/jvm/{jvm_dir}"
                        try:
                            result = execute_remote_command(ssh, f"[ -x {test_path}/bin/java ] && echo '{test_path}' || echo ''")
                            if result.strip():
                                java_home = result.strip()
                                logger.info(f"Found JAVA_HOME in /usr/lib/jvm: {java_home}")
                                break
                        except:
                            continue
            except Exception as e:
                logger.warning(f"JVM directory search failed: {e}")
        
        # If still not found, show debug info and fail
        if not java_home:
            logger.error("Could not detect JAVA_HOME. Debug information:")
            try:
                which_result = execute_remote_command(ssh, "which java 2>&1")
                logger.error(f"  which java: {which_result.strip()}")
            except:
                pass
            try:
                jvm_contents = execute_remote_command(ssh, "ls -la /usr/lib/jvm/ 2>&1")
                logger.error(f"  /usr/lib/jvm contents:\n{jvm_contents}")
            except:
                pass
            raise Exception("Could not detect JAVA_HOME. Java may not be installed correctly.")
        
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
        
        # Upload dolphinscheduler_env.sh to bin/env/
        try:
            temp_remote_env = "/tmp/dolphinscheduler_env.sh"
            upload_file(ssh, temp_env_file, temp_remote_env)
            
            # Move to final location with sudo and set correct ownership
            final_env_path = f"{install_path}/bin/env/dolphinscheduler_env.sh"
            move_cmd = f"sudo mv {temp_remote_env} {final_env_path} && sudo chown {deploy_user}:{deploy_user} {final_env_path} && sudo chmod +x {final_env_path}"
            execute_remote_command(ssh, move_cmd)
            
            logger.info(f"✓ dolphinscheduler_env.sh configured with JAVA_HOME={java_home}")
        except Exception as e:
            logger.error(f"Failed to upload env config: {e}")
            raise
        finally:
            # Clean up local temp file
            try:
                if os.path.exists(temp_env_file):
                    os.remove(temp_env_file)
            except Exception as e:
                logger.warning(f"Failed to clean up temp file: {e}")
        
        # ========================================================================
        # STEP 2: Install DolphinScheduler plugins (required for 3.3.0+)
        # ========================================================================
        logger.info("Installing DolphinScheduler plugins...")
        
        # Configure plugins - use minimal set to avoid Maven wrapper issues
        # DolphinScheduler 3.3.2 binary distribution should include basic plugins
        plugins_config = """--task-plugins--
dolphinscheduler-task-shell
dolphinscheduler-task-sql
--end--

--alert-plugins--
dolphinscheduler-alert-email
--end--

--datasource-plugins--
dolphinscheduler-datasource-mysql
--end--"""
        
        # Create plugins config file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
            f.write(plugins_config)
            temp_plugins_config = f.name
        
        try:
            # Upload plugins config
            temp_remote_plugins_config = "/tmp/plugins_config"
            upload_file(ssh, temp_plugins_config, temp_remote_plugins_config)
            
            # Move to final location
            plugins_config_path = f"{install_path}/conf/plugins_config"
            move_cmd = f"sudo mv {temp_remote_plugins_config} {plugins_config_path} && sudo chown {deploy_user}:{deploy_user} {plugins_config_path}"
            execute_remote_command(ssh, move_cmd)
            
            os.remove(temp_plugins_config)
            logger.info("✓ Plugins configuration created")
            
            # Try plugin installation, but don't fail deployment if it doesn't work
            logger.info("Attempting plugin installation...")
            
            # First, check if install-plugins.sh exists and is executable
            check_script_cmd = f"ls -la {install_path}/bin/install-plugins.sh 2>/dev/null || echo 'Script not found'"
            script_check = execute_remote_command(ssh, check_script_cmd)
            logger.info(f"Plugin script check: {script_check}")
            
            plugins_installed = False
            
            if "install-plugins.sh" in script_check and "Script not found" not in script_check:
                try:
                    # Make sure script is executable
                    execute_remote_command(ssh, f"sudo chmod +x {install_path}/bin/install-plugins.sh")
                    
                    # Check if Maven wrapper exists
                    mvnw_check = execute_remote_command(ssh, f"ls -la {install_path}/mvnw {install_path}/.mvn/ 2>/dev/null || echo 'Maven wrapper missing'")
                    logger.info(f"Maven wrapper check: {mvnw_check}")
                    
                    if "Maven wrapper missing" not in mvnw_check:
                        # Try to run plugin installation
                        install_plugins_cmd = f"cd {install_path} && sudo -u {deploy_user} bash bin/install-plugins.sh {version}"
                        logger.info("Running install-plugins.sh script (this may take several minutes)...")
                        
                        # Check SSH connection before long operation
                        try:
                            ssh.exec_command('echo "SSH connection check"')
                        except:
                            logger.warning("SSH connection lost, reconnecting...")
                            ssh = get_ssh_connection()
                        
                        plugins_output = execute_remote_command(ssh, install_plugins_cmd, timeout=600)
                        logger.info(f"Plugins installation output: {plugins_output}")
                        
                        # Check if installation was successful
                        if "BUILD SUCCESS" in plugins_output or "successfully" in plugins_output.lower():
                            logger.info("✓ Plugins installed successfully")
                            plugins_installed = True
                        else:
                            logger.warning("Plugin installation completed but may have issues")
                    else:
                        logger.warning("Maven wrapper files missing, skipping automatic plugin installation")
                        
                except Exception as e:
                    logger.warning(f"Plugin installation failed: {e}")
                    logger.info("Will proceed with manual dependency installation...")
            else:
                logger.warning("install-plugins.sh script not found, skipping automatic plugin installation")
            
            # Always ensure MySQL JDBC driver is available (critical for database connectivity)
            logger.info("Ensuring MySQL JDBC driver is available...")
            
            # Check if driver already exists
            check_driver_cmd = f"find {install_path}/libs/ -name '*mysql*' -o -name '*connector*' 2>/dev/null || echo 'No MySQL driver found'"
            driver_check = execute_remote_command(ssh, check_driver_cmd)
            logger.info(f"MySQL driver check: {driver_check}")
            
            if "No MySQL driver found" in driver_check or not driver_check.strip():
                logger.info("MySQL JDBC driver not found, downloading...")
                
                # Try multiple download sources for MySQL JDBC driver
                mysql_drivers = [
                    {
                        "url": "https://repo1.maven.org/maven2/com/mysql/mysql-connector-j/8.0.33/mysql-connector-j-8.0.33.jar",
                        "filename": "mysql-connector-j-8.0.33.jar"
                    },
                    {
                        "url": "https://repo1.maven.org/maven2/com/mysql/mysql-connector-j/8.2.0/mysql-connector-j-8.2.0.jar",
                        "filename": "mysql-connector-j-8.2.0.jar"
                    },
                    {
                        "url": "https://repo1.maven.org/maven2/com/mysql/mysql-connector-j/8.1.0/mysql-connector-j-8.1.0.jar",
                        "filename": "mysql-connector-j-8.1.0.jar"
                    }
                ]
                
                driver_installed = False
                for driver in mysql_drivers:
                    try:
                        logger.info(f"Trying to download MySQL driver from: {driver['url']}")
                        download_driver_cmd = f"""
                        cd /tmp && \
                        rm -f {driver['filename']} && \
                        (wget -O {driver['filename']} {driver['url']} || curl -L -o {driver['filename']} {driver['url']}) && \
                        sudo mkdir -p {install_path}/libs/ && \
                        sudo cp {driver['filename']} {install_path}/libs/ && \
                        sudo chown {deploy_user}:{deploy_user} {install_path}/libs/{driver['filename']} && \
                        ls -la {install_path}/libs/{driver['filename']}
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
                    # Don't fail deployment, but warn user
                    logger.warning("⚠️ MySQL JDBC driver installation failed. You may need to manually install it later.")
                    logger.warning("⚠️ Download mysql-connector-j-8.0.33.jar and place it in {install_path}/libs/")
            else:
                logger.info("✓ MySQL JDBC driver already available")
            
            # Verify essential libraries exist
            logger.info("Verifying essential libraries...")
            verify_libs_cmd = f"ls -la {install_path}/libs/ 2>/dev/null | head -10 || echo 'Libs directory empty'"
            libs_output = execute_remote_command(ssh, verify_libs_cmd)
            logger.info(f"Available libraries: {libs_output}")
            
            # Create libs directory if it doesn't exist
            execute_remote_command(ssh, f"sudo mkdir -p {install_path}/libs/ && sudo chown {deploy_user}:{deploy_user} {install_path}/libs/")
                    
        except Exception as e:
            logger.error(f"Failed to configure plugins: {e}")
            raise
        
        # ========================================================================
        # STEP 3: Initialize database (now JAVA_HOME and JDBC driver are available)
        # ========================================================================
        logger.info("Preparing database initialization...")
        db_config = config['database']
        
        # Configure tools/conf/application.yaml for database initialization
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
        
        # Prepare database before initialization
        logger.info("Preparing database for DolphinScheduler...")
        try:
            import pymysql
            # 确保密码是字符串类型，解决 PyMySQL 兼容性问题
            password = str(db_config['password']) if db_config['password'] is not None else ''
            
            # Validate database configuration first
            required_db_fields = ['host', 'username', 'password', 'database']
            for field in required_db_fields:
                if not db_config.get(field):
                    raise ValueError(f"Database configuration missing required field: {field}")
            
            logger.info(f"Connecting to database: {db_config['host']}:{db_config.get('port', 3306)}")
            logger.info(f"Database name: {db_config['database']}")
            logger.info(f"Username: {db_config['username']}")
            
            # Connect to MySQL server (without specifying database)
            conn = pymysql.connect(
                host=db_config['host'],
                port=db_config.get('port', 3306),
                user=db_config['username'],
                password=password,
                connect_timeout=10,
                charset='utf8mb4'
            )
            cursor = conn.cursor()
            
            # Try to create database if not exists (may fail if user lacks privileges)
            database_name = db_config['database']
            try:
                cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{database_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
                logger.info(f"✓ Database '{database_name}' created/verified")
            except Exception as e:
                logger.warning(f"Could not create database (may already exist): {e}")
            
            # Try to grant permissions (may fail if user lacks privileges)
            try:
                cursor.execute(f"GRANT ALL PRIVILEGES ON `{database_name}`.* TO '{db_config['username']}'@'%'")
                cursor.execute("FLUSH PRIVILEGES")
                logger.info("✓ Database permissions configured")
            except Exception as e:
                logger.warning(f"Could not grant permissions (may already be set): {e}")
            
            conn.close()
            
            # Now connect to the specific database to check initialization
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
            cursor.execute("SHOW TABLES LIKE 't_ds_version'")
            result = cursor.fetchone()
            conn.close()
            
            if result:
                logger.info("✓ Database already initialized, skipping schema upgrade")
            else:
                logger.info("Database not initialized, running schema upgrade...")
                
                # Ensure dolphinscheduler user has write permission to tools directory for gc.log
                logger.info("Setting permissions for database initialization...")
                perm_cmd = f"sudo chown -R {deploy_user}:{deploy_user} {install_path}/tools && sudo chmod -R 755 {install_path}/tools"
                execute_remote_command(ssh, perm_cmd)
                
                # Create a wrapper script to ensure proper environment setup
                # This approach is more reliable than complex sudo -E commands
                wrapper_script = f"""#!/bin/bash
set -e
cd {install_path}/tools

# Set environment variables
export DATABASE=mysql
export SPRING_PROFILES_ACTIVE=mysql
export SPRING_DATASOURCE_URL="jdbc:mysql://{db_config['host']}:{db_config.get('port', 3306)}/{db_config['database']}?useUnicode=true&characterEncoding=UTF-8&useSSL=false"
export SPRING_DATASOURCE_USERNAME="{db_config['username']}"
export SPRING_DATASOURCE_PASSWORD="{password}"
export JAVA_HOME={java_home}
export PATH=$JAVA_HOME/bin:$PATH
export DOLPHINSCHEDULER_HOME={install_path}
export JAVA_OPTS='-server -Duser.timezone=UTC -Xms1g -Xmx1g -XX:+HeapDumpOnOutOfMemoryError -XX:HeapDumpPath={install_path}/logs/dump.hprof'

# Debug information
echo "=== Environment Debug ==="
echo "JAVA_HOME: $JAVA_HOME"
echo "DATABASE: $DATABASE"
echo "SPRING_DATASOURCE_URL: $SPRING_DATASOURCE_URL"
echo "SPRING_DATASOURCE_USERNAME: $SPRING_DATASOURCE_USERNAME"
echo "Working directory: $(pwd)"
echo "Java version: $(java -version 2>&1 | head -1)"
echo "MySQL version: $(mysql --version 2>&1 | head -1)"
echo "========================="

# Run the upgrade script
bash bin/upgrade-schema.sh
"""
                
                # Upload wrapper script
                with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as f:
                    f.write(wrapper_script)
                    temp_wrapper = f.name
                
                try:
                    temp_remote_wrapper = "/tmp/db_init_wrapper.sh"
                    upload_file(ssh, temp_wrapper, temp_remote_wrapper)
                    
                    # Make wrapper executable and run it
                    execute_remote_command(ssh, f"chmod +x {temp_remote_wrapper}")
                    upgrade_cmd = f"sudo -u {deploy_user} bash {temp_remote_wrapper} 2>&1"
                    
                    os.remove(temp_wrapper)
                except Exception as e:
                    logger.error(f"Failed to create wrapper script: {e}")
                    raise
                logger.info(f"Executing database initialization...")
                logger.debug(f"Command: {upgrade_cmd}")
                
                # First check if the script exists and is executable
                check_script_cmd = f"ls -la {install_path}/tools/bin/upgrade-schema.sh 2>/dev/null || echo 'Script not found'"
                script_check = execute_remote_command(ssh, check_script_cmd)
                logger.info(f"Script check result: {script_check}")
                
                # Make sure the script is executable
                chmod_cmd = f"sudo chmod +x {install_path}/tools/bin/*.sh"
                execute_remote_command(ssh, chmod_cmd)
                
                # Also check if required commands are available in the script environment
                env_check_cmd = f"""cd {install_path}/tools && \
export JAVA_HOME={java_home} && \
export PATH=$JAVA_HOME/bin:$PATH && \
echo "Java check:" && java -version 2>&1 | head -1 && \
echo "MySQL check:" && mysql --version 2>&1 | head -1 && \
echo "Script exists:" && ls -la bin/upgrade-schema.sh"""
                env_check = execute_remote_command(ssh, env_check_cmd)
                logger.info(f"Environment check: {env_check}")
                
                try:
                    output = execute_remote_command(ssh, upgrade_cmd, timeout=600)
                except Exception as e:
                    # Capture the error output for analysis
                    error_msg = str(e)
                    logger.error(f"Database initialization failed with error: {error_msg}")
                    
                    # Try to get more detailed output by running the command without error handling
                    try:
                        stdin, stdout, stderr = ssh.exec_command(upgrade_cmd, timeout=600)
                        exit_code = stdout.channel.recv_exit_status()
                        stdout_output = stdout.read().decode('utf-8')
                        stderr_output = stderr.read().decode('utf-8')
                        
                        logger.info(f"Database initialization detailed output:")
                        logger.info("=" * 60)
                        logger.info(f"Exit code: {exit_code}")
                        logger.info(f"STDOUT:\n{stdout_output}")
                        logger.info(f"STDERR:\n{stderr_output}")
                        logger.info("=" * 60)
                        
                        output = stdout_output + "\n" + stderr_output
                    except Exception as detail_error:
                        logger.error(f"Could not get detailed output: {detail_error}")
                        output = error_msg
                
                logger.info(f"Database initialization output:")
                logger.info("=" * 60)
                logger.info(output)
                logger.info("=" * 60)
                
                # Check if initialization was successful by looking for success indicators
                if "successfully" in output.lower() or "completed" in output.lower():
                    logger.info("✓ Database initialized successfully")
                else:
                    logger.warning("Database initialization may have failed, checking output...")
                    # Try to extract specific error information
                    lines = output.split('\n')
                    error_lines = [line for line in lines if 'error' in line.lower() or 'exception' in line.lower() or 'failed' in line.lower()]
                    if error_lines:
                        logger.error("Detected errors in database initialization:")
                        for error_line in error_lines[:5]:  # Show first 5 error lines
                            logger.error(f"  {error_line.strip()}")
                    raise Exception(f"Database initialization failed with output: {output[-500:]}")  # Last 500 chars
        except Exception as e:
            logger.warning(f"Could not check database status: {e}")
            logger.info("Attempting to initialize database anyway...")
            try:
                # Ensure permissions first
                logger.info("Setting permissions for database initialization...")
                perm_cmd = f"sudo chown -R {deploy_user}:{deploy_user} {install_path}/tools && sudo chmod -R 755 {install_path}/tools"
                execute_remote_command(ssh, perm_cmd)
                
                # Create retry wrapper script with enhanced debugging
                retry_wrapper_script = f"""#!/bin/bash
set -e
cd {install_path}/tools

# Set environment variables
export DATABASE=mysql
export SPRING_PROFILES_ACTIVE=mysql
export SPRING_DATASOURCE_URL="jdbc:mysql://{db_config['host']}:{db_config.get('port', 3306)}/{db_config['database']}?useUnicode=true&characterEncoding=UTF-8&useSSL=false"
export SPRING_DATASOURCE_USERNAME="{db_config['username']}"
export SPRING_DATASOURCE_PASSWORD="{password}"
export JAVA_HOME={java_home}
export PATH=$JAVA_HOME/bin:$PATH
export DOLPHINSCHEDULER_HOME={install_path}
export JAVA_OPTS='-server -Duser.timezone=UTC -Xms1g -Xmx1g -XX:+HeapDumpOnOutOfMemoryError -XX:HeapDumpPath={install_path}/logs/dump.hprof'

# Enhanced debug information
echo "=== Retry Environment Debug ==="
echo "JAVA_HOME: $JAVA_HOME"
echo "DATABASE: $DATABASE"
echo "SPRING_DATASOURCE_URL: $SPRING_DATASOURCE_URL"
echo "SPRING_DATASOURCE_USERNAME: $SPRING_DATASOURCE_USERNAME"
echo "Working directory: $(pwd)"
echo "Java version: $(java -version 2>&1 | head -1)"
echo "MySQL version: $(mysql --version 2>&1 | head -1)"
echo "Script permissions: $(ls -la bin/upgrade-schema.sh)"
echo "Available files in bin/: $(ls -la bin/)"
echo "==============================="

# Test database connectivity first
echo "Testing database connectivity..."
mysql -h {db_config['host']} -P {db_config.get('port', 3306)} -u {db_config['username']} -p{password} -e "SELECT 1;" {db_config['database']} || echo "Database connection test failed"

# Run the upgrade script with verbose output
echo "Running upgrade-schema.sh..."
bash -x bin/upgrade-schema.sh
"""
                
                # Upload retry wrapper script
                with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as f:
                    f.write(retry_wrapper_script)
                    temp_retry_wrapper = f.name
                
                try:
                    temp_remote_retry_wrapper = "/tmp/db_init_retry_wrapper.sh"
                    upload_file(ssh, temp_retry_wrapper, temp_remote_retry_wrapper)
                    
                    # Make wrapper executable and run it
                    execute_remote_command(ssh, f"chmod +x {temp_remote_retry_wrapper}")
                    upgrade_cmd = f"sudo -u {deploy_user} bash {temp_remote_retry_wrapper} 2>&1"
                    
                    os.remove(temp_retry_wrapper)
                except Exception as e:
                    logger.error(f"Failed to create retry wrapper script: {e}")
                    raise
                logger.info(f"Attempting database initialization (retry)...")
                logger.debug(f"Retry command: {upgrade_cmd}")
                
                # Check script again and ensure it's executable
                script_check = execute_remote_command(ssh, check_script_cmd)
                logger.info(f"Retry script check: {script_check}")
                
                # Make sure the script is executable
                chmod_cmd = f"sudo chmod +x {install_path}/tools/bin/*.sh"
                execute_remote_command(ssh, chmod_cmd)
                
                try:
                    output = execute_remote_command(ssh, upgrade_cmd, timeout=600)
                except Exception as e:
                    # Capture the error output for analysis
                    error_msg = str(e)
                    logger.error(f"Database initialization retry failed with error: {error_msg}")
                    
                    # Try to get more detailed output
                    try:
                        stdin, stdout, stderr = ssh.exec_command(upgrade_cmd, timeout=600)
                        exit_code = stdout.channel.recv_exit_status()
                        stdout_output = stdout.read().decode('utf-8')
                        stderr_output = stderr.read().decode('utf-8')
                        
                        logger.info(f"Database initialization retry detailed output:")
                        logger.info("=" * 60)
                        logger.info(f"Exit code: {exit_code}")
                        logger.info(f"STDOUT:\n{stdout_output}")
                        logger.info(f"STDERR:\n{stderr_output}")
                        logger.info("=" * 60)
                        
                        output = stdout_output + "\n" + stderr_output
                    except Exception as detail_error:
                        logger.error(f"Could not get detailed retry output: {detail_error}")
                        output = error_msg
                
                logger.info(f"Database initialization retry output:")
                logger.info("=" * 60)
                logger.info(output)
                logger.info("=" * 60)
                
                # Check if initialization was successful
                if "successfully" in output.lower() or "completed" in output.lower():
                    logger.info("✓ Database initialized successfully")
                else:
                    logger.error("Database initialization failed on retry")
                    # Extract error information
                    lines = output.split('\n')
                    error_lines = [line for line in lines if 'error' in line.lower() or 'exception' in line.lower() or 'failed' in line.lower()]
                    if error_lines:
                        logger.error("Detected errors:")
                        for error_line in error_lines[:5]:
                            logger.error(f"  {error_line.strip()}")
                    raise Exception(f"Database initialization retry failed: {output[-500:]}")
            except Exception as init_error:
                logger.error(f"Failed to initialize database: {init_error}")
                raise
        
        # Generate and upload application.yaml for each component
        logger.info("Configuring components...")
        
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
        
        # Step 5: Install MySQL JDBC driver to all component libs directories
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
        
        # Step 6: Initialize database schema
        logger.info("Initializing database schema...")
        db_config = config['database']
        
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
        
        # Step 7: Generate application.yaml files for all components
        logger.info("Generating component configuration files...")
        
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
        
        # Step 8: Run the install.sh script to deploy to all nodes
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