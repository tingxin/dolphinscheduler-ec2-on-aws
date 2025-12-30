"""
Create cluster command implementation
"""
import time
import boto3
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed
from src.aws.ec2 import create_instances_parallel, wait_for_service_ready
from src.deploy.ssh import wait_for_ssh
from src.deploy.node_initializer import (
    initialize_node,
    create_deployment_user,
    setup_ssh_keys,
    configure_hosts_file
)
from src.deploy.service_manager import start_services
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


def initialize_nodes_parallel_with_state(hosts, state, max_workers=10, config=None):
    """
    Initialize multiple nodes in parallel with state tracking
    
    Args:
        hosts: List of host addresses
        state: Deployment state
        max_workers: Maximum parallel workers
        config: Configuration dictionary
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
                    state.add_initialized_node(host)
                    completed += 1
                    pbar.update(1)
                    logger.info(f"✓ Node {completed}/{len(hosts)} initialized: {host}")
                except Exception as e:
                    logger.error(f"Initialization failed for {host}: {e}")
                    raise


def create_users_parallel_local(hosts, deploy_user, max_workers=10, config=None):
    """
    Create deployment user on multiple nodes in parallel (local version)
    
    Args:
        hosts: List of host addresses
        deploy_user: Deployment user name
        max_workers: Maximum parallel workers
        config: Configuration dictionary
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


class DeploymentState:
    """Track deployment state for rollback"""
    
    def __init__(self):
        self.created_instances = []
        self.initialized_nodes = []
    
    def add_instance(self, instance):
        self.created_instances.append(instance)
    
    def add_initialized_node(self, host):
        self.initialized_nodes.append(host)


def analyze_selector_deployment(config):
    """
    Analyze selector-based deployment requirements
    
    Args:
        config: Configuration dictionary
    
    Returns:
        Dictionary mapping selector to deployment info
    """
    selector_map = {}
    
    # Analyze each component's selector requirements
    for component in ['master', 'worker', 'api', 'alert']:
        component_config = config['cluster'][component]
        nodes = component_config.get('nodes', [])
        
        for i, node in enumerate(nodes):
            selector = node.get('selector', i + 1)  # Default to index-based if no selector
            availability_zone = node.get('availability_zone')
            
            if selector not in selector_map:
                selector_map[selector] = {
                    'components': [],
                    'availability_zone': availability_zone,
                    'instance_type': None,
                    'node_configs': {}
                }
            
            # Add component to this selector
            selector_map[selector]['components'].append(component)
            selector_map[selector]['node_configs'][component] = node
            
            # Use the largest instance type among components sharing this selector
            current_instance_type = component_config.get('instance_type', 't3.large')
            if selector_map[selector]['instance_type'] is None:
                selector_map[selector]['instance_type'] = current_instance_type
            else:
                # Simple logic: prefer larger instance types (this could be more sophisticated)
                current_size = get_instance_size_priority(current_instance_type)
                existing_size = get_instance_size_priority(selector_map[selector]['instance_type'])
                if current_size > existing_size:
                    selector_map[selector]['instance_type'] = current_instance_type
    
    return selector_map


def get_instance_size_priority(instance_type):
    """
    Get priority score for instance type (larger = higher priority)
    
    Args:
        instance_type: EC2 instance type
    
    Returns:
        Priority score
    """
    size_map = {
        'nano': 1, 'micro': 2, 'small': 3, 'medium': 4, 'large': 5,
        'xlarge': 6, '2xlarge': 7, '4xlarge': 8, '8xlarge': 9, '16xlarge': 10
    }
    
    for size, priority in size_map.items():
        if size in instance_type:
            return priority
    
    return 5  # Default to 'large' priority


