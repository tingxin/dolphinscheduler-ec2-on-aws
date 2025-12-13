"""
EC2 instance management
"""
import boto3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


def get_ami_id(region, os_type='al2023'):
    """
    Get latest Amazon Linux 2023 AMI ID
    
    Args:
        region: AWS region
        os_type: OS type (al2023, ubuntu, etc.)
    
    Returns:
        AMI ID
    """
    ec2 = boto3.client('ec2', region_name=region)
    
    if os_type == 'al2023':
        filters = [
            {'Name': 'name', 'Values': ['al2023-ami-*-x86_64']},
            {'Name': 'state', 'Values': ['available']},
            {'Name': 'architecture', 'Values': ['x86_64']}
        ]
        owner = 'amazon'
    else:
        raise ValueError(f"Unsupported OS type: {os_type}")
    
    response = ec2.describe_images(
        Owners=[owner],
        Filters=filters
    )
    
    if not response['Images']:
        raise ValueError(f"No AMI found for {os_type}")
    
    # Sort by creation date and get latest
    images = sorted(response['Images'], key=lambda x: x['CreationDate'], reverse=True)
    ami_id = images[0]['ImageId']
    
    logger.info(f"Selected AMI: {ami_id} ({images[0]['Name']})")
    return ami_id


def create_ec2_instance(config, component, index, subnet_id, availability_zone):
    """
    Create EC2 instance
    
    Args:
        config: Configuration dictionary
        component: Component type (master, worker, api, alert)
        index: Instance index
        subnet_id: Subnet ID
        availability_zone: Availability zone
    
    Returns:
        Instance object
    """
    ec2 = boto3.resource('ec2', region_name=config['aws']['region'])
    
    # Get AMI
    ami_id = get_ami_id(config['aws']['region'])
    
    # Get instance configuration
    cluster_config = config['cluster'][component]
    instance_type = cluster_config['instance_type']
    security_group = config['aws']['security_groups'][component]
    key_name = config['aws']['key_name']
    iam_profile = config['aws'].get('iam_instance_profile')
    
    # Get volume configuration
    ec2_advanced = config.get('ec2_advanced', {}).get(component, {})
    volume_size = ec2_advanced.get('root_volume_size', 50)
    volume_type = ec2_advanced.get('root_volume_type', 'gp3')
    
    # Tags
    custom_tags = ec2_advanced.get('tags', {})
    tag_name = f"ds-{component}-{index}"
    
    # Build base tags
    base_tags = {
        'Name': tag_name,
        'Component': component,
        'Index': str(index),
        'ManagedBy': 'dolphinscheduler-cli'
    }
    
    # Merge with custom tags (custom tags won't override base tags)
    all_tags = {**custom_tags, **base_tags}
    
    # Convert to AWS tag format for both instance and volume
    resource_tags = [{'Key': k, 'Value': v} for k, v in all_tags.items()]
    
    tag_specifications = [
        {
            'ResourceType': 'instance',
            'Tags': resource_tags
        },
        {
            'ResourceType': 'volume',
            'Tags': resource_tags
        }
    ]
    
    # Create instance
    logger.info(f"Creating {component} instance {index} in {availability_zone}...")
    
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
    logger.info(f"Waiting for instance {instance.id} to start...")
    instance.wait_until_running()
    instance.reload()
    
    logger.info(f"✓ Instance created: {instance.id} ({instance.private_ip_address})")
    
    return instance


def create_ec2_instance_idempotent(config, component, index, subnet_id, availability_zone):
    """
    Create EC2 instance (idempotent)
    
    Args:
        config: Configuration dictionary
        component: Component type
        index: Instance index
        subnet_id: Subnet ID
        availability_zone: Availability zone
    
    Returns:
        Instance object
    """
    ec2 = boto3.resource('ec2', region_name=config['aws']['region'])
    
    # Check if instance already exists
    tag_name = f"ds-{component}-{index}"
    existing_instances = list(ec2.instances.filter(
        Filters=[
            {'Name': 'tag:Name', 'Values': [tag_name]},
            {'Name': 'instance-state-name', 'Values': ['running', 'pending']}
        ]
    ))
    
    if existing_instances:
        instance = existing_instances[0]
        logger.info(f"Instance already exists: {tag_name} ({instance.id})")
        return instance
    
    # Create new instance
    return create_ec2_instance(config, component, index, subnet_id, availability_zone)


def create_instances_parallel(config, component, count, subnets):
    """
    Create multiple instances in parallel
    
    Args:
        config: Configuration dictionary
        component: Component type
        count: Number of instances
        subnets: List of subnet configurations
    
    Returns:
        List of instance objects
    """
    instances = []
    
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = []
        
        for i in range(count):
            # Distribute across subnets
            subnet = subnets[i % len(subnets)]
            
            future = executor.submit(
                create_ec2_instance_idempotent,
                config, component, i,
                subnet['subnet_id'],
                subnet['availability_zone']
            )
            futures.append(future)
        
        for future in as_completed(futures):
            try:
                instance = future.result()
                instances.append(instance)
            except Exception as e:
                logger.error(f"Failed to create instance: {e}")
                raise
    
    return instances


def terminate_instances(config, instance_ids):
    """
    Terminate EC2 instances
    
    Args:
        config: Configuration dictionary
        instance_ids: List of instance IDs
    
    Returns:
        True if successful
    """
    if not instance_ids:
        return True
    
    ec2 = boto3.client('ec2', region_name=config['aws']['region'])
    
    logger.info(f"Terminating {len(instance_ids)} instances...")
    ec2.terminate_instances(InstanceIds=instance_ids)
    
    logger.info("✓ Instances terminated")
    return True


def get_instance_by_tag(config, tag_name):
    """
    Get instance by tag name
    
    Args:
        config: Configuration dictionary
        tag_name: Tag name value
    
    Returns:
        Instance object or None
    """
    ec2 = boto3.resource('ec2', region_name=config['aws']['region'])
    
    instances = list(ec2.instances.filter(
        Filters=[
            {'Name': 'tag:Name', 'Values': [tag_name]},
            {'Name': 'instance-state-name', 'Values': ['running']}
        ]
    ))
    
    return instances[0] if instances else None


def is_service_running(host, port, timeout=5):
    """
    Check if service is running on host:port
    
    Args:
        host: Host address
        port: Port number
        timeout: Connection timeout
    
    Returns:
        True if service is running
    """
    import socket
    
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except:
        return False


def wait_for_service_ready(host, port, max_retries=30, retry_interval=10):
    """
    Wait for service to be ready
    
    Args:
        host: Host address
        port: Port number
        max_retries: Maximum retry attempts
        retry_interval: Interval between retries (seconds)
    
    Returns:
        True if service is ready
    """
    for i in range(max_retries):
        if is_service_running(host, port):
            logger.info(f"✓ Service ready on {host}:{port}")
            return True
        
        if i < max_retries - 1:
            logger.info(f"Waiting for service on {host}:{port} (attempt {i+1}/{max_retries})...")
            time.sleep(retry_interval)
    
    logger.error(f"Service not ready on {host}:{port} after {max_retries} attempts")
    return False
