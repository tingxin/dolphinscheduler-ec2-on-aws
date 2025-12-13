"""
SSH connection and remote execution
"""
import paramiko
import time
import os
from pathlib import Path
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


def get_ssh_key_path():
    """
    Get SSH key path from environment or default location
    
    Returns:
        Path to SSH key
    """
    key_path = os.getenv('SSH_KEY_PATH')
    if key_path:
        return Path(key_path).expanduser()
    
    # Try default locations
    default_paths = [
        Path.home() / '.ssh' / 'id_rsa',
        Path.home() / '.ssh' / 'id_ed25519',
    ]
    
    for path in default_paths:
        if path.exists():
            return path
    
    raise FileNotFoundError("SSH key not found. Set SSH_KEY_PATH environment variable")


def connect_ssh(host, username='ec2-user', key_file=None, port=22):
    """
    Create SSH connection
    
    Args:
        host: Host address
        username: SSH username
        key_file: Path to SSH key file
        port: SSH port
    
    Returns:
        SSH client
    """
    if key_file is None:
        key_file = get_ssh_key_path()
    
    # Ensure key file has correct permissions
    os.chmod(key_file, 0o600)
    
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    try:
        ssh.connect(
            hostname=host,
            username=username,
            key_filename=str(key_file),
            port=port,
            timeout=10
        )
        return ssh
    except Exception as e:
        raise Exception(f"SSH connection failed to {host}: {str(e)}")


def wait_for_ssh(host, username='ec2-user', key_file=None, max_retries=30, retry_interval=10):
    """
    Wait for SSH service to be available
    
    Args:
        host: Host address
        username: SSH username
        key_file: Path to SSH key file
        max_retries: Maximum retry attempts
        retry_interval: Interval between retries (seconds)
    
    Returns:
        True if SSH is available
    """
    if key_file is None:
        key_file = get_ssh_key_path()
    
    for i in range(max_retries):
        try:
            ssh = connect_ssh(host, username, key_file)
            ssh.close()
            logger.info(f"✓ SSH available on {host}")
            return True
        except Exception as e:
            if i < max_retries - 1:
                logger.info(f"Waiting for SSH on {host} (attempt {i+1}/{max_retries})...")
                time.sleep(retry_interval)
            else:
                logger.error(f"SSH not available on {host}: {str(e)}")
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
