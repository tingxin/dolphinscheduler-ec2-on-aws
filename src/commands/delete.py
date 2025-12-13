"""
Delete cluster command implementation
"""
import boto3
from src.deploy.installer import stop_services
from src.aws.ec2 import terminate_instances
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


def cleanup_by_tags(region, project_name=None):
    """
    Clean up all resources managed by dolphinscheduler-cli by finding them via tags
    
    Args:
        region: AWS region
        project_name: Optional project name to filter by
    
    Returns:
        True if successful
    """
    logger.info("=" * 70)
    if project_name:
        logger.info(f"Cleaning up resources (ManagedBy=dolphinscheduler-cli, Project={project_name})")
    else:
        logger.info("Cleaning up resources (ManagedBy=dolphinscheduler-cli)")
    logger.info("=" * 70)
    
    ec2 = boto3.client('ec2', region_name=region)
    elbv2 = boto3.client('elbv2', region_name=region)
    
    # Build filters
    filters = [
        {'Name': 'tag:ManagedBy', 'Values': ['dolphinscheduler-cli']},
        {'Name': 'instance-state-name', 'Values': ['running', 'pending', 'stopping', 'stopped']}
    ]
    
    if project_name:
        filters.append({'Name': 'tag:Project', 'Values': [project_name]})
    
    # Find all instances with our tag
    logger.info("\nSearching for EC2 instances...")
    instances_response = ec2.describe_instances(Filters=filters)
    
    instance_ids = []
    for reservation in instances_response['Reservations']:
        for instance in reservation['Instances']:
            instance_ids.append(instance['InstanceId'])
            
            # Get component and project tags
            component = 'unknown'
            project = 'unknown'
            for tag in instance.get('Tags', []):
                if tag['Key'] == 'Component':
                    component = tag['Value']
                elif tag['Key'] == 'Project':
                    project = tag['Value']
            
            logger.info(f"Found instance: {instance['InstanceId']} ({component}, {project}, {instance.get('PrivateIpAddress', 'N/A')})")
    
    # Find all ALBs with our tag
    logger.info("\nSearching for Load Balancers...")
    albs_response = elbv2.describe_load_balancers()
    alb_arns = []
    
    for lb in albs_response['LoadBalancers']:
        try:
            tags_response = elbv2.describe_tags(ResourceArns=[lb['LoadBalancerArn']])
            tags = {tag['Key']: tag['Value'] for tag in tags_response['TagDescriptions'][0]['Tags']}
            
            # Check ManagedBy and optionally Project
            if tags.get('ManagedBy') == 'dolphinscheduler-cli':
                if project_name is None or tags.get('Project') == project_name:
                    alb_arns.append(lb['LoadBalancerArn'])
                    logger.info(f"Found ALB: {lb['LoadBalancerName']} (Project: {tags.get('Project', 'N/A')})")
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
        # Step 1: Stop services (if nodes are configured)
        logger.info("=" * 70)
        logger.info("Step 1: Stopping Services")
        logger.info("=" * 70)
        
        has_nodes = any(config['cluster'][component]['nodes'] for component in ['master', 'worker', 'api', 'alert'])
        
        if has_nodes:
            try:
                stop_services(config)
                logger.info("✓ Services stopped")
            except Exception as e:
                logger.warning(f"Failed to stop services gracefully: {e}")
                logger.info("Continuing with deletion...")
        else:
            logger.info("No nodes configured in config, skipping service stop")
        
        # Step 2: Collect instance IDs from config AND AWS tags
        logger.info("\n" + "=" * 70)
        logger.info("Step 2: Collecting Instance Information")
        logger.info("=" * 70)
        
        instance_ids_from_config = []
        
        # Try to get from config first
        for component in ['master', 'worker', 'api', 'alert']:
            nodes = config['cluster'][component]['nodes']
            for node in nodes:
                if 'instance_id' in node:
                    instance_ids_from_config.append(node['instance_id'])
                    logger.info(f"Found {component} instance from config: {node['instance_id']}")
        
        # Also search by tags (in case config is incomplete)
        logger.info("\nSearching for instances by tags...")
        ec2 = boto3.client('ec2', region_name=config['aws']['region'])
        
        project_name = config.get('project', {}).get('name', 'dolphinscheduler')
        
        instances_response = ec2.describe_instances(
            Filters=[
                {'Name': 'tag:ManagedBy', 'Values': ['dolphinscheduler-cli']},
                {'Name': 'tag:Project', 'Values': [project_name]},
                {'Name': 'instance-state-name', 'Values': ['running', 'pending', 'stopping', 'stopped']}
            ]
        )
        
        instance_ids_from_tags = []
        for reservation in instances_response['Reservations']:
            for instance in reservation['Instances']:
                instance_id = instance['InstanceId']
                instance_ids_from_tags.append(instance_id)
                
                # Get component tag
                component = 'unknown'
                for tag in instance.get('Tags', []):
                    if tag['Key'] == 'Component':
                        component = tag['Value']
                        break
                
                logger.info(f"Found {component} instance from tags: {instance_id} ({instance.get('PrivateIpAddress', 'N/A')})")
        
        # Merge both lists (remove duplicates)
        instance_ids = list(set(instance_ids_from_config + instance_ids_from_tags))
        
        logger.info(f"\n✓ Found {len(instance_ids)} instances to terminate")
        
        # Step 3: Delete ALB (search by tags)
        logger.info("\n" + "=" * 70)
        logger.info("Step 3: Deleting Load Balancer")
        logger.info("=" * 70)
        
        delete_alb_by_tags(config)
        
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


