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
    
    # Validate storage configuration based on type
    storage_type = config.get('storage', {}).get('type', 'LOCAL').upper()
    
    # Validate storage-specific fields
    if storage_type == 'HDFS':
        hdfs_namenode = get_nested_value(config, 'storage.hdfs.namenode_host')
        hdfs_port = get_nested_value(config, 'storage.hdfs.namenode_port')
        if not hdfs_namenode:
            errors.append("Missing required field: storage.hdfs.namenode_host")
        if not hdfs_port:
            errors.append("Missing required field: storage.hdfs.namenode_port")
    elif storage_type == 'S3':
        # S3 storage requires additional S3-specific fields
        s3_bucket = get_nested_value(config, 'storage.bucket')
        s3_region = get_nested_value(config, 'storage.region')
        s3_access_key = get_nested_value(config, 'storage.access_key_id')
        s3_secret_key = get_nested_value(config, 'storage.secret_access_key')
        s3_use_iam = get_nested_value(config, 'storage.use_iam_role')
        
        if not s3_bucket:
            errors.append("Missing required field: storage.bucket (for S3 storage)")
        if not s3_region:
            errors.append("Missing required field: storage.region (for S3 storage)")
        if not s3_use_iam and (not s3_access_key or not s3_secret_key):
            errors.append("S3 storage requires either use_iam_role=true or access_key_id/secret_access_key")
    
    # Validate package distribution configuration if enabled
    pkg_dist_enabled = config.get('package_distribution', {}).get('enabled', False)
    if pkg_dist_enabled:
        pkg_bucket = get_nested_value(config, 'package_distribution.s3.bucket')
        pkg_region = get_nested_value(config, 'package_distribution.s3.region')
        if not pkg_bucket:
            errors.append("Missing required field: package_distribution.s3.bucket")
        if not pkg_region:
            errors.append("Missing required field: package_distribution.s3.region")
    
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
        # 确保密码是字符串类型，解决 PyMySQL 兼容性问题
        password = str(db_config['password']) if db_config['password'] is not None else ''
        
        # First try to connect to MySQL server (without specifying database)
        conn = pymysql.connect(
            host=db_config['host'],
            port=db_config.get('port', 3306),
            user=db_config['username'],
            password=password,
            connect_timeout=10,
            charset='utf8mb4'  # 添加字符集支持
        )
        
        # Check if target database exists, if not try to create it
        cursor = conn.cursor()
        database_name = db_config['database']
        
        try:
            cursor.execute(f"USE `{database_name}`")
            logger.info(f"✓ Database '{database_name}' exists and accessible")
        except Exception:
            # Database doesn't exist or no access, try to create it
            try:
                cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{database_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
                logger.info(f"✓ Database '{database_name}' created")
            except Exception as create_error:
                logger.warning(f"Could not create database '{database_name}': {create_error}")
                logger.info(f"Assuming database '{database_name}' will be created by admin")
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


def validate_storage_access(config):
    """
    Validate storage access based on configured storage type
    
    Args:
        config: Configuration dictionary
    
    Raises:
        ValueError: If storage validation fails
    
    Returns:
        True if valid
    """
    storage_config = config.get('storage', {})
    storage_type = storage_config.get('type', 'LOCAL').upper()
    
    logger.info(f"Validating {storage_type} storage access...")
    
    if storage_type == 'HDFS':
        # Validate HDFS connectivity
        validate_hdfs_access(storage_config)
        logger.info("✓ HDFS storage validation passed")
    elif storage_type == 'S3':
        # Validate S3 access
        validate_s3_access(storage_config)
        logger.info("✓ S3 storage validation passed")
    else:
        # LOCAL storage doesn't need validation
        logger.info("✓ LOCAL storage (no validation needed)")
    
    return True


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


def validate_hdfs_access(storage_config):
    """
    Validate HDFS connectivity and access
    
    Args:
        storage_config: Storage configuration with HDFS settings
    
    Raises:
        ValueError: If HDFS is not accessible
    
    Returns:
        True if valid
    """
    import socket
    
    hdfs_config = storage_config.get('hdfs', {})
    namenode_host = hdfs_config.get('namenode_host')
    namenode_port = hdfs_config.get('namenode_port', 8020)
    
    if not namenode_host:
        raise ValueError("HDFS namenode_host is not configured")
    
    try:
        # Test HDFS NameNode connectivity
        logger.info(f"Testing HDFS NameNode connectivity: {namenode_host}:{namenode_port}")
        
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10)
        result = sock.connect_ex((namenode_host, namenode_port))
        sock.close()
        
        if result == 0:
            logger.info(f"✓ HDFS NameNode is reachable: {namenode_host}:{namenode_port}")
            return True
        else:
            raise ValueError(f"HDFS NameNode is not reachable at {namenode_host}:{namenode_port}")
            
    except socket.gaierror as e:
        raise ValueError(f"HDFS NameNode hostname resolution failed: {namenode_host} - {str(e)}")
    except socket.timeout:
        raise ValueError(f"HDFS NameNode connection timeout: {namenode_host}:{namenode_port}")
    except Exception as e:
        raise ValueError(f"HDFS connectivity validation failed: {str(e)}")
