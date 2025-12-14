"""
Node initialization and setup functions
"""
import time
import tempfile
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
from src.deploy.ssh import connect_ssh, execute_remote_command, execute_script
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


def initialize_node(host, username='ec2-user', key_file=None, config=None):
    """
    Initialize EC2 node with required dependencies
    
    Args:
        host: Host address
        username: SSH username
        key_file: SSH key file path
        config: Configuration dictionary
    
    Returns:
        True if successful
    """
    logger.debug(f"Initializing node: {host}")
    
    ssh = connect_ssh(host, username, key_file, config=config)
    
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


def create_deployment_user(host, username='ec2-user', deploy_user='dolphinscheduler', key_file=None, config=None):
    """
    Create deployment user on node
    
    Args:
        host: Host address
        username: SSH username
        deploy_user: Deployment user to create
        key_file: SSH key file path
        config: Configuration dictionary
    
    Returns:
        True if successful
    """
    logger.info(f"Creating deployment user: {deploy_user}")
    
    ssh = connect_ssh(host, username, key_file, config=config)
    
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


def setup_ssh_keys(nodes, username='ec2-user', key_file=None, config=None):
    """
    Setup SSH key-based authentication between nodes for dolphinscheduler user
    
    Args:
        nodes: List of node dictionaries
        username: SSH username (ec2-user)
        key_file: SSH key file path
        config: Configuration dictionary
    
    Returns:
        True if successful
    """
    logger.info("Setting up SSH keys between nodes for dolphinscheduler user...")
    
    deploy_user = config.get('deployment', {}).get('user', 'dolphinscheduler')
    
    # Generate SSH key on first node for dolphinscheduler user
    first_node = nodes[0]['host']
    ssh = connect_ssh(first_node, username, key_file, config=config)
    
    try:
        # Generate key for dolphinscheduler user if not exists (without randomart to avoid parsing issues)
        generate_key_script = f"""
        sudo -u {deploy_user} bash -c '
            mkdir -p /home/{deploy_user}/.ssh
            chmod 700 /home/{deploy_user}/.ssh
            if [ ! -f /home/{deploy_user}/.ssh/id_rsa ]; then
                # Generate SSH key without randomart display to avoid parsing issues
                ssh-keygen -t rsa -b 4096 -f /home/{deploy_user}/.ssh/id_rsa -N "" -q -C "{deploy_user}@dolphinscheduler"
                echo "SSH key generated successfully"
            else
                echo "SSH key already exists"
            fi
            # Ensure correct permissions
            chmod 600 /home/{deploy_user}/.ssh/id_rsa
            chmod 644 /home/{deploy_user}/.ssh/id_rsa.pub
        '
        """
        
        # Generate key first
        key_gen_output = execute_remote_command(ssh, generate_key_script)
        logger.debug(f"SSH key generation output: {key_gen_output}")
        
        # Then get the public key content separately
        get_pubkey_script = f"sudo -u {deploy_user} cat /home/{deploy_user}/.ssh/id_rsa.pub"
        pub_key_output = execute_remote_command(ssh, get_pubkey_script)
        
        # Extract only the public key line (starts with ssh-rsa, ssh-ed25519, etc.)
        pub_key_lines = pub_key_output.strip().split('\n')
        pub_key = None
        for line in pub_key_lines:
            line = line.strip()
            if line.startswith(('ssh-rsa', 'ssh-ed25519', 'ssh-dss', 'ecdsa-sha2')):
                pub_key = line
                break
        
        if not pub_key:
            raise Exception(f"Could not extract public key from output: {pub_key_output}")
            
        logger.info("✓ SSH key generated for dolphinscheduler user")
        
    finally:
        ssh.close()
    
    # Distribute public key to all nodes in parallel
    def add_key_to_node(node):
        ssh = connect_ssh(node['host'], username, key_file, config=config)
        try:
            # Create SSH directory first
            setup_ssh_dir_script = f"""
            sudo -u {deploy_user} bash -c '
                mkdir -p /home/{deploy_user}/.ssh
                chmod 700 /home/{deploy_user}/.ssh
                touch /home/{deploy_user}/.ssh/authorized_keys
                chmod 600 /home/{deploy_user}/.ssh/authorized_keys
            '
            """
            execute_remote_command(ssh, setup_ssh_dir_script)
            
            # Add public key directly using echo (safer than file upload)
            # Escape any special characters in the public key
            escaped_pub_key = pub_key.replace("'", "'\"'\"'")  # Escape single quotes
            
            add_key_script = f"""
            # Add public key to authorized_keys if not already present
            if ! sudo -u {deploy_user} grep -Fxq '{escaped_pub_key}' /home/{deploy_user}/.ssh/authorized_keys 2>/dev/null; then
                echo '{escaped_pub_key}' | sudo -u {deploy_user} tee -a /home/{deploy_user}/.ssh/authorized_keys > /dev/null
                echo "Public key added"
            else
                echo "Public key already exists"
            fi
            
            # Ensure correct permissions
            sudo -u {deploy_user} chmod 600 /home/{deploy_user}/.ssh/authorized_keys
            sudo -u {deploy_user} chmod 700 /home/{deploy_user}/.ssh
            """
            
            result = execute_remote_command(ssh, add_key_script)
            logger.debug(f"Key addition result for {node['host']}: {result}")
            
            return node['host']
        except Exception as e:
            logger.error(f"Failed to add SSH key to {node['host']}: {e}")
            raise
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
    
    # Test SSH connectivity between nodes
    logger.info("Testing SSH connectivity between nodes...")
    first_node_ssh = connect_ssh(first_node, username, key_file, config=config)
    try:
        for node in nodes[1:]:  # Test from first node to others
            test_cmd = f"sudo -u {deploy_user} ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 {deploy_user}@{node['host']} 'echo SSH_TEST_SUCCESS'"
            try:
                result = execute_remote_command(first_node_ssh, test_cmd, timeout=30)
                if "SSH_TEST_SUCCESS" in result:
                    logger.info(f"✓ SSH connectivity verified: {first_node} -> {node['host']}")
                else:
                    logger.warning(f"⚠ SSH test failed: {first_node} -> {node['host']}")
            except Exception as e:
                logger.warning(f"⚠ SSH test failed: {first_node} -> {node['host']}: {e}")
    finally:
        first_node_ssh.close()
    
    return True


