"""
DolphinScheduler configuration file generators
"""
import tempfile
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


def generate_application_yaml_v320(config, component='master'):
    """
    Generate application.yaml for DolphinScheduler 3.2.x component
    
    Supports both 3.2.0 and 3.2.2 versions.
    
    Args:
        config: Configuration dictionary
        component: Component name (master, worker, api, alert)
    
    Returns:
        Configuration content string (YAML format)
    """
    db_config = config['database']
    registry_config = config['registry']
    service_config = config.get('service_config', {}).get(component, {})
    version = config.get('deployment', {}).get('version', '3.2.0')
    
    # Build database URL
    db_params = db_config.get('params', 'useUnicode=true&characterEncoding=UTF-8&useSSL=false&allowPublicKeyRetrieval=true')
    db_url = f"jdbc:mysql://{db_config['host']}:{db_config.get('port', 3306)}/{db_config['database']}?{db_params}"
    
    # Build Zookeeper connection string
    zk_connect = ','.join(registry_config['servers'])
    
    # Get registry timeout settings (3.2.2 format)
    session_timeout = registry_config.get('session_timeout', 30000)
    connection_timeout = registry_config.get('connection_timeout', 9000)
    block_until_connected = registry_config.get('block_until_connected', 600)
    base_sleep_time = registry_config.get('retry', {}).get('base_sleep_time', 60)
    max_sleep_time = registry_config.get('retry', {}).get('max_sleep_time', 300)
    max_retries = registry_config.get('retry', {}).get('max_retries', 5)
    
    # Base configuration for components that need database access
    if component in ['master', 'worker', 'api', 'alert']:
        yaml_content = f"""spring:
  profiles:
    active: mysql
  datasource:
    driver-class-name: com.mysql.cj.jdbc.Driver
    url: {db_url}
    username: {db_config['username']}
    password: {db_config['password']}

registry:
  type: {registry_config['type']}
  zookeeper:
    namespace: {registry_config.get('namespace', 'dolphinscheduler')}
    connect-string: {zk_connect}
    retry-policy:
      base-sleep-time: {base_sleep_time}ms
      max-sleep: {max_sleep_time}ms
      max-retries: {max_retries}
    session-timeout: {session_timeout // 1000}s
    connection-timeout: {connection_timeout // 1000}s
    block-until-connected: {block_until_connected}ms

"""
    
    # Add component-specific configuration
    if component == 'master':
        listen_port = service_config.get('listen_port', 5679)  # 3.2.2 default
        yaml_content += f"""server:
  port: {listen_port}

metrics:
  enabled: true
"""
    
    elif component == 'worker':
        listen_port = service_config.get('listen_port', 1235)  # 3.2.2 default
        yaml_content += f"""server:
  port: {listen_port}

metrics:
  enabled: true
"""
    
    elif component == 'api':
        api_port = service_config.get('port', 12345)
        yaml_content += f"""server:
  port: {api_port}

metrics:
  enabled: true
"""
    
    elif component == 'alert':
        yaml_content += f"""server:
  port: 50052

metrics:
  enabled: true
"""
    
    return yaml_content


