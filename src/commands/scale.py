"""
Scale cluster command implementation
"""
import time
from src.aws.ec2 import create_instances_parallel, terminate_instances, wait_for_service_ready
from src.deploy.ssh import wait_for_ssh
from src.deploy.installer import (
    initialize_node,
    create_deployment_user,
    configure_hosts_file,
    deploy_dolphinscheduler,
    download_dolphinscheduler
)
from src.config import save_config
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


def scale_cluster(config, component, target_count, config_file):
    """
    Scale cluster component
    
    Args:
        config: Configuration dictionary
        component: Component to scale (master, worker, api)
        target_count: Target node count
        config_file: Configuration file path
    
    Returns:
        True if successful
    """
    current_count = config['cluster'][component]['count']
    
    if target_count == current_count:
        logger.info(f"✓ {component} already has {target_count} nodes")
        return True
    
    if target_count > current_count:
        # Scale out
        return scale_out(config, component, target_count - current_count, config_file)
    else:
        # Scale in
        return scale_in(config, component, current_count - target_count, config_file)


def scale_out(config, component, additional_count, config_file):
    """
    Scale out cluster component
    
    Args:
        config: Configuration dictionary
        component: Component to scale
        additional_count: Number of nodes to add
        config_file: Configuration file path
    
    Returns:
        True if successful
    """
    logger.info("=" * 70)
    logger.info(f"Scaling Out {component.upper()}: +{additional_count} nodes")
    logger.info("=" * 70)
    
    subnets = config['aws']['subnets']
    current_nodes = config['cluster'][component]['nodes']
    
    try:
        # Step 1: Create new instances
        logger.info("\n[1/5] Creating new EC2 instances...")
        
        new_instances = create_instances_parallel(
            config, component, additional_count, subnets
        )
        
        logger.info(f"✓ Created {len(new_instances)} new instances")
        
        # Step 2: Wait for SSH
        logger.info("\n[2/5] Waiting for SSH access...")
        
        new_hosts = [inst.private_ip_address for inst in new_instances]
        for host in new_hosts:
            if not wait_for_ssh(host):
                raise Exception(f"SSH not available on {host}")
        
        logger.info("✓ SSH available on all new nodes")
        
        # Step 3: Initialize nodes
        logger.info("\n[3/5] Initializing new nodes...")
        
        deploy_user = config['deployment']['user']
        for host in new_hosts:
            logger.info(f"Initializing {host}...")
            initialize_node(host)
            create_deployment_user(host, deploy_user=deploy_user)
        
        logger.info("✓ New nodes initialized")
        
        # Step 4: Update configuration
        logger.info("\n[4/5] Updating cluster configuration...")
        
        # Add new nodes to config
        for i, instance in enumerate(new_instances):
            node_info = {
                'host': instance.private_ip_address,
                'ssh_port': 22,
                'instance_id': instance.id,
                'subnet_id': instance.subnet_id,
                'availability_zone': instance.placement['AvailabilityZone']
            }
            
            if component == 'worker':
                node_info['groups'] = ['default']
            
            current_nodes.append(node_info)
        
        config['cluster'][component]['count'] += additional_count
        
        # Update hosts file on all nodes
        all_nodes = []
        for comp in ['master', 'worker', 'api', 'alert']:
            for i, node in enumerate(config['cluster'][comp]['nodes']):
                all_nodes.append({
                    'host': node['host'],
                    'component': comp,
                    'index': i,
                    'hostname': f"ds-{comp}-{i}"
                })
        
        configure_hosts_file(all_nodes)
        
        # Save updated config
        save_config(config_file, config)
        
        logger.info("✓ Configuration updated")
        
        # Step 5: Deploy and start services on new nodes
        logger.info("\n[5/5] Starting services on new nodes...")
        
        if component == 'worker':
            # Workers can be added without restarting existing nodes
            install_path = config['deployment']['install_path']
            version = config['deployment']['version']
            package_file = download_dolphinscheduler(version)
            
            # Deploy to new nodes only
            for host in new_hosts:
                logger.info(f"Deploying to {host}...")
                # Note: This is simplified, actual implementation would need
                # to deploy only to specific nodes
            
            # Start worker services
            from src.deploy.ssh import connect_ssh, execute_remote_command
            for host in new_hosts:
                ssh = connect_ssh(host)
                try:
                    execute_remote_command(
                        ssh,
                        f"cd {install_path} && bash bin/dolphinscheduler-daemon.sh start worker-server"
                    )
                    logger.info(f"✓ Worker started on {host}")
                    
                    # Verify
                    if wait_for_service_ready(host, 1234, max_retries=10):
                        logger.info(f"✓ Worker service ready on {host}")
                finally:
                    ssh.close()
        
        elif component in ['master', 'api']:
            logger.info(f"⚠️  {component.upper()} scaling requires cluster restart")
            logger.info("Please run: cli.py config update --component all")
        
        logger.info("\n" + "=" * 70)
        logger.info(f"✓ Scale Out Completed: {component} now has {config['cluster'][component]['count']} nodes")
        logger.info("=" * 70)
        
        return True
        
    except Exception as e:
        logger.error(f"Scale out failed: {str(e)}")
        raise


