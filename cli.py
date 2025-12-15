#!/usr/bin/env python3
"""
DolphinScheduler EC2 Cluster Management CLI
"""
import click
import sys
import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src.config import (
    load_config, save_config, ConfigVersionManager,
    analyze_config_diff, print_config_diff
)
from src.utils.logger import setup_logger
from src.utils.validator import (
    validate_config, validate_aws_resources,
    validate_database_connection, validate_zookeeper_connection,
    validate_storage_access
)

# Setup logger with detailed file logging
import datetime
log_timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
logger = setup_logger('cli', f'logs/deployment_{log_timestamp}.log')


@click.group()
@click.version_option(version='1.0.0')
def cli():
    """
    DolphinScheduler EC2 Cluster Management Tool
    
    Manage DolphinScheduler clusters on AWS EC2 with ease.
    """
    pass


@cli.command()
@click.option('--config', required=True, type=click.Path(exists=True), help='Configuration file path')
@click.option('--dry-run', is_flag=True, help='Validate only, do not execute')
@click.option('--verbose', is_flag=True, help='Verbose output')
def create(config, dry_run, verbose):
    """
    Create DolphinScheduler cluster
    
    This command will:
    1. Validate configuration
    2. Create EC2 instances
    3. Initialize nodes
    4. Deploy DolphinScheduler
    5. Start services
    """
    try:
        click.echo("=" * 70)
        click.echo("DolphinScheduler Cluster Creation")
        click.echo("=" * 70)
        
        # Load configuration
        click.echo("\n[1/5] Loading configuration...")
        cfg = load_config(config)
        click.echo(f"✓ Configuration loaded from: {config}")
        
        # Validate configuration
        click.echo("\n[2/5] Validating configuration...")
        validate_config(cfg)
        validate_aws_resources(cfg)
        validate_database_connection(cfg['database'])
        validate_zookeeper_connection(cfg['registry']['servers'])
        validate_storage_access(cfg)
        click.echo("✓ All validations passed")
        
        if dry_run:
            click.echo("\n✓ Dry-run mode: Configuration is valid")
            return
        
        # Create cluster
        from src.commands.create import create_cluster
        from src.config import save_config
        
        result = create_cluster(cfg)
        
        # Save updated config with instance information
        save_config(config, cfg)
        
        click.echo("\n" + "=" * 70)
        click.echo("✓ Cluster Creation Completed!")
        click.echo("=" * 70)
        click.echo(f"\nAPI Endpoint: {result['api_endpoint']}")
        click.echo("\nDefault credentials:")
        click.echo("  Username: admin")
        click.echo("  Password: dolphinscheduler123")
        click.echo("\nNext steps:")
        click.echo("  1. Access the Web UI")
        click.echo("  2. Change the default password")
        click.echo("  3. Create your first workflow")
        
    except Exception as e:
        click.echo(f"\n✗ Error: {str(e)}", err=True)
        sys.exit(1)


@cli.command()
@click.option('--config', required=True, type=click.Path(exists=True), help='Configuration file path')
@click.option('--force', is_flag=True, help='Force deletion without confirmation')
@click.option('--keep-data', is_flag=True, help='Keep database and S3 data')
def delete(config, force, keep_data):
    """
    Delete DolphinScheduler cluster
    
    This command will:
    1. Stop all services
    2. Terminate EC2 instances
    3. Optionally clean up data
    """
    try:
        cfg = load_config(config)
        
        if not force:
            click.confirm(
                '⚠️  This will delete the entire cluster. Are you sure?',
                abort=True
            )
        
        from src.commands.delete import delete_cluster
        
        delete_cluster(cfg, keep_data=keep_data)
        
        click.echo("\n✓ Cluster deleted successfully")
        
    except Exception as e:
        click.echo(f"✗ Error: {str(e)}", err=True)
        sys.exit(1)


@cli.command()
@click.option('--config', required=True, type=click.Path(exists=True), help='Configuration file path')
@click.option('--component', required=True, 
              type=click.Choice(['master', 'worker', 'api']),
              help='Component to scale')
