"""
SSH connection and remote execution
"""
import paramiko
import time
import os
from pathlib import Path
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


def get_ssh_key_path(config=None):
    """
    Get SSH key path from environment, config, or default location
    
    Args:
        config: Configuration dictionary (optional)
    
    Returns:
        Path to SSH key
    """
    # Priority 1: Environment variable
    key_path = os.getenv('SSH_KEY_PATH')
    if key_path:
        path = Path(key_path).expanduser()
        if path.exists():
            return path
        else:
            logger.warning(f"SSH_KEY_PATH points to non-existent file: {path}")
    
    # Priority 2: Derive from config key_name
    if config and 'aws' in config and 'key_name' in config['aws']:
        key_name = config['aws']['key_name']
        # Try common naming patterns
        possible_names = [
            f"{key_name}.pem",
            f"{key_name}",
            f"{key_name}.key"
        ]
        
        # Check in common directories
        search_dirs = [
            Path.home() / '.ssh',
            Path.home() / 'Downloads',
            Path.home() / '.aws',
            Path.cwd()
        ]
        
        for directory in search_dirs:
            if directory.exists():
                for name in possible_names:
                    key_path = directory / name
                    if key_path.exists():
                        logger.info(f"Found SSH key: {key_path}")
                        return key_path
    
    # Priority 3: Try default locations
    default_paths = [
        Path.home() / '.ssh' / 'id_rsa',
        Path.home() / '.ssh' / 'id_ed25519',
    ]
    
    for path in default_paths:
        if path.exists():
            logger.info(f"Using default SSH key: {path}")
            return path
    
    # Provide helpful error message
    error_msg = "SSH key not found. Please:\n"
    error_msg += "1. Set SSH_KEY_PATH environment variable, or\n"
    error_msg += "2. Place your key file in ~/.ssh/ directory, or\n"
    if config and 'aws' in config:
        key_name = config['aws'].get('key_name', 'your-key')
        error_msg += f"3. Place {key_name}.pem in current directory or ~/Downloads/\n"
    error_msg += "4. Ensure the key file has correct permissions (chmod 600)"
    
    raise FileNotFoundError(error_msg)


def connect_ssh(host, username='ec2-user', key_file=None, port=22, config=None):
    """
    Create SSH connection
    
    Args:
        host: Host address
        username: SSH username
        key_file: Path to SSH key file
        port: SSH port
        config: Configuration dictionary (for key path resolution)
    
    Returns:
        SSH client
    """
    if key_file is None:
        key_file = get_ssh_key_path(config)
    
    # Ensure key file has correct permissions
    try:
        os.chmod(key_file, 0o600)
    except OSError as e:
        logger.warning(f"Could not set key file permissions: {e}")
    
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    try:
        logger.debug(f"Connecting to {host} with key {key_file}")
        ssh.connect(
            hostname=host,
            username=username,
            key_filename=str(key_file),
            port=port,
            timeout=30,  # Increased timeout for slow connections
            banner_timeout=60,  # Add banner timeout
            auth_timeout=60,  # Authentication timeout
            # Keep-alive settings to prevent connection drops
            sock=None,
            gss_auth=False,
            gss_kex=False,
            gss_deleg_creds=False,
            gss_host=None,
            allow_agent=False,
            look_for_keys=False
        )
        
        # Set keep-alive to prevent connection drops during long operations
        transport = ssh.get_transport()
        if transport:
            transport.set_keepalive(30)  # Send keep-alive every 30 seconds
        
        return ssh
    except paramiko.AuthenticationException:
        raise Exception(f"SSH authentication failed to {host}. Check key file: {key_file}")
    except paramiko.SSHException as e:
        raise Exception(f"SSH connection error to {host}: {str(e)}")
    except Exception as e:
        raise Exception(f"SSH connection failed to {host}: {str(e)}")