def configure_hosts_file(nodes, username='ec2-user', key_file=None, config=None):
    """
    Configure /etc/hosts on all nodes
    
    Args:
        nodes: List of node dictionaries with 'host' and 'hostname'
        username: SSH username
        key_file: SSH key file path
        config: Configuration dictionary
    
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
    def update_hosts_on_node(node):
        ssh = connect_ssh(node['host'], username, key_file, config=config)
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


def initialize_nodes_parallel(hosts, max_workers=10, config=None):
    """
    Initialize multiple nodes in parallel
    
    Args:
        hosts: List of host addresses
        max_workers: Maximum parallel workers
        config: Configuration dictionary
    
    Returns:
        True if successful
    """
    logger.info(f"Initializing {len(hosts)} nodes in parallel (max {max_workers} concurrent)...")
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(initialize_node, host, config=config): host for host in hosts}
        
        completed = 0
        with tqdm(total=len(hosts), desc="Node initialization", unit="node") as pbar:
            for future in as_completed(futures):
                host = futures[future]
                try:
                    future.result()
                    completed += 1
                    pbar.update(1)
                    logger.info(f"✓ Node {completed}/{len(hosts)} initialized: {host}")
                except Exception as e:
                    logger.error(f"Initialization failed for {host}: {e}")
                    raise
    
    return True


def create_users_parallel(hosts, deploy_user, max_workers=10, config=None):
    """
    Create deployment user on multiple nodes in parallel
    
    Args:
        hosts: List of host addresses
        deploy_user: Deployment user name
        max_workers: Maximum parallel workers
        config: Configuration dictionary
    
    Returns:
        True if successful
    """
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(create_deployment_user, host, deploy_user=deploy_user, config=config): host for host in hosts}
        
        with tqdm(total=len(hosts), desc="Creating users") as pbar:
            for future in as_completed(futures):
                host = futures[future]
                try:
                    future.result()
                    pbar.update(1)
                except Exception as e:
                    logger.error(f"User creation failed for {host}: {e}")
                    raise
    
    return True


def deploy_to_single_node(host, source_dir, config, component, username='ec2-user', key_file=None):
    """
    Deploy DolphinScheduler to a single node
    
    Args:
        host: Target host IP
        source_dir: Source directory on first node
        config: Configuration dictionary
        component: Component type (master, worker, api, alert)
        username: SSH username
        key_file: SSH key file path
    
    Returns:
        True if successful
    """
    logger.debug(f"Deploying {component} component to {host}")
    
    deploy_user = config['deployment']['user']
    install_path = config['deployment']['install_path']
    
    ssh = connect_ssh(host, username, key_file, config=config)
    
    try:
        # Create install directory
        execute_remote_command(ssh, f"sudo mkdir -p {install_path}")
        execute_remote_command(ssh, f"sudo chown {deploy_user}:{deploy_user} {install_path}")
        
        # If this is not the first node, copy files from first node
        first_master = config['cluster']['master']['nodes'][0]['host']
        if host != first_master:
            # Copy DolphinScheduler files from first node
            copy_script = f"""
            sudo -u {deploy_user} scp -o StrictHostKeyChecking=no -r {deploy_user}@{first_master}:{source_dir}/* {install_path}/
            """
            execute_remote_command(ssh, copy_script, timeout=300)
        else:
            # Move files from temp directory to install path
            execute_remote_command(ssh, f"sudo -u {deploy_user} cp -r {source_dir}/* {install_path}/")
        
        # Set permissions
        execute_remote_command(ssh, f"sudo chown -R {deploy_user}:{deploy_user} {install_path}")
        execute_remote_command(ssh, f"sudo chmod +x {install_path}/bin/*.sh")
        
        logger.debug(f"✓ Files deployed to {host}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to deploy to {host}: {e}")
        raise
    finally:
        ssh.close()