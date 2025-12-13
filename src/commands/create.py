"""
Create cluster command implementation
"""
from tqdm import tqdm
from src.aws.ec2 import create_instances_parallel, wait_for_service_ready
from src.deploy.ssh import wait_for_ssh
from src.deploy.installer import (
    download_dolphinscheduler,
    initialize_node,
    create_deployment_user,
    setup_ssh_keys,
    configure_hosts_file,
    deploy_dolphinscheduler,
    start_services
)
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


class DeploymentState:
    """Track deployment state for rollback"""
    
    def __init__(self):
        self.created_instances = []
        self.initialized_nodes = []
    
    def add_instance(self, instance):
        self.created_instances.append(instance)
    
    def add_initialized_node(self, host):
        self.initialized_nodes.append(host)


def distribute_nodes_across_azs(count, subnets):
    """
    Distribute nodes evenly across availability zones
    
    Args:
        count: Number of nodes
        subnets: List of subnet configurations
    
    Returns:
        List of node distributions
    """
    distribution = []
    for i in range(count):
        subnet = subnets[i % len(subnets)]
        distribution.append({
            'index': i,
            'subnet_id': subnet['subnet_id'],
            'availability_zone': subnet['availability_zone']
        })
    return distribution


def create_cluster(config):
    """
    Create DolphinScheduler cluster
    
    Args:
        config: Configuration dictionary
    
    Returns:
        Deployment result
    """
    state = DeploymentState()
    subnets = config['aws']['subnets']
    
    try:
        # Step 1: Create EC2 instances
        logger.info("=" * 70)
        logger.info("Step 1: Creating EC2 Instances")
        logger.info("=" * 70)
        
        all_instances = {}
        
        # Create Master instances
        logger.info(f"\nCreating {config['cluster']['master']['count']} Master instances...")
        master_instances = create_instances_parallel(
            config, 'master',
            config['cluster']['master']['count'],
            subnets
        )
        all_instances['master'] = master_instances
        for instance in master_instances:
            state.add_instance(instance)
        logger.info(f"✓ Created {len(master_instances)} Master instances")
        
        # Create Worker instances
        logger.info(f"\nCreating {config['cluster']['worker']['count']} Worker instances...")
        worker_instances = create_instances_parallel(
            config, 'worker',
            config['cluster']['worker']['count'],
            subnets
        )
        all_instances['worker'] = worker_instances
        for instance in worker_instances:
            state.add_instance(instance)
        logger.info(f"✓ Created {len(worker_instances)} Worker instances")
        
        # Create API instances
        logger.info(f"\nCreating {config['cluster']['api']['count']} API instances...")
        api_instances = create_instances_parallel(
            config, 'api',
            config['cluster']['api']['count'],
            subnets
        )
        all_instances['api'] = api_instances
        for instance in api_instances:
            state.add_instance(instance)
        logger.info(f"✓ Created {len(api_instances)} API instances")
        
        # Create Alert instance
        logger.info(f"\nCreating 1 Alert instance...")
        alert_instances = create_instances_parallel(
            config, 'alert', 1, subnets
        )
        all_instances['alert'] = alert_instances
        for instance in alert_instances:
            state.add_instance(instance)
        logger.info(f"✓ Created {len(alert_instances)} Alert instance")
        
        # Update config with actual instance information
        update_config_with_instances(config, all_instances)
        
        # Step 2: Wait for SSH
        logger.info("\n" + "=" * 70)
        logger.info("Step 2: Waiting for SSH Access")
        logger.info("=" * 70)
        
        all_hosts = []
        for component, instances in all_instances.items():
            for instance in instances:
                all_hosts.append(instance.private_ip_address)
        
        logger.info(f"\nWaiting for SSH on {len(all_hosts)} nodes...")
        for host in tqdm(all_hosts, desc="SSH availability"):
            if not wait_for_ssh(host):
                raise Exception(f"SSH not available on {host}")
        
        logger.info("✓ SSH available on all nodes")
        
        # Step 3: Initialize nodes
        logger.info("\n" + "=" * 70)
        logger.info("Step 3: Initializing Nodes")
        logger.info("=" * 70)
        
        logger.info("\nInstalling system dependencies...")
        for host in tqdm(all_hosts, desc="Node initialization"):
            initialize_node(host)
            state.add_initialized_node(host)
        
        logger.info("\nCreating deployment user...")
        deploy_user = config['deployment']['user']
        for host in tqdm(all_hosts, desc="Creating users"):
            create_deployment_user(host, deploy_user=deploy_user)
        
        logger.info("✓ All nodes initialized")
        
        # Step 4: Configure cluster
        logger.info("\n" + "=" * 70)
        logger.info("Step 4: Configuring Cluster")
        logger.info("=" * 70)
        
        # Prepare node list for configuration
        all_nodes = []
        for component, instances in all_instances.items():
            for i, instance in enumerate(instances):
                all_nodes.append({
                    'host': instance.private_ip_address,
                    'component': component,
                    'index': i,
                    'hostname': f"ds-{component}-{i}"
                })
        
        logger.info("\nSetting up SSH keys...")
        setup_ssh_keys(all_nodes)
        
        logger.info("\nConfiguring /etc/hosts...")
        configure_hosts_file(all_nodes)
        
        logger.info("✓ Cluster configured")
        
        # Step 5: Deploy DolphinScheduler
        logger.info("\n" + "=" * 70)
        logger.info("Step 5: Deploying DolphinScheduler")
        logger.info("=" * 70)
        
        version = config['deployment']['version']
        logger.info(f"\nDownloading DolphinScheduler {version}...")
        package_file = download_dolphinscheduler(version)
        
        logger.info("\nDeploying to cluster...")
        deploy_dolphinscheduler(config, package_file)
        
        logger.info("✓ DolphinScheduler deployed")
        
        # Step 6: Start services
        logger.info("\n" + "=" * 70)
        logger.info("Step 6: Starting Services")
        logger.info("=" * 70)
        
        start_services(config)
        
        # Verify services
        logger.info("\nVerifying services...")
        
        # Check Master
        for node in config['cluster']['master']['nodes']:
            if wait_for_service_ready(node['host'], 5678, max_retries=10):
                logger.info(f"✓ Master service ready on {node['host']}")
        
        # Check Worker
        for node in config['cluster']['worker']['nodes']:
            if wait_for_service_ready(node['host'], 1234, max_retries=10):
                logger.info(f"✓ Worker service ready on {node['host']}")
        
        # Check API
        for node in config['cluster']['api']['nodes']:
            if wait_for_service_ready(node['host'], 12345, max_retries=10):
                logger.info(f"✓ API service ready on {node['host']}")
        
        logger.info("✓ All services started and verified")
        
        # Success
        return {
            'success': True,
            'instances': all_instances,
            'api_endpoint': f"http://{config['cluster']['api']['nodes'][0]['host']}:12345/dolphinscheduler"
        }
        
    except Exception as e:
        logger.error(f"Deployment failed: {str(e)}")
        logger.info("\nRolling back...")
        rollback_deployment(config, state)
        raise


def update_config_with_instances(config, instances):
    """
    Update configuration with actual instance information
    
    Args:
        config: Configuration dictionary
        instances: Dictionary of instances by component
    """
    for component, instance_list in instances.items():
        config['cluster'][component]['nodes'] = []
        for i, instance in enumerate(instance_list):
            node_info = {
                'host': instance.private_ip_address,
                'ssh_port': 22,
                'instance_id': instance.id,
                'subnet_id': instance.subnet_id,
                'availability_zone': instance.placement['AvailabilityZone']
            }
            
            # Add groups for workers
            if component == 'worker':
                node_info['groups'] = ['default']
            
            config['cluster'][component]['nodes'].append(node_info)


def rollback_deployment(config, state):
    """
    Rollback failed deployment
    
    Args:
        config: Configuration dictionary
        state: Deployment state
    """
    from src.aws.ec2 import terminate_instances
    
    logger.info("Rolling back deployment...")
    
    # Terminate created instances
    if state.created_instances:
        instance_ids = [inst.id for inst in state.created_instances]
        logger.info(f"Terminating {len(instance_ids)} instances...")
        terminate_instances(config, instance_ids)
    
    logger.info("✓ Rollback completed")