def wait_for_ssh(host, username='ec2-user', key_file=None, max_retries=30, retry_interval=10, config=None):
    """
    Wait for SSH service to be available
    
    Args:
        host: Host address
        username: SSH username
        key_file: Path to SSH key file
        max_retries: Maximum retry attempts
        retry_interval: Interval between retries (seconds)
        config: Configuration dictionary (for key path resolution)
    
    Returns:
        True if SSH is available
    """
    if key_file is None:
        key_file = get_ssh_key_path(config)
    
    logger.info(f"Waiting for SSH on {host} (using key: {key_file})")
    
    for i in range(max_retries):
        try:
            ssh = connect_ssh(host, username, key_file, config=config)
            # Test connection with a simple command
            ssh.exec_command('echo "SSH test"', timeout=5)
            ssh.close()
            logger.info(f"✓ SSH available on {host}")
            return True
        except Exception as e:
            if i < max_retries - 1:
                logger.debug(f"SSH attempt {i+1}/{max_retries} failed for {host}: {str(e)}")
                if i % 5 == 0:  # Log every 5th attempt to reduce noise
                    logger.info(f"Still waiting for SSH on {host} (attempt {i+1}/{max_retries})...")
                time.sleep(retry_interval)
            else:
                logger.error(f"SSH not available on {host} after {max_retries} attempts: {str(e)}")
                return False
    
    return False


def execute_remote_command(ssh, command, sudo=False, timeout=300):
    """
    Execute command on remote host
    
    Args:
        ssh: SSH client
        command: Command to execute
        sudo: Execute with sudo
        timeout: Command timeout (seconds)
    
    Returns:
        Command output
    """
    if sudo:
        command = f"sudo bash -c '{command}'"
    
    logger.debug(f"Executing: {command}")
    
    stdin, stdout, stderr = ssh.exec_command(command, timeout=timeout)
    exit_code = stdout.channel.recv_exit_status()
    
    output = stdout.read().decode('utf-8')
    error = stderr.read().decode('utf-8')
    
    if exit_code != 0:
        logger.error(f"Command failed (exit code {exit_code}): {error}")
        raise Exception(f"Command execution failed: {error}")
    
    return output


def upload_file(ssh, local_path, remote_path, show_progress=False):
    """
    Upload file to remote host
    
    Args:
        ssh: SSH client
        local_path: Local file path
        remote_path: Remote file path
        show_progress: Show progress bar
    
    Returns:
        True if successful
    """
    sftp = ssh.open_sftp()
    
    try:
        if show_progress:
            file_size = os.path.getsize(local_path)
            transferred = [0]
            
            def progress_callback(transferred_bytes, total_bytes):
                transferred[0] = transferred_bytes
                percent = (transferred_bytes / total_bytes) * 100
                print(f"\rUploading: {percent:.1f}%", end='', flush=True)
            
            sftp.put(local_path, remote_path, callback=progress_callback)
            print()  # New line
        else:
            sftp.put(local_path, remote_path)
        
        logger.info(f"✓ File uploaded: {remote_path}")
        return True
    finally:
        sftp.close()


def download_file(ssh, remote_path, local_path):
    """
    Download file from remote host
    
    Args:
        ssh: SSH client
        remote_path: Remote file path
        local_path: Local file path
    
    Returns:
        True if successful
    """
    sftp = ssh.open_sftp()
    
    try:
        sftp.get(remote_path, local_path)
        logger.info(f"✓ File downloaded: {local_path}")
        return True
    finally:
        sftp.close()


def execute_script(ssh, script_content, sudo=False):
    """
    Execute script on remote host
    
    Args:
        ssh: SSH client
        script_content: Script content
        sudo: Execute with sudo
    
    Returns:
        Script output
    """
    # Upload script to temp file
    temp_script = f"/tmp/script_{int(time.time())}.sh"
    
    sftp = ssh.open_sftp()
    with sftp.file(temp_script, 'w') as f:
        f.write(script_content)
    sftp.close()
    
    # Make executable
    execute_remote_command(ssh, f"chmod +x {temp_script}")
    
    # Execute
    try:
        output = execute_remote_command(ssh, temp_script, sudo=sudo)
        return output
    finally:
        # Cleanup
        execute_remote_command(ssh, f"rm -f {temp_script}")
