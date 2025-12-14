"""
Node initialization and setup functions
"""
import time
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
    Setup SSH key-based authentication between nodes
    
    Args:
        nodes: List of node dictionaries
        username: SSH username
        key_file: SSH key file path
        config: Configuration dictionary
    
    Returns:
        True if successful
    """
    logger.info("Setting up SSH keys between nodes...")
    
    # Generate SSH key on first node
    first_node = nodes[0]['host']
    ssh = connect_ssh(first_node, username, key_file, config=config)
    
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
    def add_key_to_node(node):
        ssh = connect_ssh(node['host'], username, key_file, config=config)
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