def create_selector_instance(config, selector, selector_info, subnet_id, availability_zone):
    """
    Create EC2 instance for a selector
    
    Args:
        config: Configuration dictionary
        selector: Selector value
        selector_info: Selector deployment info
        subnet_id: Subnet ID
        availability_zone: Availability zone
    
    Returns:
        Instance object
    """
    from src.aws.ec2 import create_ec2_instance
    
    # Use the primary component for instance creation parameters
    primary_component = selector_info['components'][0]
    instance_type = selector_info['instance_type']
    
    # Create a temporary config for this selector
    selector_config = config.copy()
    selector_config['cluster'] = {primary_component: {'instance_type': instance_type}}
    
    # Create instance with selector-based naming
    ec2 = boto3.resource('ec2', region_name=config['aws']['region'])
    
    # Get AMI
    from src.aws.ec2 import get_ami_id
    ami_id = get_ami_id(config['aws']['region'])
    
    # Get security group (use the first component's security group)
    security_group = config['aws']['security_groups'][primary_component]
    key_name = config['aws']['key_name']
    iam_profile = config['aws'].get('iam_instance_profile')
    
    # Get volume configuration from primary component
    ec2_advanced = config.get('ec2_advanced', {}).get(primary_component, {})
    volume_size = ec2_advanced.get('root_volume_size', 50)
    volume_type = ec2_advanced.get('root_volume_type', 'gp3')
    
    # Tags for selector-based instance
    project_name = config.get('project', {}).get('name', 'dolphinscheduler')
    components_str = '-'.join(selector_info['components'])
    
    base_tags = {
        'Name': f"ds-selector-{selector}",
        'Selector': str(selector),
        'Components': components_str,
        'ManagedBy': 'dolphinscheduler-cli',
        'Project': project_name
    }
    
    # Convert to AWS tag format
    resource_tags = [{'Key': k, 'Value': v} for k, v in base_tags.items()]
    tag_specifications = [
        {'ResourceType': 'instance', 'Tags': resource_tags},
        {'ResourceType': 'volume', 'Tags': resource_tags}
    ]
    
    # Create instance
    logger.info(f"Creating selector {selector} instance ({components_str}) in {availability_zone}...")
    
    create_params = {
        'ImageId': ami_id,
        'InstanceType': instance_type,
        'MinCount': 1,
        'MaxCount': 1,
        'KeyName': key_name,
        'SecurityGroupIds': [security_group],
        'SubnetId': subnet_id,
        'TagSpecifications': tag_specifications,
        'BlockDeviceMappings': [{
            'DeviceName': '/dev/xvda',
            'Ebs': {
                'VolumeSize': volume_size,
                'VolumeType': volume_type,
                'DeleteOnTermination': True
            }
        }]
    }
    
    if iam_profile:
        create_params['IamInstanceProfile'] = {'Name': iam_profile}
    
    instances = ec2.create_instances(**create_params)
    instance = instances[0]
    
    # Wait for instance to be running
    logger.info(f"Waiting for selector {selector} instance {instance.id} to start...")
    instance.wait_until_running()
    instance.reload()
    
    logger.info(f"✓ Selector {selector} instance created: {instance.id} ({instance.private_ip_address})")
    
    return instance


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
    Create DolphinScheduler cluster with selector-based deployment
    
    Args:
        config: Configuration dictionary
    
    Returns:
        Deployment result
    """
    state = DeploymentState()
    subnets = config['aws']['subnets']
    
    try:
        # Step 1: Create EC2 instances (selector-based)
        logger.info("=" * 70)
        logger.info("Step 1: Creating EC2 Instances (Selector-based)")
        logger.info("=" * 70)
        
        # Analyze selector-based deployment requirements
        selector_map = analyze_selector_deployment(config)
        logger.info(f"\nSelector deployment plan:")
        for selector, info in selector_map.items():
            components = ', '.join(info['components'])
            logger.info(f"  Selector {selector}: {components} -> {info['availability_zone']}")
        
        # Create instances based on selectors
        all_instances = {}
        selector_instances = {}
        
        logger.info(f"\nCreating {len(selector_map)} EC2 instances for selectors...")
        
        for selector, info in selector_map.items():
            # Find subnet for the availability zone
            target_subnet = None
            for subnet in subnets:
                if subnet['availability_zone'] == info['availability_zone']:
                    target_subnet = subnet
                    break
            
            if not target_subnet:
                raise Exception(f"No subnet found for availability zone: {info['availability_zone']}")
            
            # Create instance for this selector (use the primary component for naming)
            primary_component = info['components'][0]
            logger.info(f"Creating instance for selector {selector} ({', '.join(info['components'])})...")
            
            instance = create_selector_instance(
                config, selector, info, target_subnet['subnet_id'], info['availability_zone']
            )
            
            selector_instances[selector] = instance
            state.add_instance(instance)
            
            # Map instance to all components that use this selector
            for component in info['components']:
                if component not in all_instances:
                    all_instances[component] = []
                all_instances[component].append(instance)
            
            logger.info(f"✓ Created instance {instance.id} for selector {selector}")
        
        logger.info(f"✓ Created {len(selector_instances)} instances for {len(selector_map)} selectors")
        
        # Update config with actual instance information
        update_config_with_selector_instances(config, selector_instances, selector_map)
        
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
        initialize_nodes_parallel_with_state(all_hosts, state, max_workers=max_workers, config=config)
        
        logger.info("\nCreating deployment user...")
        deploy_user = config['deployment']['user']
        create_users_parallel_local(all_hosts, deploy_user, max_workers=max_workers, config=config)
        
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
        setup_ssh_keys(all_nodes, config=config)
        
        logger.info("\nConfiguring /etc/hosts...")
        configure_hosts_file(all_nodes, config=config)
        
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
            from src.deploy.installer import deploy_dolphinscheduler_v320
            deploy_dolphinscheduler_v320(config, package_file=None)
        else:
            logger.info(f"\nDownloading DolphinScheduler {version} on local machine...")
            download_url = config.get('advanced', {}).get('download_url')
            from src.deploy.package_manager import download_dolphinscheduler
            package_file = download_dolphinscheduler(version, download_url=download_url)
            logger.info("\nDeploying to cluster...")
            from src.deploy.installer import deploy_dolphinscheduler_v320
            deploy_dolphinscheduler_v320(config, package_file=package_file)
        
        logger.info("✓ DolphinScheduler deployed")
        
        # Step 6: Start services
        logger.info("\n" + "=" * 70)
        logger.info("Step 6: Starting Services")
        logger.info("=" * 70)
        
        start_services(config)
        
        # Verify services
        logger.info("\nVerifying services...")
        
        # Get service ports from config
        master_port = config.get('service_config', {}).get('master', {}).get('listen_port', 5679)
        worker_port = config.get('service_config', {}).get('worker', {}).get('listen_port', 1235)
        api_port = config.get('service_config', {}).get('api', {}).get('port', 12345)
        
        # Check Master services
        logger.info("Verifying Master services...")
        for i, node in enumerate(config['cluster']['master']['nodes']):
            if wait_for_service_ready(node['host'], master_port, max_retries=15, retry_interval=10):
                logger.info(f"✓ Master {i+1} service ready on {node['host']}:{master_port}")
            else:
                logger.warning(f"⚠ Master {i+1} service not responding on {node['host']}:{master_port}")
        
        # Check Worker services
        logger.info("Verifying Worker services...")
        for i, node in enumerate(config['cluster']['worker']['nodes']):
            if wait_for_service_ready(node['host'], worker_port, max_retries=15, retry_interval=10):
                logger.info(f"✓ Worker {i+1} service ready on {node['host']}:{worker_port}")
            else:
                logger.warning(f"⚠ Worker {i+1} service not responding on {node['host']}:{worker_port}")
        
        # Check API services
        logger.info("Verifying API services...")
        for i, node in enumerate(config['cluster']['api']['nodes']):
            if wait_for_service_ready(node['host'], api_port, max_retries=15, retry_interval=10):
                logger.info(f"✓ API {i+1} service ready on {node['host']}:{api_port}")
            else:
                logger.warning(f"⚠ API {i+1} service not responding on {node['host']}:{api_port}")
        
        # Final health check
        logger.info("Performing final health check...")
        time.sleep(30)  # Wait for services to fully initialize
        
        # Try to access API health endpoint
        try:
            import requests
            api_node = config['cluster']['api']['nodes'][0]
            health_url = f"http://{api_node['host']}:{api_port}/dolphinscheduler/actuator/health"
            response = requests.get(health_url, timeout=10)
            if response.status_code == 200:
                logger.info("✓ API health check passed")
            else:
                logger.warning(f"⚠ API health check returned status {response.status_code}")
        except Exception as e:
            logger.warning(f"⚠ Could not perform API health check: {e}")
        
        logger.info("✓ Service verification completed")
        
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


def update_config_with_selector_instances(config, selector_instances, selector_map):
    """
    Update configuration with actual instance information for selector-based deployment
    
    Args:
        config: Configuration dictionary
        selector_instances: Dictionary of instances by selector
        selector_map: Selector deployment mapping
    """
    # Clear existing nodes
    for component in ['master', 'worker', 'api', 'alert']:
        config['cluster'][component]['nodes'] = []
    
    # Update nodes based on selector mapping
    for selector, instance in selector_instances.items():
        selector_info = selector_map[selector]
        
        for component in selector_info['components']:
            # Find the original node config for this component and selector
            original_node = None
            for node in config['cluster'][component].get('nodes', []):
                if node.get('selector') == selector:
                    original_node = node
                    break
            
            if not original_node:
                # Create a default node config if not found
                original_node = {'selector': selector}
            
            node_info = {
                'host': instance.private_ip_address,
                'ssh_port': 22,
                'instance_id': instance.id,
                'subnet_id': instance.subnet_id,
                'availability_zone': instance.placement['AvailabilityZone'],
                'selector': selector
            }
            
            # Preserve original node configuration
            if 'groups' in original_node:
                node_info['groups'] = original_node['groups']
            elif component == 'worker':
                node_info['groups'] = ['default']
            
            config['cluster'][component]['nodes'].append(node_info)


def update_config_with_instances(config, instances):
    """
    Update configuration with actual instance information (legacy function)
    
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
    rollback_errors = []
    
    try:
        # Stop any running services first
        if state.initialized_nodes:
            logger.info("Stopping services on initialized nodes...")
            try:
                from src.deploy.installer import stop_services
                stop_services(config)
            except Exception as e:
                rollback_errors.append(f"Failed to stop services: {e}")
                logger.warning(f"Could not stop services during rollback: {e}")
    except Exception as e:
        rollback_errors.append(f"Service cleanup error: {e}")
    
    try:
        # Terminate created instances
        if state.created_instances:
            instance_ids = [inst.id for inst in state.created_instances]
            logger.info(f"Terminating {len(instance_ids)} instances...")
            terminate_instances(config, instance_ids)
            
            # Wait a bit for termination to start
            import time
            time.sleep(10)
    except Exception as e:
        rollback_errors.append(f"Failed to terminate instances: {e}")
        logger.error(f"Instance termination failed: {e}")
    
    try:
        # Clean up any ALB resources if they were created
        # (This would need to be implemented based on your ALB creation logic)
        logger.info("Checking for ALB resources to clean up...")
        # TODO: Add ALB cleanup logic here
    except Exception as e:
        rollback_errors.append(f"ALB cleanup error: {e}")
    
    if rollback_errors:
        logger.warning("Rollback completed with some errors:")
        for error in rollback_errors:
            logger.warning(f"  - {error}")
        logger.warning("You may need to manually clean up some resources in AWS console")
    else:
        logger.info("✓ Rollback completed successfully")
    
    # Clear the config nodes to prevent confusion
    try:
        for component in ['master', 'worker', 'api', 'alert']:
            config['cluster'][component]['nodes'] = []
    except Exception as e:
        logger.warning(f"Could not clear config nodes: {e}")