@click.option('--count', required=True, type=int, help='Target node count')
def scale(config, component, count):
    """
    Scale cluster components
    
    Increase or decrease the number of nodes for a specific component.
    """
    try:
        cfg = load_config(config)
        current_count = cfg['cluster'][component]['count']
        
        if count == current_count:
            click.echo(f"✓ {component} already has {count} nodes")
            return
        
        from src.commands.scale import scale_cluster
        
        if count > current_count:
            click.echo(f"Scaling out {component}: {current_count} → {count}")
        else:
            click.echo(f"Scaling in {component}: {current_count} → {count}")
        
        scale_cluster(cfg, component, count, config)
        
        click.echo(f"\n✓ {component} scaled to {count} nodes")
        
    except Exception as e:
        click.echo(f"✗ Error: {str(e)}", err=True)
        sys.exit(1)


@cli.command()
@click.option('--config', required=True, type=click.Path(exists=True), help='Configuration file path')
@click.option('--detailed', is_flag=True, help='Show detailed information')
def status(config, detailed):
    """
    Show cluster status
    
    Display the current status of all cluster components.
    """
    try:
        cfg = load_config(config)
        
        click.echo("\n" + "=" * 80)
        click.echo("DolphinScheduler Cluster Status")
        click.echo("=" * 80)
        
        # Basic cluster info
        click.echo("\n📊 Cluster Overview:")
        click.echo(f"  Region: {cfg['aws']['region']}")
        click.echo(f"  VPC: {cfg['aws']['vpc_id']}")
        click.echo(f"  Version: {cfg['deployment']['version']}")
        click.echo(f"  Install Path: {cfg['deployment']['install_path']}")
        
        # Node counts
        click.echo("\n🖥️  Node Configuration:")
        total_nodes = 0
        for component in ['master', 'worker', 'api', 'alert']:
            count = cfg['cluster'][component]['count']
            instance_type = cfg['cluster'][component]['instance_type']
            total_nodes += count
            click.echo(f"  • {component.capitalize()}: {count} x {instance_type}")
        click.echo(f"  Total: {total_nodes} nodes")
        
        # Check if cluster is created
        has_nodes = any(cfg['cluster'][component]['nodes'] for component in ['master', 'worker', 'api', 'alert'])
        
        if not has_nodes:
            click.echo("\n⚠️  Cluster not yet created")
            click.echo("Run 'python cli.py create --config <config>' to create the cluster")
            return
        
        # Node details
        click.echo("\n🌐 Node Details:")
        for component in ['master', 'worker', 'api', 'alert']:
            nodes = cfg['cluster'][component]['nodes']
            if nodes:
                click.echo(f"\n  {component.capitalize()} Nodes:")
                for i, node in enumerate(nodes, 1):
                    click.echo(f"    [{i}] {node['host']}")
                    if detailed:
                        click.echo(f"        Instance ID: {node.get('instance_id', 'N/A')}")
                        click.echo(f"        Subnet: {node.get('subnet_id', 'N/A')}")
                        click.echo(f"        AZ: {node.get('availability_zone', 'N/A')}")
                        if component == 'worker':
                            click.echo(f"        Groups: {', '.join(node.get('groups', ['default']))}")
        
        # Service status
        click.echo("\n🔍 Service Status:")
        try:
            from src.deploy.service_manager import check_service_status
            
            status_result = check_service_status(cfg)
            
            all_running = True
            for component, nodes in status_result.items():
                running_count = sum(1 for n in nodes if n['running'])
                total_count = len(nodes)
                
                if running_count == total_count:
                    status_icon = "✅"
                elif running_count > 0:
                    status_icon = "⚠️"
                    all_running = False
                else:
                    status_icon = "❌"
                    all_running = False
                
                click.echo(f"  {status_icon} {component.capitalize()}: {running_count}/{total_count} running")
                
                if detailed:
                    for node in nodes:
                        node_status = "🟢 Running" if node['running'] else "🔴 Stopped"
                        click.echo(f"      - {node['host']}: {node_status}")
            
            # Overall status
            click.echo("\n📈 Overall Status:")
            if all_running:
                click.echo("  ✅ All services are running")
            else:
                click.echo("  ⚠️  Some services are not running")
                
        except Exception as e:
            click.echo(f"  ⚠️  Could not check service status: {str(e)}")
            click.echo("  Tip: Ensure SSH access is configured and services are started")
        
        # API endpoint
        api_nodes = cfg['cluster']['api']['nodes']
        if api_nodes:
            click.echo("\n🌍 Access Information:")
            api_port = cfg.get('service_config', {}).get('api', {}).get('port', 12345)
            click.echo(f"  Web UI: http://{api_nodes[0]['host']}:{api_port}/dolphinscheduler")
            click.echo(f"  Default Username: admin")
            click.echo(f"  Default Password: dolphinscheduler123")
            
            # Check if ALB is enabled
            alb_config = cfg.get('service_config', {}).get('api', {}).get('load_balancer', {})
            if alb_config.get('enabled'):
                click.echo(f"  Load Balancer: Enabled (check AWS console for DNS)")
        
        # Database info
        click.echo("\n💾 External Services:")
        click.echo(f"  Database: {cfg['database']['host']}")
        click.echo(f"  Zookeeper: {', '.join(cfg['registry']['servers'][:2])}...")
        click.echo(f"  S3 Bucket: {cfg['storage']['bucket']}")
        
        click.echo("\n" + "=" * 80)
        
    except Exception as e:
        click.echo(f"✗ Error: {str(e)}", err=True)
        sys.exit(1)


