"""
Configuration validation utilities
"""
import boto3
import pymysql
from kazoo.client import KazooClient
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


def get_nested_value(data, path):
    """
    Get nested value from dict using dot notation
    
    Args:
        data: Dictionary
        path: Dot notation path (e.g., 'database.host')
    
    Returns:
        Value or None
    """
    keys = path.split('.')
    value = data
    for key in keys:
        if isinstance(value, dict) and key in value:
            value = value[key]
        else:
            return None
    return value


def validate_config(config):
    """
    Validate configuration completeness
    
    Args:
        config: Configuration dictionary
    
    Raises:
        ValueError: If validation fails
    
    Returns:
        True if valid
    """
    errors = []
    
    # Required fields
    required_fields = [
        'database.host',
        'database.username',
        'database.password',
        'database.database',
        'registry.servers',
        'storage.bucket',
        'storage.region',
        'aws.region',
        'aws.vpc_id',
        'aws.subnets',
        'aws.key_name',
        'cluster.master.count',
        'cluster.worker.count',
        'cluster.api.count',
        'deployment.user',
        'deployment.install_path',
    ]
    
    for field in required_fields:
        value = get_nested_value(config, field)
        if value is None or (isinstance(value, (str, list)) and not value):
            errors.append(f"Missing required field: {field}")
    
    # Validate node counts
    master_count = config.get('cluster', {}).get('master', {}).get('count', 0)
    if master_count < 2:
        errors.append("Master node count must be at least 2 for high availability")
    
    api_count = config.get('cluster', {}).get('api', {}).get('count', 0)
    if api_count < 1:
        errors.append("API node count must be at least 1")
    
    worker_count = config.get('cluster', {}).get('worker', {}).get('count', 0)
    if worker_count < 1:
        errors.append("Worker node count must be at least 1")
    
    # Validate availability zone distribution
    master_nodes = config.get('cluster', {}).get('master', {}).get('nodes', [])
    if master_nodes:
        master_azs = set(node.get('availability_zone') for node in master_nodes if node.get('availability_zone'))
        if len(master_azs) < 2:
            errors.append("Master nodes must be distributed across at least 2 availability zones")
    
    # Validate subnets
    subnets = config.get('aws', {}).get('subnets', [])
    if len(subnets) < 2:
        errors.append("At least 2 subnets in different AZs are required for high availability")
    
    if errors:
        raise ValueError("Configuration validation failed:\n" + "\n".join(f"  - {e}" for e in errors))
    
    logger.info("✓ Configuration validation passed")
    return True


def validate_aws_resources(config):
    """
    Validate AWS resources exist
    
    Args:
        config: Configuration dictionary
    
    Raises:
        ValueError: If validation fails
    
    Returns:
        True if valid
    """
    region = config['aws']['region']
    ec2 = boto3.client('ec2', region_name=region)
    
    # Validate VPC
    vpc_id = config['aws']['vpc_id']
    try:
        response = ec2.describe_vpcs(VpcIds=[vpc_id])
        if not response['Vpcs']:
            raise ValueError(f"VPC not found: {vpc_id}")
        logger.info(f"✓ VPC validated: {vpc_id}")
    except Exception as e:
        raise ValueError(f"VPC validation failed: {str(e)}")
    
    # Validate subnets
    subnet_ids = [s['subnet_id'] for s in config['aws']['subnets']]
    try:
        response = ec2.describe_subnets(SubnetIds=subnet_ids)
        if len(response['Subnets']) != len(subnet_ids):
            raise ValueError("Some subnets not found")
        
        # Verify subnets are in the correct VPC
        for subnet in response['Subnets']:
            if subnet['VpcId'] != vpc_id:
                raise ValueError(f"Subnet {subnet['SubnetId']} is not in VPC {vpc_id}")
        
        logger.info(f"✓ Subnets validated: {len(subnet_ids)} subnets")
    except Exception as e:
        raise ValueError(f"Subnet validation failed: {str(e)}")
    
    # Validate security groups
    sg_ids = list(config['aws']['security_groups'].values())
    unique_sg_ids = list(set(sg_ids))  # Remove duplicates
    
    try:
        response = ec2.describe_security_groups(GroupIds=unique_sg_ids)
        found_sg_ids = [sg['GroupId'] for sg in response['SecurityGroups']]
        
        if len(found_sg_ids) != len(unique_sg_ids):
            missing = set(unique_sg_ids) - set(found_sg_ids)
            raise ValueError(f"Security groups not found: {', '.join(missing)}")
        
        # Verify security groups are in the correct VPC
        for sg in response['SecurityGroups']:
            if sg['VpcId'] != vpc_id:
                raise ValueError(f"Security group {sg['GroupId']} is not in VPC {vpc_id} (found in {sg['VpcId']})")
        
        logger.info(f"✓ Security groups validated: {len(unique_sg_ids)} unique group(s)")
    except Exception as e:
        raise ValueError(f"Security group validation failed: {str(e)}")
    
    # Validate key pair
    key_name = config['aws']['key_name']
    try:
        response = ec2.describe_key_pairs(KeyNames=[key_name])
        if not response['KeyPairs']:
            raise ValueError(f"Key pair not found: {key_name}")
        logger.info(f"✓ Key pair validated: {key_name}")
    except Exception as e:
        raise ValueError(f"Key pair validation failed: {str(e)}")
    
    logger.info("✓ AWS resources validation passed")
    return True


def validate_database_connection(db_config):
    """
    Validate database connection
    
    Args:
        db_config: Database configuration
    
    Raises:
        ValueError: If connection fails
    
    Returns:
        True if valid
    """
    try:
        conn = pymysql.connect(
            host=db_config['host'],
            port=db_config.get('port', 3306),
            user=db_config['username'],
            password=db_config['password'],
            database=db_config['database'],
            connect_timeout=10
        )
        conn.close()
        logger.info(f"✓ Database connection validated: {db_config['host']}")
        return True
    except Exception as e:
        raise ValueError(f"Database connection failed: {str(e)}")


def validate_zookeeper_connection(zk_servers):
    """
    Validate Zookeeper connection
    
    Args:
        zk_servers: List of Zookeeper servers
    
    Raises:
        ValueError: If connection fails
    
    Returns:
        True if valid
    """
    zk = KazooClient(hosts=','.join(zk_servers))
    try:
        zk.start(timeout=10)
        zk.stop()
        logger.info(f"✓ Zookeeper connection validated: {len(zk_servers)} servers")
        return True
    except Exception as e:
        raise ValueError(f"Zookeeper connection failed: {str(e)}")


def validate_s3_access(storage_config):
    """
    Validate S3 access
    
    Args:
        storage_config: Storage configuration
    
    Raises:
        ValueError: If access fails
    
    Returns:
        True if valid
    """
    s3 = boto3.client('s3', region_name=storage_config['region'])
    bucket = storage_config['bucket']
    
    try:
        # Test list objects
        s3.list_objects_v2(Bucket=bucket, MaxKeys=1)
        
        # Test write permission
        test_key = f"{storage_config.get('upload_path', '/dolphinscheduler')}/test.txt"
        s3.put_object(Bucket=bucket, Key=test_key, Body=b'test')
        s3.delete_object(Bucket=bucket, Key=test_key)
        
        logger.info(f"✓ S3 access validated: {bucket}")
        return True
    except Exception as e:
        raise ValueError(f"S3 access failed: {str(e)}")
