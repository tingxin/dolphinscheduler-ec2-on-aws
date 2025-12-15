"""
DolphinScheduler service management functions
"""
import time
from src.deploy.ssh import connect_ssh, execute_remote_command
from src.utils.logger import setup_logger
from src.deploy.installer import download_hadoop_config_from_emr, setup_hadoop_config_on_node

logger = setup_logger(__name__)


def apply_hdfs_config_to_api_servers(config, username='ec2-user', key_file=None):
    """
    Apply HDFS configuration to API servers after initial startup
    
    This function:
    1. Downloads Hadoop config files from HDFS cluster master
    2. Copies them to each API server
    3. Patches common.properties with correct HDFS settings
    4. Restarts API servers
    
    Args:
        config: Configuration dictionary
        username: SSH username
        key_file: SSH key file path
    
    Returns:
        True if successful
    """
    logger.info("Configuring HDFS storage on API servers...")
    
    install_path = config['deployment']['install_path']
    deploy_user = config['deployment']['user']
    
    storage_config = config.get('storage', {})
    hdfs_config = storage_config.get('hdfs', {})
    hdfs_user = hdfs_config.get('user', 'hadoop')
    hdfs_path = hdfs_config.get('upload_path', '/dolphinscheduler')
    
    # Download Hadoop config files from HDFS cluster master
    hadoop_config_files = download_hadoop_config_from_emr(config)
    hdfs_address = None
    
    if hadoop_config_files:
        hdfs_address = hadoop_config_files.get('hdfs_address')
        logger.info(f"Got HDFS address from core-site.xml: {hdfs_address}")
    else:
        # Fallback to configured address
        namenode_host = hdfs_config.get('namenode_host', 'localhost')
        namenode_port = hdfs_config.get('namenode_port', 8020)
        hdfs_address = f"hdfs://{namenode_host}:{namenode_port}"
        logger.warning(f"Could not get HDFS address from cluster, using configured: {hdfs_address}")
    
    # Apply to each API server
    for i, node in enumerate(config['cluster']['api']['nodes']):
        host = node['host']
        logger.info(f"Configuring HDFS on API server {i+1}: {host}")
        
        ssh = connect_ssh(host, username, key_file, config=config)
        try:
            # Copy Hadoop config files if available
            if hadoop_config_files:
                try:
                    setup_hadoop_config_on_node(ssh, config, host, hadoop_config_files)
                except Exception as e:
                    logger.warning(f"Failed to copy Hadoop config to {host}: {e}")
            
            # Patch common.properties with HDFS settings
            conf_file = f"{install_path}/api-server/conf/common.properties"
            
            patch_script = f"""
            # Patch HDFS configuration
            sudo sed -i 's|resource.storage.type=.*|resource.storage.type=HDFS|g' {conf_file}
            sudo sed -i 's|resource.storage.upload.base.path=.*|resource.storage.upload.base.path={hdfs_path}|g' {conf_file}
            sudo sed -i 's|resource.hdfs.root.user=.*|resource.hdfs.root.user={hdfs_user}|g' {conf_file}
            sudo sed -i 's|resource.hdfs.fs.defaultFS=.*|resource.hdfs.fs.defaultFS={hdfs_address}|g' {conf_file}
            
            # Verify changes
            echo "=== HDFS configuration ==="
            grep -E "resource.storage.type|resource.hdfs.fs.defaultFS|resource.hdfs.root.user" {conf_file}
            """
            
            output = execute_remote_command(ssh, patch_script, timeout=30)
            logger.info(f"HDFS config on {host}:\n{output}")
            
            # Restart API server
            logger.info(f"Restarting API server on {host}...")
            restart_script = f"""
            sudo -u {deploy_user} {install_path}/bin/dolphinscheduler-daemon.sh stop api-server
            sleep 3
            sudo -u {deploy_user} {install_path}/bin/dolphinscheduler-daemon.sh start api-server
            """
            execute_remote_command(ssh, restart_script, timeout=60)
            
            logger.info(f"✓ API server {i+1} configured and restarted on {host}")
            time.sleep(5)
            
        except Exception as e:
            logger.error(f"Failed to configure HDFS on {host}: {e}")
            raise
        finally:
            ssh.close()
    
    # Clean up temp files
    if hadoop_config_files and 'temp_dir' in hadoop_config_files:
        import shutil
        try:
            shutil.rmtree(hadoop_config_files['temp_dir'])
        except Exception:
            pass
    
    logger.info("✓ HDFS configuration applied to all API servers")
    return True


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
        ssh = connect_ssh(node['host'], username, key_file, config=config)
        try:
            # For 3.2.0, use dolphinscheduler-daemon.sh from main bin directory
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
        ssh = connect_ssh(node['host'], username, key_file, config=config)
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
        ssh = connect_ssh(node['host'], username, key_file, config=config)
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
    
    # Apply HDFS configuration if configured
    storage_type = config.get('storage', {}).get('type', 'LOCAL').upper()
    if storage_type == 'HDFS':
        logger.info("Applying HDFS configuration to API servers...")
        apply_hdfs_config_to_api_servers(config, username, key_file)
    
    # Start Alert service
    logger.info("Starting Alert service...")
    alert_node = config['cluster']['alert']['nodes'][0]
    ssh = connect_ssh(alert_node['host'], username, key_file, config=config)
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
    deploy_user = config['deployment']['user']
    
    # Stop Alert
    alert_node = config['cluster']['alert']['nodes'][0]
    ssh = connect_ssh(alert_node['host'], username, key_file, config=config)
    try:
        execute_remote_command(
            ssh,
            f"cd {install_path} && sudo -u {deploy_user} bash bin/dolphinscheduler-daemon.sh stop alert-server"
        )
        logger.info(f"✓ Alert stopped")
    finally:
        ssh.close()
    
    # Stop API services
    for node in config['cluster']['api']['nodes']:
        ssh = connect_ssh(node['host'], username, key_file, config=config)
        try:
            execute_remote_command(
                ssh,
                f"cd {install_path} && sudo -u {deploy_user} bash bin/dolphinscheduler-daemon.sh stop api-server"
            )
            logger.info(f"✓ API stopped on {node['host']}")
        finally:
            ssh.close()
    
    # Stop Worker services
    for node in config['cluster']['worker']['nodes']:
        ssh = connect_ssh(node['host'], username, key_file, config=config)
        try:
            execute_remote_command(
                ssh,
                f"cd {install_path} && sudo -u {deploy_user} bash bin/dolphinscheduler-daemon.sh stop worker-server"
            )
            logger.info(f"✓ Worker stopped on {node['host']}")
        finally:
            ssh.close()
    
    # Stop Master services
    for node in config['cluster']['master']['nodes']:
        ssh = connect_ssh(node['host'], username, key_file, config=config)
        try:
            execute_remote_command(
                ssh,
                f"cd {install_path} && sudo -u {deploy_user} bash bin/dolphinscheduler-daemon.sh stop master-server"
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
        ssh = connect_ssh(node['host'], username, key_file, config=config)
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
        ssh = connect_ssh(node['host'], username, key_file, config=config)
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
        ssh = connect_ssh(node['host'], username, key_file, config=config)
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
    ssh = connect_ssh(alert_node['host'], username, key_file, config=config)
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


def restart_service(config, component, node_host, username='ec2-user', key_file=None):
    """
    Restart a specific service on a specific node
    
    Args:
        config: Configuration dictionary
        component: Component name (master, worker, api, alert)
        node_host: Node host address
        username: SSH username
        key_file: SSH key file path
    
    Returns:
        True if successful
    """
    logger.info(f"Restarting {component} service on {node_host}...")
    
    install_path = config['deployment']['install_path']
    deploy_user = config['deployment']['user']
    
    ssh = connect_ssh(node_host, username, key_file, config=config)
    try:
        # Stop service
        stop_cmd = f"cd {install_path} && sudo -u {deploy_user} bash bin/dolphinscheduler-daemon.sh stop {component}-server"
        execute_remote_command(ssh, stop_cmd)
        
        # Wait a moment
        time.sleep(5)
        
        # Start service
        start_cmd = f"cd {install_path} && sudo -u {deploy_user} bash bin/dolphinscheduler-daemon.sh start {component}-server"
        execute_remote_command(ssh, start_cmd)
        
        logger.info(f"✓ {component} service restarted on {node_host}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to restart {component} on {node_host}: {e}")
        raise
    finally:
        ssh.close()


def rolling_restart_component(config, component, username='ec2-user', key_file=None):
    """
    Perform rolling restart of a component
    
    Args:
        config: Configuration dictionary
        component: Component name (master, worker, api)
        username: SSH username
        key_file: SSH key file path
    
    Returns:
        True if successful
    """
    logger.info(f"Performing rolling restart of {component} component...")
    
    nodes = config['cluster'][component]['nodes']
    
    for i, node in enumerate(nodes):
        logger.info(f"Restarting {component} {i+1}/{len(nodes)}: {node['host']}")
        restart_service(config, component, node['host'], username, key_file)
        
        # Wait between restarts to ensure stability
        if i < len(nodes) - 1:
            logger.info("Waiting 30 seconds before next restart...")
            time.sleep(30)
    
    logger.info(f"✓ Rolling restart of {component} completed")
    return True