@cli.command()
@click.option('--config', required=True, type=click.Path(exists=True), help='Configuration file path')
@click.option('--export', type=click.Path(), help='Export cluster info to JSON file')
def info(config, export):
    """
    Show detailed cluster information
    
    Display comprehensive information about the cluster including costs.
    """
    try:
        from src.commands.status import print_cluster_summary, export_cluster_info
        
        cfg = load_config(config)
        
        # Print summary
        print_cluster_summary(cfg)
        
        # Export if requested
        if export:
            export_cluster_info(cfg, export)
            click.echo(f"✓ Cluster information exported to: {export}")
        
    except Exception as e:
        click.echo(f"✗ Error: {str(e)}", err=True)
        sys.exit(1)


@cli.command()
@click.option('--config', required=True, type=click.Path(exists=True), help='Configuration file path')
def validate(config):
    """
    Validate configuration
    
    Check if the configuration is valid and all resources are accessible.
    """
    try:
        click.echo("Validating configuration...")
        
        cfg = load_config(config)
        validate_config(cfg)
        validate_aws_resources(cfg)
        validate_database_connection(cfg['database'])
        validate_zookeeper_connection(cfg['registry']['servers'])
        validate_storage_access(cfg)
        
        click.echo("\n✓ Configuration is valid")
        
    except Exception as e:
        click.echo(f"\n✗ Validation failed: {str(e)}", err=True)
        sys.exit(1)


@cli.command()
@click.option('--region', required=True, help='AWS region')
@click.option('--project', help='Project name to filter resources (optional)')
@click.option('--force', is_flag=True, help='Force cleanup without confirmation')
def cleanup(region, project, force):
    """
    Clean up orphaned resources by tags
    
    Find and delete all resources managed by dolphinscheduler-cli
    using the ManagedBy tag. Useful when config file is lost.
    
    Use --project to only delete resources from a specific project.
    """
    try:
        if project:
            message = f'⚠️  This will delete ALL resources with ManagedBy=dolphinscheduler-cli AND Project={project}. Are you sure?'
        else:
            message = '⚠️  This will delete ALL resources tagged with ManagedBy=dolphinscheduler-cli. Are you sure?'
        
        if not force:
            click.confirm(message, abort=True)
        
        from src.commands.delete import cleanup_by_tags
        
        cleanup_by_tags(region, project_name=project)
        
        click.echo("\n✓ Cleanup completed successfully")
        
    except Exception as e:
        click.echo(f"✗ Error: {str(e)}", err=True)
        sys.exit(1)


