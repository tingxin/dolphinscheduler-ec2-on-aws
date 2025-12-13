"""
Delete cluster command implementation
"""
import boto3
from src.deploy.installer import stop_services
from src.aws.ec2 import terminate_instances
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


def cleanup_by_tags(region):
    """
    Clean up all resources managed by dolphinscheduler-cli by finding them via tags
    
    Args:
        region: AWS region
    
    Returns:
        True if successful
    """
    logger.info("=" * 70)
    logger.info("Cleaning up resources by tags (ManagedBy=dolphinscheduler-cli)")
    logger.info("=" * 70)
    
    ec2 = boto3.client('ec2', region_name=region)
    elbv2 = boto3.client('elbv2', region_name=region)
    
    # Find all instances with our tag
    logger.info("\nSearching for EC2 instances...")
    instances_response = ec2.describe_instances(
        Filters=[
            {'Name': 'tag:ManagedBy', 'Values': ['dolphinscheduler-cli']},
            {'Name': 'instance-state-name', 'Values': ['running', 'pending', 'stopping', 'stopped']}
        ]
    )
    
    instance_ids = []
    for reservation in instances_response['Reservations']:
        for instance in reservation['Instances']:
            instance_ids.append(instance['InstanceId'])
            logger.info(f"Found instance: {instance['InstanceId']} ({instance.get('PrivateIpAddress', 'N/A')})")
    
    # Find all ALBs with our tag
    logger.info("\nSearching for Load Balancers...")
    albs_response = elbv2.describe_load_balancers()
    alb_arns = []
    
    for lb in albs_response['LoadBalancers']:
        try:
            tags_response = elbv2.describe_tags(ResourceArns=[lb['LoadBalancerArn']])
            tags = {tag['Key']: tag['Value'] for tag in tags_response['TagDescriptions'][0]['Tags']}
            
            if tags.get('ManagedBy') == 'dolphinscheduler-cli':
                alb_arns.append(lb['LoadBalancerArn'])
                logger.info(f"Found ALB: {lb['LoadBalancerName']}")
        except:
            continue
    
    # Confirm deletion
    logger.info("\n" + "=" * 70)
    logger.info(f"Found {len(instance_ids)} instances and {len(alb_arns)} ALBs to delete")
    logger.info("=" * 70)
    
    if not instance_ids and not alb_arns:
        logger.info("No resources found to delete")
        return True
    
    # Delete ALBs
    if alb_arns:
        logger.info("\nDeleting Load Balancers...")
        for alb_arn in alb_arns:
            try:
                # Delete target groups
                tg_response = elbv2.describe_target_groups(LoadBalancerArn=alb_arn)
                for tg in tg_response['TargetGroups']:
                    logger.info(f"Deleting target group: {tg['TargetGroupName']}")
                    elbv2.delete_target_group(TargetGroupArn=tg['TargetGroupArn'])
                
                # Delete ALB
                logger.info(f"Deleting ALB: {alb_arn}")
                elbv2.delete_load_balancer(LoadBalancerArn=alb_arn)
            except Exception as e:
                logger.error(f"Failed to delete ALB {alb_arn}: {e}")
    
    # Terminate instances
    if instance_ids:
        logger.info("\nTerminating EC2 instances...")
        try:
            ec2.terminate_instances(InstanceIds=instance_ids)
            logger.info(f"✓ Terminated {len(instance_ids)} instances")
        except Exception as e:
            logger.error(f"Failed to terminate instances: {e}")
    
    logger.info("\n" + "=" * 70)
    logger.info("✓ Cleanup completed")
    logger.info("=" * 70)
    
    return True


