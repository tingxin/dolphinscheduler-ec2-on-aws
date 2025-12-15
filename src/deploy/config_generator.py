"""
DolphinScheduler configuration file generators
"""
import tempfile
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


def generate_application_yaml_v320(config, component='master'):
    """
    Generate application.yaml for DolphinScheduler 3.2.0 component
    
    Args:
        config: Configuration dictionary
        component: Component name (master, worker, api, alert)
    
    Returns:
        Configuration content string (YAML format)
    """
    db_config = config['database']
    registry_config = config['registry']
    service_config = config.get('service_config', {}).get(component, {})
    
    # Build database URL with 3.2.0 compatible parameters
    db_params = "useUnicode=true&characterEncoding=UTF-8&useSSL=false&allowPublicKeyRetrieval=true"
    db_url = f"jdbc:mysql://{db_config['host']}:{db_config.get('port', 3306)}/{db_config['database']}?{db_params}"
    
    # Build Zookeeper connection string
    zk_connect = ','.join(registry_config['servers'])
    
    # Base configuration for components that need database access
    if component in ['master', 'api', 'alert']:
        yaml_content = f"""spring:
  profiles:
    active: mysql
  banner:
    charset: UTF-8
  jackson:
    time-zone: UTC
    date-format: "yyyy-MM-dd HH:mm:ss"
  datasource:
    driver-class-name: com.mysql.cj.jdbc.Driver
    url: {db_url}
    username: {db_config['username']}
    password: {db_config['password']}
    hikari:
      connection-test-query: select 1
      pool-name: DolphinScheduler
      minimum-idle: 5
      maximum-pool-size: 50
      auto-commit: true
      idle-timeout: 600000
      pool-prepared-statements: true
      max-prepared-statements-per-connection: 20
      connection-timeout: 30000
      connection-init-sql: SELECT 1
      validation-timeout: 3000

"""
    else:
        # Worker doesn't need database configuration in 3.2.0
        yaml_content = f"""spring:
  profiles:
    active: mysql
  banner:
    charset: UTF-8
  jackson:
    time-zone: UTC
    date-format: "yyyy-MM-dd HH:mm:ss"

"""
    
    # Add registry configuration for all components
    yaml_content += f"""registry:
  type: {registry_config['type']}
  zookeeper:
    namespace: {registry_config.get('namespace', 'dolphinscheduler')}
    connect-string: {zk_connect}
    retry-policy:
      base-sleep-time: {registry_config.get('retry', {}).get('base_sleep_time', 1000)}ms
      max-sleep: {registry_config.get('retry', {}).get('max_sleep_time', 3000)}ms
      max-retries: {registry_config.get('retry', {}).get('max_retries', 5)}
    session-timeout: {registry_config.get('session_timeout', 60000)}ms
    connection-timeout: {registry_config.get('connection_timeout', 30000)}ms

"""
    
    # Add resource storage configuration for all components
    storage_config = config.get('storage', {})
    storage_type = storage_config.get('type', 'LOCAL').upper()
    
    if storage_type == 'S3':
        yaml_content += f"""# Resource Storage Configuration
resource-storage:
  type: S3
  s3:
    region: {storage_config.get('region', 'us-east-2')}
    bucket-name: {storage_config.get('bucket', 'dolphinscheduler')}
    folder: {storage_config.get('upload_path', '/dolphinscheduler')}
    access-key-id: {storage_config.get('access_key_id', '')}
    secret-access-key: {storage_config.get('secret_access_key', '')}
    endpoint: {storage_config.get('endpoint', f"https://s3.{storage_config.get('region', 'us-east-2')}.amazonaws.com")}

"""
    elif storage_type == 'HDFS':
        hdfs_config = storage_config.get('hdfs', {})
        namenode_host = hdfs_config.get('namenode_host', 'localhost')
        namenode_port = hdfs_config.get('namenode_port', 8020)
        hdfs_user = hdfs_config.get('user', 'hadoop')
        hdfs_path = hdfs_config.get('upload_path', '/dolphinscheduler')
        
        yaml_content += f"""# Resource Storage Configuration
resource-storage:
  type: HDFS
  hdfs:
    fs-default-name: hdfs://{namenode_host}:{namenode_port}
    resource-upload-path: {hdfs_path}
    hadoop-security-authentication: simple
    hadoop.security.authentication: simple
    hadoop.security.authorization: false
    hadoop.user.name: {hdfs_user}

"""
    else:
        # Default to local storage if not configured
        yaml_content += f"""# Resource Storage Configuration
resource-storage:
  type: LOCAL
  local:
    base-dir: /opt/dolphinscheduler/resources

"""
    
    # Add component-specific configuration for 3.2.0
    if component == 'master':
        max_cpu_load = service_config.get('max_cpu_load_avg', 3)
        reserved_memory = service_config.get('reserved_memory', 0.1)
        max_waiting_time = service_config.get('max_waiting_time', '150s')
        
        yaml_content += f"""master:
  listen-port: 5678
  max-cpu-load-avg: {max_cpu_load}
  reserved-memory: {reserved_memory}
  max-waiting-time: {max_waiting_time}
  heartbeat-interval: 10s
  task-commit-retry-times: 5
  task-commit-interval: 1000
  state-wheel-interval: 5s
  process-task-cleanup-time: 120s

"""
    
    elif component == 'worker':
        max_cpu_load = service_config.get('max_cpu_load_avg', 3)
        reserved_memory = service_config.get('reserved_memory', 0.1)
        max_waiting_time = service_config.get('max_waiting_time', '150s')
        
        yaml_content += f"""worker:
  listen-port: 1234
  max-cpu-load-avg: {max_cpu_load}
  reserved-memory: {reserved_memory}
  max-waiting-time: {max_waiting_time}
  heartbeat-interval: 10s
  host-weight: 100
  tenant-auto-create: true
  exec-threads: 100

"""
    
    elif component == 'api':
        api_port = service_config.get('port', 12345)
        yaml_content += f"""server:
  port: {api_port}
  servlet:
    session:
      timeout: 7200s
    context-path: /dolphinscheduler
  compression:
    enabled: true
    mime-types: text/html,text/xml,text/plain,text/css,text/javascript,application/javascript,application/json,application/xml
  jetty:
    max-http-post-size: 5000000

management:
  endpoints:
    web:
      exposure:
        include: health,metrics,prometheus
  endpoint:
    health:
      enabled: true
      show-details: always
  health:
    db:
      enabled: true
    defaults:
      enabled: false

"""
    
    elif component == 'alert':
        yaml_content += f"""server:
  port: 50052

alert:
  port: 50052
  wait-timeout: 5000

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
    Generate dolphinscheduler_env.sh for DolphinScheduler 3.2.0
    
    Args:
        config: Configuration dictionary
    
    Returns:
        Configuration content string
    """
    db_config = config['database']
    registry_config = config['registry']
    
    # Build database URL with 3.2.0 compatible parameters
    db_params = "useUnicode=true&characterEncoding=UTF-8&useSSL=false&allowPublicKeyRetrieval=true"
    db_url = f"jdbc:mysql://{db_config['host']}:{db_config.get('port', 3306)}/{db_config['database']}?{db_params}"
    
    # Build Zookeeper connection string
    zk_connect = ','.join(registry_config['servers'])
    
    env_content = f"""#!/bin/bash
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

# JAVA_HOME, will use it to start DolphinScheduler server
export JAVA_HOME=${{JAVA_HOME:-/usr/lib/jvm/java-1.8.0}}

# Database related configuration, set database type, username and password
export DATABASE=${{DATABASE:-mysql}}
export SPRING_PROFILES_ACTIVE=${{DATABASE}}
export SPRING_DATASOURCE_URL="{db_url}"
export SPRING_DATASOURCE_USERNAME={db_config['username']}
export SPRING_DATASOURCE_PASSWORD={db_config['password']}

# DolphinScheduler server related configuration
export SPRING_CACHE_TYPE=${{SPRING_CACHE_TYPE:-none}}
export SPRING_JACKSON_TIME_ZONE=${{SPRING_JACKSON_TIME_ZONE:-UTC}}
export MASTER_FETCH_COMMAND_NUM=${{MASTER_FETCH_COMMAND_NUM:-10}}

# Registry center configuration, determines the type and link of the registry center
export REGISTRY_TYPE=${{REGISTRY_TYPE:-zookeeper}}
export REGISTRY_ZOOKEEPER_CONNECT_STRING=${{REGISTRY_ZOOKEEPER_CONNECT_STRING:-{zk_connect}}}

# Tasks related configurations, need to change the configuration if you use the related tasks.
export HADOOP_HOME=${{HADOOP_HOME:-/opt/soft/hadoop}}
export HADOOP_CONF_DIR=${{HADOOP_CONF_DIR:-/opt/soft/hadoop/etc/hadoop}}
export SPARK_HOME=${{SPARK_HOME:-/opt/soft/spark}}
export PYTHON_LAUNCHER=${{PYTHON_LAUNCHER:-/usr/bin/python}}
export HIVE_HOME=${{HIVE_HOME:-/opt/soft/hive}}
export FLINK_HOME=${{FLINK_HOME:-/opt/soft/flink}}
export DATAX_LAUNCHER=${{DATAX_LAUNCHER:-/opt/soft/datax/bin/datax.py}}

export PATH=$HADOOP_HOME/bin:$SPARK_HOME/bin:$PYTHON_LAUNCHER:$JAVA_HOME/bin:$HIVE_HOME/bin:$FLINK_HOME/bin:$DATAX_LAUNCHER:$PATH
"""
    
    return env_content