# Config management commands
@cli.group()
def config():
    """Configuration management commands"""
    pass


@config.command('update')
@click.option('--config', required=True, type=click.Path(exists=True), help='Configuration file path')
@click.option('--component', 
              type=click.Choice(['master', 'worker', 'api', 'alert', 'all']),
              default='all',
              help='Component to update')
@click.option('--dry-run', is_flag=True, help='Show changes only, do not execute')
def config_update(config, component, dry_run):
    """
    Update configuration and rolling restart
    
    Apply configuration changes with zero-downtime rolling restart.
    """
    try:
        # Load new configuration
        new_cfg = load_config(config)
        
        # Load current configuration from backup
        version_manager = ConfigVersionManager(config)
        versions = version_manager.list_versions()
        
        if not versions:
            click.echo("⚠️  No previous configuration found, assuming first update")
            old_cfg = new_cfg
        else:
            old_cfg = load_config(versions[0]['file'])
        
        # Analyze differences
        changes = analyze_config_diff(old_cfg, new_cfg)
        print_config_diff(changes)
        
        if dry_run:
            click.echo("✓ Dry-run mode: Changes analyzed")
            return
        
        if not changes['requires_restart']:
            click.echo("✓ No restart required")
            return
        
        # Confirm
        if not click.confirm('\nProceed with rolling restart?'):
            click.echo("Operation cancelled")
            return
        
        # Backup current config
        version_manager.backup_current_config()
        
        # Rolling restart
        click.echo("\nStarting rolling restart...")
        click.echo("⚠️  Rolling restart feature is under development")
        click.echo("For now, please manually restart services:")
        click.echo(f"  1. Stop services: ssh to each node and run stop commands")
        click.echo(f"  2. Start services: ssh to each node and run start commands")
        
        click.echo("\n✓ Configuration updated (manual restart required)")
        
    except Exception as e:
        click.echo(f"\n✗ Error: {str(e)}", err=True)
        click.echo("\nYou can rollback using: cli.py config rollback")
        sys.exit(1)


@config.command('history')
@click.option('--config', required=True, type=click.Path(exists=True), help='Configuration file path')
def config_history(config):
    """
    Show configuration history
    
    List all previous configuration versions.
    """
    try:
        version_manager = ConfigVersionManager(config)
        versions = version_manager.list_versions()
        
        if not versions:
            click.echo("No configuration history found")
            return
        
        click.echo("\n" + "=" * 70)
        click.echo("Configuration History")
        click.echo("=" * 70)
        
        for i, version in enumerate(versions, 1):
            click.echo(f"\n{i}. Version: {version['timestamp']}")
            click.echo(f"   File: {version['file']}")
            click.echo(f"   Size: {version['size']} bytes")
        
    except Exception as e:
        click.echo(f"✗ Error: {str(e)}", err=True)
        sys.exit(1)


@config.command('rollback')
@click.option('--config', required=True, type=click.Path(exists=True), help='Configuration file path')
@click.option('--version', required=True, help='Version timestamp (e.g., 20241212_143000)')
def config_rollback(config, version):
    """
    Rollback to previous configuration
    
    Restore a previous configuration version and restart services.
    """
    try:
        if not click.confirm(f'Rollback to version {version}?'):
            click.echo("Operation cancelled")
            return
        
        version_manager = ConfigVersionManager(config)
        version_manager.rollback_to_version(version)
        
        click.echo("✓ Configuration rolled back")
        click.echo("\nUse 'config update' to apply the changes")
        
    except Exception as e:
        click.echo(f"✗ Error: {str(e)}", err=True)
        sys.exit(1)


if __name__ == '__main__':
    cli()