def delete_cluster(config, keep_data=False):
    """
    Delete DolphinScheduler cluster
    
    Args:
        config: Configuration dictionary
        keep_data: Keep database and S3 data
    
    Returns:
        True if successful
    """
    try:
        # Step 1: Stop services
        logger.info("=" * 70)
        logger.info("Step 1: Stopping Services")
        logger.info("=" * 70)
        
        try:
            stop_services(config)
            logger.info("✓ Services stopped")
        except Exception as e:
            logger.warning(f"Failed to stop services gracefully: {e}")
            logger.info("Continuing with deletion...")
        
        # Step 2: Collect instance IDs
        logger.info("\n" + "=" * 70)
        logger.info("Step 2: Collecting Instance Information")
        logger.info("=" * 70)
        
        instance_ids = []
        
        for component in ['master', 'worker', 'api', 'alert']:
            nodes = config['cluster'][component]['nodes']
            for node in nodes:
                if 'instance_id' in node:
                    instance_ids.append(node['instance_id'])
                    logger.info(f"Found {component} instance: {node['instance_id']}")
        
        logger.info(f"\n✓ Found {len(instance_ids)} instances to terminate")
        
        # Step 3: Delete ALB (if exists)
        logger.info("\n" + "=" * 70)
        logger.info("Step 3: Deleting Load Balancer")
        logger.info("=" * 70)
        
        delete_alb(config)
        
        # Step 4: Terminate instances
        logger.info("\n" + "=" * 70)
        logger.info("Step 4: Terminating EC2 Instances")
        logger.info("=" * 70)
        
        if instance_ids:
            terminate_instances(config, instance_ids)
            logger.info(f"✓ Terminated {len(instance_ids)} instances")
        else:
            logger.info("No instances to terminate")
        
        # Step 5: Clean up data (optional)
        if not keep_data:
            logger.info("\n" + "=" * 70)
            logger.info("Step 4: Cleaning Up Data")
            logger.info("=" * 70)
            
            logger.info("\n⚠️  Data cleanup not implemented")
            logger.info("To manually clean up:")
            logger.info(f"  - Database: {config['database']['database']}")
            logger.info(f"  - S3 bucket: {config['storage']['bucket']}")
        else:
            logger.info("\n✓ Keeping database and S3 data")
        
        logger.info("\n" + "=" * 70)
        logger.info("✓ Cluster Deletion Completed")
        logger.info("=" * 70)
        
        return True
        
    except Exception as e:
        logger.error(f"Deletion failed: {str(e)}")
        raise


def delete_alb(config):
    """
    Delete Application Load Balancer
    
    Args:
        config: Configuration dictionary
    
    Returns:
        True if successful
    """
    if not config.get('service_config', {}).get('api', {}).get('load_balancer', {}).get('enabled'):
        logger.info("ALB not enabled, skipping")
        return True
    
    logger.info("Deleting Application Load Balancer...")
    
    elbv2 = boto3.client('elbv2', region_name=config['aws']['region'])
    
    try:
        # Find ALB by tags
        response = elbv2.describe_load_balancers()
        
        deleted_count = 0
        for lb in response['LoadBalancers']:
            lb_arn = lb['LoadBalancerArn']
            
            # Check tags to confirm this is our ALB
            try:
                tags_response = elbv2.describe_tags(ResourceArns=[lb_arn])
                tags = {tag['Key']: tag['Value'] for tag in tags_response['TagDescriptions'][0]['Tags']}
                
                if tags.get('ManagedBy') == 'dolphinscheduler-cli':
                    logger.info(f"Found managed ALB: {lb['LoadBalancerName']}")
                    
                    # Get and delete target groups first
                    try:
                        tg_response = elbv2.describe_target_groups(LoadBalancerArn=lb_arn)
                        for tg in tg_response['TargetGroups']:
                            logger.info(f"Deleting target group: {tg['TargetGroupName']}")
                            elbv2.delete_target_group(TargetGroupArn=tg['TargetGroupArn'])
                    except Exception as e:
                        logger.warning(f"Failed to delete target groups: {e}")
                    
                    # Delete ALB
                    logger.info(f"Deleting ALB: {lb['LoadBalancerName']}")
                    elbv2.delete_load_balancer(LoadBalancerArn=lb_arn)
                    deleted_count += 1
                    
            except Exception as e:
                logger.debug(f"Error checking ALB {lb['LoadBalancerName']}: {e}")
                continue
        
        if deleted_count > 0:
            logger.info(f"✓ Deleted {deleted_count} ALB(s)")
        else:
            logger.info("No managed ALBs found to delete")
        
        return True
        
    except Exception as e:
        logger.warning(f"Failed to delete ALB: {e}")
        return False