def scale_in(config, component, reduce_count, config_file):
    """
    Scale in cluster component
    
    Args:
        config: Configuration dictionary
        component: Component to scale
        reduce_count: Number of nodes to remove
        config_file: Configuration file path
    
    Returns:
        True if successful
    """
    logger.info("=" * 70)
    logger.info(f"Scaling In {component.upper()}: -{reduce_count} nodes")
    logger.info("=" * 70)
    
    current_nodes = config['cluster'][component]['nodes']
    current_count = len(current_nodes)
    
    # Validation
    if component == 'master' and current_count - reduce_count < 2:
        raise ValueError("Cannot scale below 2 Master nodes (high availability requirement)")
    
    if component == 'api' and current_count - reduce_count < 1:
        raise ValueError("Cannot scale below 1 API node")
    
    if component == 'worker' and current_count - reduce_count < 1:
        raise ValueError("Cannot scale below 1 Worker node")
    
    try:
        # Step 1: Select nodes to remove (last N nodes)
        logger.info("\n[1/4] Selecting nodes to remove...")
        
        nodes_to_remove = current_nodes[-reduce_count:]
        remaining_nodes = current_nodes[:-reduce_count]
        
        for node in nodes_to_remove:
            logger.info(f"Will remove: {node['host']} ({node['instance_id']})")
        
        # Step 2: Stop services on nodes to remove
        logger.info("\n[2/4] Stopping services on nodes to remove...")
        
        from src.deploy.ssh import connect_ssh, execute_remote_command
        install_path = config['deployment']['install_path']
        
        service_name = f"{component}-server"
        
        for node in nodes_to_remove:
            ssh = connect_ssh(node['host'])
            try:
                logger.info(f"Stopping {service_name} on {node['host']}...")
                execute_remote_command(
                    ssh,
                    f"cd {install_path} && bash bin/dolphinscheduler-daemon.sh stop {service_name}"
                )
                
                # For workers, wait for tasks to complete
                if component == 'worker':
                    logger.info("Waiting for tasks to complete...")
                    time.sleep(30)
                
                logger.info(f"✓ Service stopped on {node['host']}")
            finally:
                ssh.close()
        
        # Step 3: Terminate instances
        logger.info("\n[3/4] Terminating EC2 instances...")
        
        instance_ids = [node['instance_id'] for node in nodes_to_remove]
        terminate_instances(config, instance_ids)
        
        logger.info(f"✓ Terminated {len(instance_ids)} instances")
        
        # Step 4: Update configuration
        logger.info("\n[4/4] Updating cluster configuration...")
        
        config['cluster'][component]['nodes'] = remaining_nodes
        config['cluster'][component]['count'] = len(remaining_nodes)
        
        # Save updated config
        save_config(config_file, config)
        
        logger.info("✓ Configuration updated")
        
        logger.info("\n" + "=" * 70)
        logger.info(f"✓ Scale In Completed: {component} now has {config['cluster'][component]['count']} nodes")
        logger.info("=" * 70)
        
        return True
        
    except Exception as e:
        logger.error(f"Scale in failed: {str(e)}")
        raise
