"""
Elastic Load Balancer management
"""
import boto3
import time
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


def create_alb(config, api_instances):
    """
    Create Application Load Balancer for API nodes
    
    Args:
        config: Configuration dictionary
        api_instances: List of API instance objects
    
    Returns:
        ALB ARN
    """
    elbv2 = boto3.client('elbv2', region_name=config['aws']['region'])
    
    lb_config = config.get('service_config', {}).get('api', {}).get('load_balancer', {})
    
    if not lb_config.get('enabled', False):
        logger.info("ALB not enabled, skipping")
        return None
    
    # Get subnets for ALB (must be public subnets)
    alb_subnets = lb_config.get('subnets', [])
    if not alb_subnets:
        # Use first 2 subnets from config
        alb_subnets = [s['subnet_id'] for s in config['aws']['subnets'][:2]]
    
    # Create ALB
    logger.info("Creating Application Load Balancer...")
    
    lb_response = elbv2.create_load_balancer(
        Name='dolphinscheduler-alb',
        Subnets=alb_subnets,
        SecurityGroups=[config['aws']['security_groups']['api']],
        Scheme=lb_config.get('scheme', 'internet-facing'),
        Type='application',
        Tags=[
            {'Key': 'Name', 'Value': 'dolphinscheduler-alb'},
            {'Key': 'ManagedBy', 'Value': 'dolphinscheduler-cli'}
        ]
    )
    
    alb_arn = lb_response['LoadBalancers'][0]['LoadBalancerArn']
    alb_dns = lb_response['LoadBalancers'][0]['DNSName']
    
    logger.info(f"✓ ALB created: {alb_dns}")
    
    # Create target group
    logger.info("Creating target group...")
    
    health_check = lb_config.get('health_check', {})
    
    tg_response = elbv2.create_target_group(
        Name='ds-api-tg',
        Protocol='HTTP',
        Port=12345,
        VpcId=config['aws']['vpc_id'],
        HealthCheckEnabled=True,
        HealthCheckPath=health_check.get('path', '/dolphinscheduler/actuator/health'),
        HealthCheckIntervalSeconds=health_check.get('interval', 30),
        HealthCheckTimeoutSeconds=health_check.get('timeout', 5),
        HealthyThresholdCount=health_check.get('healthy_threshold', 2),
        UnhealthyThresholdCount=health_check.get('unhealthy_threshold', 3),
        Tags=[
            {'Key': 'Name', 'Value': 'ds-api-tg'},
            {'Key': 'ManagedBy', 'Value': 'dolphinscheduler-cli'}
        ]
    )
    
    tg_arn = tg_response['TargetGroups'][0]['TargetGroupArn']
    logger.info("✓ Target group created")
    
    # Register targets
    logger.info("Registering API instances to target group...")
    
    targets = [{'Id': inst.id} for inst in api_instances]
    elbv2.register_targets(
        TargetGroupArn=tg_arn,
        Targets=targets
    )
    
    logger.info(f"✓ Registered {len(targets)} targets")
    
    # Create listener
    logger.info("Creating listener...")
    
    listener_port = lb_config.get('listener_port', 80)
    
    elbv2.create_listener(
        LoadBalancerArn=alb_arn,
        Protocol='HTTP',
        Port=listener_port,
        DefaultActions=[{
            'Type': 'forward',
            'TargetGroupArn': tg_arn
        }]
    )
    
    logger.info("✓ Listener created")
    
    return {
        'alb_arn': alb_arn,
        'alb_dns': alb_dns,
        'target_group_arn': tg_arn
    }


def get_target_group_arn(config):
    """
    Get target group ARN for API nodes
    
    Args:
        config: Configuration dictionary
    
    Returns:
        Target group ARN or None
    """
    elbv2 = boto3.client('elbv2', region_name=config['aws']['region'])
    
    try:
        response = elbv2.describe_target_groups(
            Names=['ds-api-tg']
        )
        
        if response['TargetGroups']:
            return response['TargetGroups'][0]['TargetGroupArn']
    except:
        pass
    
    return None


def deregister_target_from_alb(elbv2, target_group_arn, instance_id):
    """
    Deregister instance from ALB target group
    
    Args:
        elbv2: ELBv2 client
        target_group_arn: Target group ARN
        instance_id: Instance ID
    
    Returns:
        True if successful
    """
    elbv2.deregister_targets(
        TargetGroupArn=target_group_arn,
        Targets=[{'Id': instance_id}]
    )
    
    logger.info(f"✓ Deregistered {instance_id} from target group")
    return True


def wait_for_target_draining(elbv2, target_group_arn, instance_id, max_wait=300):
    """
    Wait for target to finish draining
    
    Args:
        elbv2: ELBv2 client
        target_group_arn: Target group ARN
        instance_id: Instance ID
        max_wait: Maximum wait time in seconds
    
    Returns:
        True if drained
    """
    start_time = time.time()
    
    while time.time() - start_time < max_wait:
        response = elbv2.describe_target_health(
            TargetGroupArn=target_group_arn,
            Targets=[{'Id': instance_id}]
        )
        
        if not response['TargetHealthDescriptions']:
            logger.info("✓ Target drained")
            return True
        
        state = response['TargetHealthDescriptions'][0]['TargetHealth']['State']
        if state == 'unused':
            logger.info("✓ Target drained")
            return True
        
        logger.info(f"Waiting for target to drain (state: {state})...")
        time.sleep(5)
    
    logger.warning("Target draining timeout")
    return False


def register_target_to_alb(elbv2, target_group_arn, instance_id):
    """
    Register instance to ALB target group
    
    Args:
        elbv2: ELBv2 client
        target_group_arn: Target group ARN
        instance_id: Instance ID
    
    Returns:
        True if successful
    """
    elbv2.register_targets(
        TargetGroupArn=target_group_arn,
        Targets=[{'Id': instance_id}]
    )
    
    logger.info(f"✓ Registered {instance_id} to target group")
    return True


def wait_for_target_healthy(elbv2, target_group_arn, instance_id, max_wait=300):
    """
    Wait for target to become healthy
    
    Args:
        elbv2: ELBv2 client
        target_group_arn: Target group ARN
        instance_id: Instance ID
        max_wait: Maximum wait time in seconds
    
    Returns:
        True if healthy
    """
    start_time = time.time()
    
    while time.time() - start_time < max_wait:
        response = elbv2.describe_target_health(
            TargetGroupArn=target_group_arn,
            Targets=[{'Id': instance_id}]
        )
        
        if response['TargetHealthDescriptions']:
            state = response['TargetHealthDescriptions'][0]['TargetHealth']['State']
            
            if state == 'healthy':
                logger.info("✓ Target is healthy")
                return True
            
            logger.info(f"Waiting for target to be healthy (state: {state})...")
        
        time.sleep(10)
    
    logger.error("Target health check timeout")
    return False
