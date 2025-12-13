"""
Create cluster command implementation
"""
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed
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


def wait_for_ssh_parallel(hosts, max_workers=10):
    """
    Wait for SSH on multiple hosts in parallel
    
    Args:
        hosts: List of host addresses
        max_workers: Maximum parallel workers
    """
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(wait_for_ssh, host): host for host in hosts}
        
        with tqdm(total=len(hosts), desc="SSH availability") as pbar:
            for future in as_completed(futures):
                host = futures[future]
                try:
                    if not future.result():
                        raise Exception(f"SSH not available on {host}")
                    pbar.update(1)
                except Exception as e:
                    logger.error(f"SSH failed for {host}: {e}")
                    raise


def initialize_nodes_parallel(hosts, state, max_workers=10):
    """
    Initialize multiple nodes in parallel
    
    Args:
        hosts: List of host addresses
        state: Deployment state
        max_workers: Maximum parallel workers
    """
    logger.info(f"Initializing {len(hosts)} nodes in parallel (max {max_workers} concurrent)...")
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(initialize_node, host): host for host in hosts}
        
        completed = 0
        with tqdm(total=len(hosts), desc="Node initialization", unit="node") as pbar:
            for future in as_completed(futures):
                host = futures[future]
                try:
                    future.result()
                    state.add_initialized_node(host)
                    completed += 1
                    pbar.update(1)
                    logger.info(f"✓ Node {completed}/{len(hosts)} initialized: {host}")
                except Exception as e:
                    logger.error(f"Initialization failed for {host}: {e}")
                    raise


def create_users_parallel(hosts, deploy_user, max_workers=10):
    """
    Create deployment user on multiple nodes in parallel
    
    Args:
        hosts: List of host addresses
        deploy_user: Deployment user name
        max_workers: Maximum parallel workers
    """
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(create_deployment_user, host, deploy_user=deploy_user): host for host in hosts}
        
        with tqdm(total=len(hosts), desc="Creating users") as pbar:
            for future in as_completed(futures):
                host = futures[future]
                try:
                    future.result()
                    pbar.update(1)
                except Exception as e:
                    logger.error(f"User creation failed for {host}: {e}")
                    raise


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
        wait_for_ssh_parallel(all_hosts)
        
        logger.info("✓ SSH available on all nodes")
        
        # Step 3: Initialize nodes
        logger.info("\n" + "=" * 70)
        logger.info("Step 3: Initializing Nodes")
        logger.info("=" * 70)
        
        logger.info("\nInstalling system dependencies...")
        max_workers = config.get('deployment', {}).get('parallel_init_workers', 10)
        initialize_nodes_parallel(all_hosts, state, max_workers=max_workers)
        
        logger.info("\nCreating deployment user...")
        deploy_user = config['deployment']['user']
        create_users_parallel(all_hosts, deploy_user)
        
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
        
        # Check if we should download on remote or local
        download_on_remote = config.get('deployment', {}).get('download_on_remote', True)
        
        if download_on_remote:
            logger.info(f"\nDolphinScheduler {version} will be downloaded directly on target node...")
            logger.info("\nDeploying to cluster...")
            deploy_dolphinscheduler(config, package_file=None)
        else:
            logger.info(f"\nDownloading DolphinScheduler {version} on local machine...")
            download_url = config.get('advanced', {}).get('download_url')
            package_file = download_dolphinscheduler(version, download_url=download_url)
            logger.info("\nDeploying to cluster...")
            deploy_dolphinscheduler(config, package_file=package_file)
        
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