def generate_install_env_v320(config):
    """
    Generate install_env.sh for DolphinScheduler 3.2.0
    
    Args:
        config: Configuration dictionary
    
    Returns:
        Configuration content string
    """
    # Collect node information
    all_ips = []
    master_ips = []
    worker_configs = []
    api_ips = []
    alert_ip = None
    
    for node in config['cluster']['master']['nodes']:
        ip = node['host']
        all_ips.append(ip)
        master_ips.append(ip)
    
    for node in config['cluster']['worker']['nodes']:
        ip = node['host']
        groups = ','.join(node.get('groups', ['default']))
        all_ips.append(ip)
        worker_configs.append(f"{ip}:{groups}")
    
    for node in config['cluster']['api']['nodes']:
        ip = node['host']
        all_ips.append(ip)
        api_ips.append(ip)
    
    alert_ip = config['cluster']['alert']['nodes'][0]['host']
    all_ips.append(alert_ip)
    
    # Generate install_env.sh content for 3.2.0
    deployment_config = config['deployment']
    registry_config = config['registry']
    
    install_env = f"""#!/bin/bash
#
# Licensed to the Apache Software Foundation (ASF) under one or more
# contributor license agreements.  See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.
# The ASF licenses this file to You under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with
# the License.  You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

# ---------------------------------------------------------
# INSTALL MACHINE
# ---------------------------------------------------------
# A comma separated list of machine hostname or IP would be installed DolphinScheduler,
# including master, worker, api, alert. If you want to deploy in pseudo-distributed
# mode, just write a pseudo-distributed hostname
# Example for hostnames: ips="ds1,ds2,ds3,ds4,ds5", Example for IPs: ips="192.168.8.1,192.168.8.2,192.168.8.3,192.168.8.4,192.168.8.5"
ips="{','.join(all_ips)}"

# Port of SSH protocol, default value is 22. For now we only support same port in all `ips` machine
# modify it if you use different ssh port
sshPort="22"

# A comma separated list of machine hostname or IP would be installed Master server, it
# must be a subset of configuration `ips`.
# Example for hostnames: masters="ds1,ds2", Example for IPs: masters="192.168.8.1,192.168.8.2"
masters="{','.join(master_ips)}"

# A comma separated list of machine <hostname>:<workerGroup> or <IP>:<workerGroup>.All hostname or IP must be a
# subset of configuration `ips`, And workerGroup have default value as `default`, but we recommend you declare behind the hosts
# Example for hostnames: workers="ds1:default,ds2:default,ds3:default", Example for IPs: workers="192.168.8.1:default,192.168.8.2:default,192.168.8.3:default"
workers="{','.join(worker_configs)}"

# A comma separated list of machine hostname or IP would be installed Alert server, it
# must be a subset of configuration `ips`.
# Example for hostname: alertServer="ds3", Example for IP: alertServer="192.168.8.3"
alertServer="{alert_ip}"

# A comma separated list of machine hostname or IP would be installed API server, it
# must be a subset of configuration `ips`.
# Example for hostnames: apiServers="ds1", Example for IPs: apiServers="192.168.8.1"
apiServers="{','.join(api_ips)}"

# The directory to install DolphinScheduler for all machine we config above. It will automatically be created by `install.sh` script if not exists.
# Do not set this configuration same as the current path (pwd). Do not add quotes to it if you using related path.
installPath="{deployment_config['install_path']}"

# The user to deploy DolphinScheduler for all machine we config above. For now user must create by yourself before running `install.sh`
# script. The user needs to have sudo privileges and permissions to operate hdfs. If hdfs is enabled than the root directory needs
# to be created by this user
deployUser="{deployment_config['user']}"

# The root of zookeeper, for now DolphinScheduler default registry server is zookeeper.
zkRoot="{registry_config.get('namespace', '/dolphinscheduler')}"
"""
    
    return install_env


def generate_dolphinscheduler_env_v320(config):
    """
    Generate dolphinscheduler_env.sh for DolphinScheduler 3.2.x
    
    Args:
        config: Configuration dictionary
    
    Returns:
        Configuration content string
    """
    db_config = config['database']
    registry_config = config['registry']
    deployment_config = config.get('deployment', {})
    
    # Get Java home - 3.2.2 recommends Java 11
    java_home = deployment_config.get('java_home', '/usr/lib/jvm/java-11-openjdk-amd64')
    
    # Build database URL
    db_params = db_config.get('params', 'useUnicode=true&characterEncoding=UTF-8&useSSL=false&allowPublicKeyRetrieval=true')
    db_url = f"jdbc:mysql://{db_config['host']}:{db_config.get('port', 3306)}/{db_config['database']}?{db_params}"
    
    # Build Zookeeper connection string
    zk_connect = ','.join(registry_config['servers'])
    
    env_content = f"""#!/bin/bash
#
# DolphinScheduler Environment Configuration
#

# JAVA_HOME - DolphinScheduler 3.2.2 recommends Java 11
export JAVA_HOME={java_home}

# Database configuration
export DATABASE=mysql
export SPRING_PROFILES_ACTIVE=mysql
export SPRING_DATASOURCE_URL="{db_url}"
export SPRING_DATASOURCE_USERNAME={db_config['username']}
export SPRING_DATASOURCE_PASSWORD={db_config['password']}
"""
    
    return env_content


