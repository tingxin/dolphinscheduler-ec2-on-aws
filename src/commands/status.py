"""
Status command implementation
"""
import boto3
from datetime import datetime
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


def get_cluster_info(config):
    """
    Get comprehensive cluster information
    
    Args:
        config: Configuration dictionary
    
    Returns:
        Dictionary with cluster information
    """
    info = {
        'basic': {},
        'nodes': {},
        'ec2_status': {},
        'costs': {}
    }
    
    # Basic info
    info['basic'] = {
        'region': config['aws']['region'],
        'vpc_id': config['aws']['vpc_id'],
        'version': config['deployment']['version'],
        'install_path': config['deployment']['install_path']
    }
    
    # Node counts
    info['nodes'] = {}
    for component in ['master', 'worker', 'api', 'alert']:
        info['nodes'][component] = {
            'count': config['cluster'][component]['count'],
            'instance_type': config['cluster'][component]['instance_type'],
            'nodes': config['cluster'][component]['nodes']
        }
    
    # Get EC2 instance status from AWS
    try:
        info['ec2_status'] = get_ec2_instance_status(config)
    except Exception as e:
        logger.warning(f"Could not get EC2 status: {e}")
        info['ec2_status'] = {}
    
    return info


def get_ec2_instance_status(config):
    """
    Get EC2 instance status from AWS
    
    Args:
        config: Configuration dictionary
    
    Returns:
        Dictionary with instance status
    """
    ec2 = boto3.client('ec2', region_name=config['aws']['region'])
    
    # Collect all instance IDs
    instance_ids = []
    for component in ['master', 'worker', 'api', 'alert']:
        for node in config['cluster'][component]['nodes']:
            if 'instance_id' in node:
                instance_ids.append(node['instance_id'])
    
    if not instance_ids:
        return {}
    
    # Get instance status
    response = ec2.describe_instances(InstanceIds=instance_ids)
    
    status_map = {}
    for reservation in response['Reservations']:
        for instance in reservation['Instances']:
            instance_id = instance['InstanceId']
            status_map[instance_id] = {
                'state': instance['State']['Name'],
                'launch_time': instance.get('LaunchTime'),
                'private_ip': instance.get('PrivateIpAddress'),
                'public_ip': instance.get('PublicIpAddress'),
                'instance_type': instance['InstanceType'],
                'availability_zone': instance['Placement']['AvailabilityZone']
            }
    
    return status_map


def get_cluster_costs(config):
    """
    Estimate cluster costs (approximate)
    
    Args:
        config: Configuration dictionary
    
    Returns:
        Dictionary with cost estimates
    """
    # Approximate hourly costs for common instance types (us-east-2)
    # These are rough estimates, actual costs may vary
    instance_costs = {
        't3.small': 0.0208,
        't3.medium': 0.0416,
        't3.large': 0.0832,
        't3.xlarge': 0.1664,
        't3.2xlarge': 0.3328,
    }
    
    total_hourly = 0
    breakdown = {}
    
    for component in ['master', 'worker', 'api', 'alert']:
        count = config['cluster'][component]['count']
        instance_type = config['cluster'][component]['instance_type']
        
        hourly_cost = instance_costs.get(instance_type, 0.1)  # Default estimate
        component_cost = hourly_cost * count
        total_hourly += component_cost
        
        breakdown[component] = {
            'count': count,
            'instance_type': instance_type,
            'hourly': component_cost,
            'daily': component_cost * 24,
            'monthly': component_cost * 24 * 30
        }
    
    return {
        'hourly': total_hourly,
        'daily': total_hourly * 24,
        'monthly': total_hourly * 24 * 30,
        'breakdown': breakdown
    }


def print_cluster_summary(config):
    """
    Print a summary of cluster information
    
    Args:
        config: Configuration dictionary
    """
    info = get_cluster_info(config)
    
    print("\n" + "=" * 80)
    print("Cluster Summary")
    print("=" * 80)
    
    # Basic info
    print(f"\nRegion: {info['basic']['region']}")
    print(f"VPC: {info['basic']['vpc_id']}")
    print(f"Version: {info['basic']['version']}")
    
    # Node summary
    print("\nNodes:")
    total_nodes = 0
    for component, data in info['nodes'].items():
        count = data['count']
        total_nodes += count
        print(f"  {component.capitalize()}: {count} x {data['instance_type']}")
    print(f"  Total: {total_nodes} nodes")
    
    # EC2 status
    if info['ec2_status']:
        print("\nEC2 Instance Status:")
        running = sum(1 for s in info['ec2_status'].values() if s['state'] == 'running')
        stopped = sum(1 for s in info['ec2_status'].values() if s['state'] == 'stopped')
        print(f"  Running: {running}")
        print(f"  Stopped: {stopped}")
    
    # Cost estimate
    try:
        costs = get_cluster_costs(config)
        print("\nEstimated Costs (EC2 only):")
        print(f"  Hourly: ${costs['hourly']:.2f}")
        print(f"  Daily: ${costs['daily']:.2f}")
        print(f"  Monthly: ${costs['monthly']:.2f}")
        print("  Note: Excludes RDS, S3, data transfer, and other AWS services")
    except Exception as e:
        logger.debug(f"Could not calculate costs: {e}")
    
    print("=" * 80 + "\n")


def export_cluster_info(config, output_file):
    """
    Export cluster information to JSON file
    
    Args:
        config: Configuration dictionary
        output_file: Output file path
    """
    import json
    from datetime import datetime
    
    info = get_cluster_info(config)
    
    # Add export metadata
    info['export_time'] = datetime.now().isoformat()
    info['export_version'] = '1.0.0'
    
    # Convert datetime objects to strings
    if 'ec2_status' in info:
        for instance_id, status in info['ec2_status'].items():
            if 'launch_time' in status and status['launch_time']:
                status['launch_time'] = status['launch_time'].isoformat()
    
    with open(output_file, 'w') as f:
        json.dump(info, f, indent=2)
    
    logger.info(f"Cluster information exported to: {output_file}")