def generate_common_properties_v320(config):
    """
    Generate common.properties for DolphinScheduler 3.2.0
    
    This file configures resource storage, data source, and other common settings.
    Critical for resource center functionality.
    
    Args:
        config: Configuration dictionary
    
    Returns:
        Configuration content string
    """
    storage_config = config.get('storage', {})
    
    # Determine resource storage type and configuration
    storage_type = storage_config.get('type', 'LOCAL').upper()
    
    if storage_type == 'S3':
        # S3 storage configuration
        resource_storage_config = f"""# Resource Storage Configuration - S3
resource.storage.type=S3
resource.aws.region={storage_config.get('region', 'us-east-2')}
resource.aws.s3.bucket.name={storage_config.get('bucket', 'dolphinscheduler')}
resource.aws.s3.upload.folder={storage_config.get('upload_path', '/dolphinscheduler')}
resource.aws.access.key.id={storage_config.get('access_key_id', '')}
resource.aws.secret.access.key={storage_config.get('secret_access_key', '')}
resource.aws.s3.endpoint={storage_config.get('endpoint', f"https://s3.{storage_config.get('region', 'us-east-2')}.amazonaws.com")}
"""
    elif storage_type == 'HDFS':
        # HDFS storage configuration
        hdfs_config = storage_config.get('hdfs', {})
        namenode_host = hdfs_config.get('namenode_host', 'localhost')
        namenode_port = hdfs_config.get('namenode_port', 8020)
        hdfs_user = hdfs_config.get('user', 'hadoop')
        hdfs_path = hdfs_config.get('upload_path', '/dolphinscheduler')
        
        resource_storage_config = f"""# Resource Storage Configuration - HDFS
resource.storage.type=HDFS
resource.hdfs.fs.defaultFS=hdfs://{namenode_host}:{namenode_port}
resource.hdfs.path.prefix={hdfs_path}
resource.hdfs.username={hdfs_user}
resource.hdfs.kerberos.authentication.enable=false
resource.hdfs.resource.upload.path={hdfs_path}
"""
    else:
        # Default to LOCAL storage
        resource_storage_config = """# Resource Storage Configuration - LOCAL
resource.storage.type=LOCAL
resource.local.basedir=/tmp/dolphinscheduler
"""
    
    # Build common.properties content
    properties_content = f"""#
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

# ============================================================================
# DolphinScheduler Common Configuration
# ============================================================================

{resource_storage_config}

# Data source configuration
datasource.driver.class.name=com.mysql.cj.jdbc.Driver
datasource.url=jdbc:mysql://{config['database']['host']}:{config['database'].get('port', 3306)}/{config['database']['database']}?useUnicode=true&characterEncoding=UTF-8&useSSL=false&allowPublicKeyRetrieval=true
datasource.username={config['database']['username']}
datasource.password={config['database']['password']}

# Connection pool configuration
datasource.hikari.connection-test-query=select 1
datasource.hikari.pool-name=DolphinScheduler
datasource.hikari.minimum-idle=5
datasource.hikari.maximum-pool-size=50
datasource.hikari.auto-commit=true
datasource.hikari.idle-timeout=600000
datasource.hikari.pool-prepared-statements=true
datasource.hikari.max-prepared-statements-per-connection=20
datasource.hikari.connection-timeout=30000
datasource.hikari.validation-timeout=3000

# Cache configuration
spring.cache.type=none

# Jackson configuration
spring.jackson.time-zone=UTC
spring.jackson.date-format=yyyy-MM-dd HH:mm:ss

# Registry center configuration
registry.type=zookeeper
registry.zookeeper.namespace={config['registry'].get('namespace', 'dolphinscheduler')}
registry.zookeeper.connect-string={','.join(config['registry']['servers'])}
registry.zookeeper.retry-policy.base-sleep-time={config['registry'].get('retry', {}).get('base_sleep_time', 1000)}
registry.zookeeper.retry-policy.max-sleep={config['registry'].get('retry', {}).get('max_sleep_time', 3000)}
registry.zookeeper.retry-policy.max-retries={config['registry'].get('retry', {}).get('max_retries', 5)}
registry.zookeeper.session-timeout={config['registry'].get('session_timeout', 60000)}
registry.zookeeper.connection-timeout={config['registry'].get('connection_timeout', 30000)}

# Server configuration
server.port=12345
server.servlet.context-path=/dolphinscheduler

# Logging configuration
logging.level.root=INFO
logging.level.org.apache.dolphinscheduler=INFO

# Task execution configuration
task.execute.threads=100
task.dispatch.task.number=3
task.commit.retry.times=5
task.commit.interval=1000

# Master configuration
master.listen.port=5678
master.exec.threads=100
master.dispatch.task.number=3
master.host.selector=LowerWeight
master.heartbeat.interval=10
master.task.commit.retry.times=5
master.task.commit.interval=1000
master.max.cpu.load.avg=-1
master.reserved.memory=0.3

# Worker configuration
worker.listen.port=1234
worker.exec.threads=100
worker.heartbeat.interval=10
worker.max.cpu.load.avg=-1
worker.reserved.memory=0.3
worker.exec.path=/tmp/dolphinscheduler/exec

# Alert configuration
alert.port=50052
alert.wait.timeout=5000

# Python gateway configuration
python-gateway.enabled=true
python-gateway.gateway-server.address=0.0.0.0
python-gateway.gateway-server.port=25333
python-gateway.python-path=/usr/bin/python

# File upload configuration
file.upload.max-size=1073741824

# Tenant configuration
tenant.auto.create=true

# Other configurations
server.compression.enabled=true
server.compression.mime-types=text/html,text/xml,text/plain,text/css,text/javascript,application/javascript,application/json,application/xml
"""
    
    return properties_content