def generate_common_properties_v320(config):
    """
    Generate common.properties for DolphinScheduler 3.2.x
    
    This file configures resource storage. Critical for resource center functionality.
    Supports S3, HDFS, and LOCAL storage types.
    
    Note: For 3.2.2, Azure placeholder values are required even if not using Azure.
    
    Args:
        config: Configuration dictionary
    
    Returns:
        Configuration content string
    """
    storage_config = config.get('storage', {})
    storage_type = storage_config.get('type', 'LOCAL').upper()
    
    # Base path configuration
    properties_content = """# DolphinScheduler Common Properties
data.basedir.path=/tmp/dolphinscheduler

"""
    
    if storage_type == 'S3':
        # S3 storage configuration
        s3_config = storage_config.get('s3', {})
        azure_config = storage_config.get('azure', {})
        
        # Get S3 credentials - must be provided (IAM Role has bugs in DolphinScheduler 3.2.2)
        use_iam_role = s3_config.get('use_iam_role', False)
        access_key = s3_config.get('access_key_id', '')
        secret_key = s3_config.get('secret_access_key', '')
        
        # Validate credentials if not using IAM Role
        if not use_iam_role and (not access_key or not secret_key):
            logger.warning("S3 storage configured but access_key_id or secret_access_key is missing!")
            logger.warning("DolphinScheduler 3.2.2 has known issues with IAM Role, AK/SK is required.")
        
        properties_content += f"""# Resource Storage Configuration - S3
resource.storage.type=S3
resource.storage.upload.base.path={s3_config.get('upload_path', '/dolphinscheduler')}

resource.aws.access.key.id={access_key}
resource.aws.secret.access.key={secret_key}
resource.aws.region={s3_config.get('region', 'us-east-2')}
resource.aws.s3.bucket.name={s3_config.get('bucket', 'dolphinscheduler')}
resource.aws.s3.endpoint={s3_config.get('endpoint', f"https://s3.{s3_config.get('region', 'us-east-2')}.amazonaws.com")}

# Azure placeholder configuration (required even if not using Azure)
resource.azure.client.id={azure_config.get('client_id', 'placeholder')}
resource.azure.client.secret={azure_config.get('client_secret', 'placeholder')}
resource.azure.subId={azure_config.get('sub_id', 'placeholder')}
resource.azure.tenant.id={azure_config.get('tenant_id', 'placeholder')}
"""
    elif storage_type == 'HDFS':
        # HDFS storage configuration
        hdfs_config = storage_config.get('hdfs', {})
        namenode_host = hdfs_config.get('namenode_host', 'localhost')
        namenode_port = hdfs_config.get('namenode_port', 8020)
        hdfs_user = hdfs_config.get('user', 'hadoop')
        hdfs_path = hdfs_config.get('upload_path', '/dolphinscheduler')
        
        properties_content += f"""# Resource Storage Configuration - HDFS
resource.storage.type=HDFS
resource.storage.upload.base.path={hdfs_path}
resource.hdfs.root.user={hdfs_user}
resource.hdfs.fs.defaultFS=hdfs://{namenode_host}:{namenode_port}
"""
    else:
        # Default to LOCAL storage
        properties_content += """# Resource Storage Configuration - LOCAL
resource.storage.type=LOCAL
resource.storage.upload.base.path=/tmp/dolphinscheduler
"""
    
    return properties_content