def delete_alb_by_tags(config):
    """
    Delete Application Load Balancer by searching with tags
    
    Args:
        config: Configuration dictionary
    
    Returns:
        True if successful
    """
    logger.info("Searching for Load Balancers by tags...")
    
    elbv2 = boto3.client('elbv2', region_name=config['aws']['region'])
    project_name = config.get('project', {}).get('name', 'dolphinscheduler')
    
    try:
        # Find all ALBs
        response = elbv2.describe_load_balancers()
        
        deleted_alb_count = 0
        deleted_tg_count = 0
        
        for lb in response['LoadBalancers']:
            lb_arn = lb['LoadBalancerArn']
            
            # Check tags to confirm this is our ALB
            try:
                tags_response = elbv2.describe_tags(ResourceArns=[lb_arn])
                tags = {tag['Key']: tag['Value'] for tag in tags_response['TagDescriptions'][0]['Tags']}
                
                # Match by ManagedBy and Project tags
                if tags.get('ManagedBy') == 'dolphinscheduler-cli' and tags.get('Project') == project_name:
                    logger.info(f"Found managed ALB: {lb['LoadBalancerName']}")
                    
                    # Get and delete target groups first
                    try:
                        tg_response = elbv2.describe_target_groups(LoadBalancerArn=lb_arn)
                        for tg in tg_response['TargetGroups']:
                            logger.info(f"Deleting target group: {tg['TargetGroupName']}")
                            elbv2.delete_target_group(TargetGroupArn=tg['TargetGroupArn'])
                            deleted_tg_count += 1
                    except Exception as e:
                        logger.warning(f"Failed to delete target groups: {e}")
                    
                    # Delete ALB
                    logger.info(f"Deleting ALB: {lb['LoadBalancerName']}")
                    elbv2.delete_load_balancer(LoadBalancerArn=lb_arn)
                    deleted_alb_count += 1
                    
            except Exception as e:
                logger.debug(f"Error checking ALB {lb['LoadBalancerName']}: {e}")
                continue
        
        # Also search for orphaned target groups
        try:
            all_tgs = elbv2.describe_target_groups()
            for tg in all_tgs['TargetGroups']:
                try:
                    tags_response = elbv2.describe_tags(ResourceArns=[tg['TargetGroupArn']])
                    tags = {tag['Key']: tag['Value'] for tag in tags_response['TagDescriptions'][0]['Tags']}
                    
                    if tags.get('ManagedBy') == 'dolphinscheduler-cli' and tags.get('Project') == project_name:
                        logger.info(f"Deleting orphaned target group: {tg['TargetGroupName']}")
                        elbv2.delete_target_group(TargetGroupArn=tg['TargetGroupArn'])
                        deleted_tg_count += 1
                except:
                    continue
        except Exception as e:
            logger.debug(f"Error searching for orphaned target groups: {e}")
        
        if deleted_alb_count > 0 or deleted_tg_count > 0:
            logger.info(f"✓ Deleted {deleted_alb_count} ALB(s) and {deleted_tg_count} target group(s)")
        else:
            logger.info("No managed ALBs or target groups found to delete")
        
        return True
        
    except Exception as e:
        logger.warning(f"Failed to delete ALB: {e}")
        return False
