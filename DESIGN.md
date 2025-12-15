# DolphinScheduler EC2 集群部署 CLI 工具技术设计文档

## 1. 项目概述

### 1.1 目标
构建一个 Python CLI 工具，用于在 AWS EC2 上自动化部署、管理 DolphinScheduler 3.2.0 集群。

### 1.2 核心功能
- `create`: 创建集群（包括 EC2 实例创建、软件安装、配置部署）
- `delete`: 删除集群（清理所有资源）
- `scale`: 扩缩容集群（动态调整节点数量）

### 1.3 技术栈
- **语言**: Python 3.12
- **AWS SDK**: boto3
- **CLI 框架**: Click
- **配置解析**: PyYAML
- **SSH 操作**: Paramiko
- **日志**: logging

## 2. 架构设计

### 2.1 模块划分

```
dolphinscheduler-ec2-on-aws/
├── cli.py                    # CLI 入口
├── config.yaml               # 配置文件
├── requirements.txt          # Python 依赖
├── src/
│   ├── __init__.py
│   ├── config.py            # 配置加载和验证
│   ├── aws/
│   │   ├── __init__.py
│   │   ├── ec2.py           # EC2 实例管理
│   │   ├── vpc.py           # VPC/子网/安全组管理
│   │   ├── elb.py           # 负载均衡器管理
│   │   └── iam.py           # IAM 角色管理
│   ├── deploy/
│   │   ├── __init__.py
│   │   ├── installer.py     # DolphinScheduler 安装
│   │   ├── ssh.py           # SSH 连接和命令执行
│   │   └── templates.py     # 配置文件模板
│   ├── commands/
│   │   ├── __init__.py
│   │   ├── create.py        # create 命令实现
│   │   ├── delete.py        # delete 命令实现
│   │   └── scale.py         # scale 命令实现
│   └── utils/
│       ├── __init__.py
│       ├── logger.py        # 日志工具
│       └── validator.py     # 配置验证
└── tests/
    └── ...
```


## 3. 部署流程设计

### 3.1 CREATE 命令流程

```
1. 配置验证
   ├── 加载 config.yaml
   ├── 验证必填字段
   ├── 验证 AWS 凭证
   └── 验证网络配置

2. AWS 资源创建
   ├── 验证 VPC/子网/安全组存在
   ├── 创建 EC2 实例（跨可用区）
   │   ├── Master 节点（2-3 个，分布在不同 AZ）
   │   ├── Worker 节点（3+ 个，均匀分布）
   │   ├── API 节点（2+ 个，分布在不同 AZ）
   │   └── Alert 节点（1 个）
   ├── 等待实例启动
   ├── 分配/关联弹性 IP（可选）
   └── 创建 ALB（如果启用）

3. 节点初始化
   ├── 等待 SSH 可用
   ├── 安装系统依赖
   │   ├── Java JDK 1.8
   │   ├── MySQL 客户端
   │   ├── psmisc
   │   └── Python3（可选）
   ├── 创建部署用户
   ├── 配置 SSH 免密登录
   └── 配置主机名解析

4. DolphinScheduler 部署
   ├── 下载安装包
   ├── 生成 install_config.conf
   │   ├── 数据库配置
   │   ├── Zookeeper 配置
   │   ├── S3 存储配置
   │   └── 节点分配
   ├── 初始化数据库（仅首次）
   ├── 分发安装包到所有节点
   ├── 执行安装脚本
   └── 启动服务

5. 验证和健康检查
   ├── 检查所有服务状态
   ├── 验证 Master-Worker 连接
   ├── 验证 API 可访问性
   └── 输出访问信息
```

### 3.2 DELETE 命令流程

```
1. 确认删除操作
   └── 用户二次确认

2. 停止服务
   ├── 连接所有节点
   ├── 执行 stop-all.sh
   └── 等待服务停止

3. 清理 AWS 资源
   ├── 删除 ALB（如果存在）
   ├── 删除目标组
   ├── 终止 EC2 实例
   ├── 释放弹性 IP（如果有）
   └── 删除相关标签

4. 可选清理
   ├── 清理 RDS 数据（可选）
   └── 清理 S3 数据（可选）
```

### 3.3 SCALE 命令流程

```
1. 解析扩缩容参数
   ├── 组件类型（master/worker/api）
   ├── 目标数量
   └── 可用区分布策略

2. 扩容流程
   ├── 创建新 EC2 实例
   ├── 初始化新节点
   ├── 更新配置文件
   ├── 重新分发配置
   └── 启动新节点服务

3. 缩容流程
   ├── 选择要删除的节点
   ├── 停止节点服务
   ├── 从集群中移除
   ├── 终止 EC2 实例
   └── 更新配置文件

4. 验证
   └── 检查集群状态
```


## 4. 关键技术点和注意事项

### 4.1 AWS 资源管理

#### 4.1.1 EC2 实例创建
**注意点：**
- 使用 `boto3.resource('ec2')` 创建实例
- 必须指定 `SubnetId` 而不是 `AvailabilityZone`，以确保跨 AZ 部署
- 使用 `IamInstanceProfile` 而不是硬编码 AWS 凭证
- 设置合适的 `Tags` 便于资源管理和成本追踪
- 使用 `waiter` 等待实例状态变为 `running`

**代码示例：**
```python
# 创建实例时指定子网（自动确定 AZ）
instance = ec2.create_instances(
    ImageId=ami_id,
    InstanceType=instance_type,
    MinCount=1,
    MaxCount=1,
    KeyName=key_name,
    SecurityGroupIds=[sg_id],
    SubnetId=subnet_id,  # 关键：指定子网
    IamInstanceProfile={'Name': iam_profile},
    TagSpecifications=[{
        'ResourceType': 'instance',
        'Tags': [
            {'Key': 'Name', 'Value': f'ds-{component}-{index}'},
            {'Key': 'Component', 'Value': component},
            {'Key': 'Cluster', 'Value': cluster_name}
        ]
    }]
)

# 等待实例启动
instance[0].wait_until_running()
instance[0].reload()  # 刷新获取公网 IP
```

#### 4.1.2 跨可用区部署策略
**注意点：**
- Master 节点必须分布在至少 2 个不同的 AZ
- Worker 节点应均匀分布在所有配置的 AZ
- API 节点应分布在不同 AZ，配合 ALB 实现高可用
- 使用轮询算法分配节点到不同子网

**代码示例：**
```python
def distribute_nodes_across_azs(count, subnets):
    """将节点均匀分布到不同可用区"""
    distribution = []
    for i in range(count):
        subnet = subnets[i % len(subnets)]
        distribution.append({
            'index': i,
            'subnet_id': subnet['subnet_id'],
            'az': subnet['availability_zone']
        })
    return distribution
```

#### 4.1.3 ALB 配置
**注意点：**
- ALB 必须配置在公有子网
- 必须跨至少 2 个可用区
- 目标组的健康检查路径必须正确
- 注册目标时使用实例 ID，不要使用 IP

**代码示例：**
```python
# 创建 ALB
alb = elbv2.create_load_balancer(
    Name='dolphinscheduler-alb',
    Subnets=[subnet1, subnet2],  # 公有子网
    SecurityGroups=[alb_sg],
    Scheme='internet-facing',
    Type='application'
)

# 创建目标组
target_group = elbv2.create_target_group(
    Name='ds-api-tg',
    Protocol='HTTP',
    Port=12345,
    VpcId=vpc_id,
    HealthCheckPath='/dolphinscheduler/actuator/health',
    HealthCheckIntervalSeconds=30
)

# 注册目标
elbv2.register_targets(
    TargetGroupArn=target_group_arn,
    Targets=[{'Id': instance_id} for instance_id in api_instances]
)
```

### 4.2 SSH 连接和远程执行

#### 4.2.1 SSH 连接管理
**注意点：**
- 使用 Paramiko 建立 SSH 连接
- 实现连接重试机制（实例启动后 SSH 服务需要时间）
- 使用 SSH 密钥而不是密码
- 保持连接池，避免频繁建立连接
- 正确处理 SSH 超时和异常

**代码示例：**
```python
import paramiko
import time

def wait_for_ssh(host, key_file, max_retries=30, retry_interval=10):
    """等待 SSH 服务可用"""
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    for i in range(max_retries):
        try:
            ssh.connect(
                hostname=host,
                username='ec2-user',
                key_filename=key_file,
                timeout=10
            )
            ssh.close()
            return True
        except Exception as e:
            if i < max_retries - 1:
                time.sleep(retry_interval)
            else:
                raise Exception(f"SSH 连接失败: {host}")
    return False

def execute_remote_command(ssh, command, sudo=False):
    """执行远程命令"""
    if sudo:
        command = f"sudo bash -c '{command}'"
    
    stdin, stdout, stderr = ssh.exec_command(command)
    exit_code = stdout.channel.recv_exit_status()
    
    output = stdout.read().decode('utf-8')
    error = stderr.read().decode('utf-8')
    
    if exit_code != 0:
        raise Exception(f"命令执行失败: {error}")
    
    return output
```

#### 4.2.2 文件传输
**注意点：**
- 使用 SFTP 传输大文件（如 DolphinScheduler 安装包）
- 显示传输进度
- 验证文件完整性（MD5/SHA256）
- 处理网络中断和重传

**代码示例：**
```python
def upload_file(ssh, local_path, remote_path, show_progress=True):
    """上传文件到远程服务器"""
    sftp = ssh.open_sftp()
    
    if show_progress:
        file_size = os.path.getsize(local_path)
        transferred = [0]
        
        def progress_callback(transferred_bytes, total_bytes):
            transferred[0] = transferred_bytes
            percent = (transferred_bytes / total_bytes) * 100
            print(f"\r上传进度: {percent:.1f}%", end='')
        
        sftp.put(local_path, remote_path, callback=progress_callback)
        print()  # 换行
    else:
        sftp.put(local_path, remote_path)
    
    sftp.close()
```

### 4.3 DolphinScheduler 安装配置

#### 4.3.1 配置文件生成
**注意点：**
- 根据 config.yaml 动态生成 `install_config.conf`
- 正确处理节点 IP 列表格式
- Worker 节点需要指定分组
- 数据库连接字符串需要正确转义
- Zookeeper 地址格式必须正确

**代码示例：**
```python
def generate_install_config(config):
    """生成 DolphinScheduler 安装配置"""
    
    # 收集所有节点 IP
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
    
    # 生成配置内容
    install_config = f"""
# 数据库配置
DATABASE_TYPE={config['database']['type']}
SPRING_DATASOURCE_URL="jdbc:mysql://{config['database']['host']}:{config['database']['port']}/{config['database']['database']}?{config['database']['params']}"
SPRING_DATASOURCE_USERNAME={config['database']['username']}
SPRING_DATASOURCE_PASSWORD={config['database']['password']}

# Zookeeper 配置
REGISTRY_TYPE={config['registry']['type']}
REGISTRY_ZOOKEEPER_CONNECT_STRING="{','.join(config['registry']['servers'])}"

# 资源存储配置
RESOURCE_STORAGE_TYPE={config['storage']['type']}
RESOURCE_UPLOAD_PATH={config['storage']['upload_path']}
AWS_REGION={config['storage']['region']}
RESOURCE_STORAGE_BUCKET_NAME={config['storage']['bucket']}

# 节点配置
ips="{','.join(all_ips)}"
masters="{','.join(master_ips)}"
workers="{','.join(worker_configs)}"
apiServers="{','.join(api_ips)}"
alertServer="{alert_ip}"

# 部署配置
deployUser="{config['deployment']['user']}"
installPath="{config['deployment']['install_path']}"
"""
    
    return install_config
```

#### 4.3.2 数据库初始化
**注意点：**
- 仅在首次部署时初始化数据库
- 检查数据库是否已存在表结构
- 使用事务确保初始化原子性
- 记录初始化日志

**代码示例：**
```python
import pymysql

def check_database_initialized(db_config):
    """检查数据库是否已初始化"""
    try:
        conn = pymysql.connect(
            host=db_config['host'],
            port=db_config['port'],
            user=db_config['username'],
            password=db_config['password'],
            database=db_config['database']
        )
        cursor = conn.cursor()
        cursor.execute("SHOW TABLES LIKE 't_ds_version'")
        result = cursor.fetchone()
        conn.close()
        return result is not None
    except Exception as e:
        return False

def initialize_database(ssh, install_path):
    """初始化数据库"""
    command = f"cd {install_path} && bash bin/upgrade-schema.sh"
    execute_remote_command(ssh, command)
```

#### 4.3.3 服务启动顺序
**注意点：**
- 必须按顺序启动：Master -> Worker -> API -> Alert
- 每个服务启动后等待健康检查通过
- 使用 `dolphinscheduler-daemon.sh` 而不是直接启动
- 检查进程是否真正启动（不只是脚本执行成功）

**代码示例：**
```python
def start_services(config):
    """按顺序启动所有服务"""
    install_path = config['deployment']['install_path']
    
    # 1. 启动 Master
    for node in config['cluster']['master']['nodes']:
        ssh = connect_ssh(node['host'])
        execute_remote_command(
            ssh, 
            f"cd {install_path} && bash bin/dolphinscheduler-daemon.sh start master-server"
        )
        wait_for_service_ready(node['host'], 5678)
    
    # 2. 启动 Worker
    for node in config['cluster']['worker']['nodes']:
        ssh = connect_ssh(node['host'])
        execute_remote_command(
            ssh,
            f"cd {install_path} && bash bin/dolphinscheduler-daemon.sh start worker-server"
        )
        wait_for_service_ready(node['host'], 1234)
    
    # 3. 启动 API
    for node in config['cluster']['api']['nodes']:
        ssh = connect_ssh(node['host'])
        execute_remote_command(
            ssh,
            f"cd {install_path} && bash bin/dolphinscheduler-daemon.sh start api-server"
        )
        wait_for_service_ready(node['host'], 12345)
    
    # 4. 启动 Alert
    alert_node = config['cluster']['alert']['nodes'][0]
    ssh = connect_ssh(alert_node['host'])
    execute_remote_command(
        ssh,
        f"cd {install_path} && bash bin/dolphinscheduler-daemon.sh start alert-server"
    )

def wait_for_service_ready(host, port, max_retries=30):
    """等待服务端口可用"""
    import socket
    for i in range(max_retries):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex((host, port))
            sock.close()
            if result == 0:
                return True
        except:
            pass
        time.sleep(10)
    raise Exception(f"服务启动超时: {host}:{port}")
```


### 4.4 配置验证

#### 4.4.1 配置文件验证
**注意点：**
- 在执行任何操作前验证配置完整性
- 验证 AWS 凭证有效性
- 验证网络资源存在性（VPC、子网、安全组）
- 验证数据库连接
- 验证 Zookeeper 连接
- 验证 S3 bucket 访问权限

**代码示例：**
```python
def validate_config(config):
    """验证配置文件"""
    errors = []
    
    # 验证必填字段
    required_fields = [
        'database.host',
        'database.username',
        'database.password',
        'registry.servers',
        'storage.bucket',
        'aws.region',
        'aws.vpc_id',
        'aws.subnets'
    ]
    
    for field in required_fields:
        if not get_nested_value(config, field):
            errors.append(f"缺少必填字段: {field}")
    
    # 验证节点数量
    if config['cluster']['master']['count'] < 2:
        errors.append("Master 节点数量至少为 2")
    
    if config['cluster']['api']['count'] < 2:
        errors.append("API 节点数量至少为 2")
    
    # 验证可用区分布
    master_azs = set(node['availability_zone'] for node in config['cluster']['master']['nodes'])
    if len(master_azs) < 2:
        errors.append("Master 节点必须分布在至少 2 个可用区")
    
    if errors:
        raise ValueError("配置验证失败:\n" + "\n".join(errors))
    
    return True

def validate_aws_resources(config):
    """验证 AWS 资源存在性"""
    ec2 = boto3.client('ec2', region_name=config['aws']['region'])
    
    # 验证 VPC
    try:
        ec2.describe_vpcs(VpcIds=[config['aws']['vpc_id']])
    except:
        raise ValueError(f"VPC 不存在: {config['aws']['vpc_id']}")
    
    # 验证子网
    subnet_ids = [s['subnet_id'] for s in config['aws']['subnets']]
    try:
        response = ec2.describe_subnets(SubnetIds=subnet_ids)
        if len(response['Subnets']) != len(subnet_ids):
            raise ValueError("部分子网不存在")
    except:
        raise ValueError("子网验证失败")
    
    # 验证安全组
    sg_ids = list(config['aws']['security_groups'].values())
    try:
        response = ec2.describe_security_groups(GroupIds=sg_ids)
        if len(response['SecurityGroups']) != len(sg_ids):
            raise ValueError("部分安全组不存在")
    except:
        raise ValueError("安全组验证失败")

def validate_database_connection(db_config):
    """验证数据库连接"""
    try:
        conn = pymysql.connect(
            host=db_config['host'],
            port=db_config['port'],
            user=db_config['username'],
            password=db_config['password'],
            database=db_config['database'],
            connect_timeout=10
        )
        conn.close()
        return True
    except Exception as e:
        raise ValueError(f"数据库连接失败: {str(e)}")

def validate_zookeeper_connection(zk_servers):
    """验证 Zookeeper 连接"""
    from kazoo.client import KazooClient
    
    zk = KazooClient(hosts=','.join(zk_servers))
    try:
        zk.start(timeout=10)
        zk.stop()
        return True
    except Exception as e:
        raise ValueError(f"Zookeeper 连接失败: {str(e)}")

def validate_s3_access(storage_config):
    """验证 S3 访问权限"""
    s3 = boto3.client('s3', region_name=storage_config['region'])
    
    try:
        # 测试列举 bucket
        s3.list_objects_v2(Bucket=storage_config['bucket'], MaxKeys=1)
        
        # 测试写入权限
        test_key = f"{storage_config['upload_path']}/test.txt"
        s3.put_object(Bucket=storage_config['bucket'], Key=test_key, Body=b'test')
        s3.delete_object(Bucket=storage_config['bucket'], Key=test_key)
        
        return True
    except Exception as e:
        raise ValueError(f"S3 访问失败: {str(e)}")
```

### 4.5 错误处理和回滚

#### 4.5.1 错误处理策略
**注意点：**
- 每个步骤都要有明确的错误处理
- 记录详细的错误日志
- 提供有意义的错误信息给用户
- 区分可恢复错误和不可恢复错误

**代码示例：**
```python
class DeploymentError(Exception):
    """部署错误基类"""
    pass

class AWSResourceError(DeploymentError):
    """AWS 资源错误"""
    pass

class SSHConnectionError(DeploymentError):
    """SSH 连接错误"""
    pass

class ServiceStartError(DeploymentError):
    """服务启动错误"""
    pass

def safe_execute(func, error_message, rollback_func=None):
    """安全执行函数，带错误处理和回滚"""
    try:
        return func()
    except Exception as e:
        logger.error(f"{error_message}: {str(e)}")
        if rollback_func:
            try:
                rollback_func()
            except Exception as rollback_error:
                logger.error(f"回滚失败: {str(rollback_error)}")
        raise DeploymentError(error_message)
```

#### 4.5.2 回滚机制
**注意点：**
- 记录每个步骤的状态
- 失败时自动回滚已创建的资源
- 提供手动回滚选项
- 确保回滚操作的幂等性

**代码示例：**
```python
class DeploymentState:
    """部署状态管理"""
    def __init__(self):
        self.created_instances = []
        self.created_alb = None
        self.created_target_groups = []
        self.initialized_nodes = []
    
    def add_instance(self, instance_id):
        self.created_instances.append(instance_id)
    
    def rollback(self):
        """回滚所有已创建的资源"""
        ec2 = boto3.client('ec2')
        elbv2 = boto3.client('elbv2')
        
        # 删除 ALB
        if self.created_alb:
            try:
                elbv2.delete_load_balancer(LoadBalancerArn=self.created_alb)
                logger.info(f"已删除 ALB: {self.created_alb}")
            except Exception as e:
                logger.error(f"删除 ALB 失败: {e}")
        
        # 删除目标组
        for tg_arn in self.created_target_groups:
            try:
                elbv2.delete_target_group(TargetGroupArn=tg_arn)
                logger.info(f"已删除目标组: {tg_arn}")
            except Exception as e:
                logger.error(f"删除目标组失败: {e}")
        
        # 终止实例
        if self.created_instances:
            try:
                ec2.terminate_instances(InstanceIds=self.created_instances)
                logger.info(f"已终止实例: {self.created_instances}")
            except Exception as e:
                logger.error(f"终止实例失败: {e}")

def create_cluster_with_rollback(config):
    """创建集群（带回滚）"""
    state = DeploymentState()
    
    try:
        # 步骤 1: 创建实例
        instances = create_ec2_instances(config)
        for instance in instances:
            state.add_instance(instance['InstanceId'])
        
        # 步骤 2: 初始化节点
        initialize_nodes(instances)
        
        # 步骤 3: 部署软件
        deploy_dolphinscheduler(config, instances)
        
        # 步骤 4: 创建 ALB
        if config['service_config']['api']['load_balancer']['enabled']:
            alb_arn = create_alb(config, instances)
            state.created_alb = alb_arn
        
        return instances
        
    except Exception as e:
        logger.error(f"部署失败: {str(e)}")
        logger.info("开始回滚...")
        state.rollback()
        raise
```

### 4.6 扩缩容实现

#### 4.6.1 扩容流程
**注意点：**
- 新节点必须使用相同的配置
- 保持可用区均衡分布
- 更新 DolphinScheduler 配置文件
- 无需重启现有节点（Worker 扩容）
- Master/API 扩容需要更新配置并重启

**代码示例：**
```python
def scale_out_workers(config, additional_count):
    """扩容 Worker 节点"""
    current_workers = config['cluster']['worker']['nodes']
    subnets = config['aws']['subnets']
    
    # 计算新节点的可用区分布
    new_nodes = []
    for i in range(additional_count):
        subnet = subnets[len(current_workers + new_nodes) % len(subnets)]
        
        # 创建实例
        instance = create_ec2_instance(
            instance_type=config['cluster']['worker']['instance_type'],
            subnet_id=subnet['subnet_id'],
            security_group=config['aws']['security_groups']['worker'],
            component='worker'
        )
        
        new_nodes.append({
            'host': instance.private_ip_address,
            'instance_id': instance.id,
            'subnet_id': subnet['subnet_id'],
            'availability_zone': subnet['availability_zone'],
            'groups': ['default']
        })
    
    # 初始化新节点
    for node in new_nodes:
        initialize_node(node)
    
    # 更新配置文件
    config['cluster']['worker']['nodes'].extend(new_nodes)
    config['cluster']['worker']['count'] += additional_count
    
    # 在新节点上部署 Worker
    for node in new_nodes:
        deploy_worker(config, node)
    
    # 启动 Worker 服务
    for node in new_nodes:
        start_worker_service(node)
    
    return new_nodes

def scale_out_masters(config, additional_count):
    """扩容 Master 节点（需要重启集群）"""
    # Master 扩容需要更新所有节点的配置
    new_nodes = create_and_initialize_nodes(
        config, 'master', additional_count
    )
    
    # 更新配置
    config['cluster']['master']['nodes'].extend(new_nodes)
    config['cluster']['master']['count'] += additional_count
    
    # 重新生成配置文件
    regenerate_install_config(config)
    
    # 分发到所有节点
    distribute_config_to_all_nodes(config)
    
    # 重启 Master 服务（滚动重启）
    rolling_restart_masters(config)
    
    return new_nodes
```

#### 4.6.2 缩容流程
**注意点：**
- 选择要删除的节点（优先删除最新创建的）
- Worker 缩容：停止服务 -> 等待任务完成 -> 删除节点
- Master 缩容：确保至少保留 2 个节点
- 更新配置文件
- 从 ALB 目标组中移除（API 节点）

**代码示例：**
```python
def scale_in_workers(config, reduce_count):
    """缩容 Worker 节点"""
    current_workers = config['cluster']['worker']['nodes']
    
    if len(current_workers) - reduce_count < 1:
        raise ValueError("至少保留 1 个 Worker 节点")
    
    # 选择要删除的节点（最后创建的）
    nodes_to_remove = current_workers[-reduce_count:]
    
    # 停止服务并等待任务完成
    for node in nodes_to_remove:
        ssh = connect_ssh(node['host'])
        
        # 停止 Worker 服务
        execute_remote_command(
            ssh,
            f"cd {config['deployment']['install_path']} && "
            f"bash bin/dolphinscheduler-daemon.sh stop worker-server"
        )
        
        # 等待任务完成（检查进程）
        wait_for_tasks_completion(ssh)
    
    # 终止 EC2 实例
    ec2 = boto3.client('ec2', region_name=config['aws']['region'])
    instance_ids = [node['instance_id'] for node in nodes_to_remove]
    ec2.terminate_instances(InstanceIds=instance_ids)
    
    # 更新配置
    config['cluster']['worker']['nodes'] = current_workers[:-reduce_count]
    config['cluster']['worker']['count'] -= reduce_count
    
    # 保存配置
    save_config(config)
    
    return nodes_to_remove

def wait_for_tasks_completion(ssh, max_wait_seconds=300):
    """等待节点上的任务完成"""
    start_time = time.time()
    
    while time.time() - start_time < max_wait_seconds:
        # 检查是否还有任务在执行
        output = execute_remote_command(
            ssh,
            "ps aux | grep 'dolphinscheduler' | grep -v grep | wc -l"
        )
        
        if int(output.strip()) <= 1:  # 只剩下守护进程
            return True
        
        time.sleep(10)
    
    logger.warning("等待任务完成超时，强制停止")
    return False
```


### 4.7 日志和监控

#### 4.7.1 日志记录
**注意点：**
- 使用结构化日志
- 记录所有关键操作
- 区分不同日志级别
- 日志文件轮转
- 敏感信息脱敏（密码、密钥）

**代码示例：**
```python
import logging
from logging.handlers import RotatingFileHandler

def setup_logger(name, log_file, level=logging.INFO):
    """配置日志"""
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # 文件处理器
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=10*1024*1024,  # 10MB
        backupCount=5
    )
    file_handler.setFormatter(formatter)
    
    # 控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger

def mask_sensitive_data(data):
    """脱敏敏感信息"""
    sensitive_keys = ['password', 'secret', 'key', 'token']
    
    if isinstance(data, dict):
        return {
            k: '***' if any(s in k.lower() for s in sensitive_keys) else mask_sensitive_data(v)
            for k, v in data.items()
        }
    elif isinstance(data, list):
        return [mask_sensitive_data(item) for item in data]
    else:
        return data
```

#### 4.7.2 进度显示
**注意点：**
- 显示当前步骤和总步骤数
- 显示每个步骤的耗时
- 使用进度条显示长时间操作
- 提供详细模式和简洁模式

**代码示例：**
```python
from tqdm import tqdm
import time

class ProgressTracker:
    """进度跟踪器"""
    def __init__(self, total_steps):
        self.total_steps = total_steps
        self.current_step = 0
        self.step_start_time = None
    
    def start_step(self, step_name):
        """开始一个步骤"""
        self.current_step += 1
        self.step_start_time = time.time()
        print(f"\n[{self.current_step}/{self.total_steps}] {step_name}...")
    
    def finish_step(self):
        """完成一个步骤"""
        elapsed = time.time() - self.step_start_time
        print(f"✓ 完成 (耗时: {elapsed:.1f}s)")
    
    def update_progress(self, message):
        """更新进度信息"""
        print(f"  → {message}")

# 使用示例
tracker = ProgressTracker(total_steps=5)

tracker.start_step("创建 EC2 实例")
for i in tqdm(range(10), desc="创建实例"):
    time.sleep(0.5)
tracker.finish_step()

tracker.start_step("初始化节点")
tracker.update_progress("安装系统依赖...")
tracker.update_progress("配置 SSH...")
tracker.finish_step()
```

### 4.8 幂等性设计

#### 4.8.1 操作幂等性
**注意点：**
- 所有操作应该是幂等的
- 重复执行不会产生副作用
- 检查资源是否已存在
- 使用标签标识资源归属

**代码示例：**
```python
def create_ec2_instance_idempotent(config, component, index):
    """幂等创建 EC2 实例"""
    ec2 = boto3.resource('ec2', region_name=config['aws']['region'])
    
    # 检查是否已存在
    tag_name = f"ds-{component}-{index}"
    existing_instances = list(ec2.instances.filter(
        Filters=[
            {'Name': 'tag:Name', 'Values': [tag_name]},
            {'Name': 'instance-state-name', 'Values': ['running', 'pending']}
        ]
    ))
    
    if existing_instances:
        logger.info(f"实例已存在: {tag_name}")
        return existing_instances[0]
    
    # 创建新实例
    instance = create_ec2_instance(config, component, index)
    logger.info(f"创建新实例: {tag_name}")
    return instance

def deploy_dolphinscheduler_idempotent(config, node):
    """幂等部署 DolphinScheduler"""
    ssh = connect_ssh(node['host'])
    install_path = config['deployment']['install_path']
    
    # 检查是否已安装
    try:
        output = execute_remote_command(
            ssh,
            f"test -d {install_path} && echo 'exists' || echo 'not_exists'"
        )
        
        if 'exists' in output:
            logger.info(f"DolphinScheduler 已安装在 {node['host']}")
            return
    except:
        pass
    
    # 执行安装
    install_dolphinscheduler(ssh, config)
```

### 4.9 安全考虑

#### 4.9.1 凭证管理
**注意点：**
- 不在代码中硬编码凭证
- 使用环境变量或 AWS Secrets Manager
- SSH 密钥权限必须是 600
- 定期轮换凭证

**代码示例：**
```python
import os
from pathlib import Path

def load_credentials():
    """加载凭证"""
    # 优先使用环境变量
    aws_access_key = os.getenv('AWS_ACCESS_KEY_ID')
    aws_secret_key = os.getenv('AWS_SECRET_ACCESS_KEY')
    
    # 或从 AWS Secrets Manager 加载
    if not aws_access_key:
        secrets = load_from_secrets_manager('dolphinscheduler/credentials')
        aws_access_key = secrets['aws_access_key_id']
        aws_secret_key = secrets['aws_secret_access_key']
    
    return aws_access_key, aws_secret_key

def ensure_ssh_key_permissions(key_file):
    """确保 SSH 密钥权限正确"""
    key_path = Path(key_file)
    if key_path.exists():
        os.chmod(key_file, 0o600)
```

#### 4.9.2 网络安全
**注意点：**
- 使用私有子网部署应用节点
- 仅 ALB 部署在公有子网
- 安全组规则最小化
- 使用 VPC Endpoint 访问 S3（可选）

### 4.10 性能优化

#### 4.10.1 并行操作
**注意点：**
- 并行创建 EC2 实例
- 并行初始化节点
- 并行上传文件
- 使用线程池或进程池

**代码示例：**
```python
from concurrent.futures import ThreadPoolExecutor, as_completed

def create_instances_parallel(config, component, count):
    """并行创建实例"""
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = []
        for i in range(count):
            future = executor.submit(
                create_ec2_instance_idempotent,
                config, component, i
            )
            futures.append(future)
        
        instances = []
        for future in as_completed(futures):
            try:
                instance = future.result()
                instances.append(instance)
            except Exception as e:
                logger.error(f"创建实例失败: {e}")
                raise
        
        return instances

def initialize_nodes_parallel(nodes):
    """并行初始化节点"""
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(initialize_node, node): node
            for node in nodes
        }
        
        for future in as_completed(futures):
            node = futures[future]
            try:
                future.result()
                logger.info(f"节点初始化完成: {node['host']}")
            except Exception as e:
                logger.error(f"节点初始化失败 {node['host']}: {e}")
                raise
```

#### 4.10.2 缓存优化
**注意点：**
- 缓存 DolphinScheduler 安装包
- 缓存 AWS 资源查询结果
- 复用 SSH 连接

**代码示例：**
```python
import functools
from pathlib import Path

@functools.lru_cache(maxsize=128)
def get_ami_id(region, os_type='amazon-linux-2023'):
    """获取 AMI ID（带缓存）"""
    ec2 = boto3.client('ec2', region_name=region)
    # 查询 AMI
    response = ec2.describe_images(
        Owners=['amazon'],
        Filters=[
            {'Name': 'name', 'Values': [f'{os_type}*']},
            {'Name': 'state', 'Values': ['available']}
        ]
    )
    return response['Images'][0]['ImageId']

def download_dolphinscheduler_cached(version, cache_dir='/tmp/ds-cache'):
    """下载 DolphinScheduler（带缓存）"""
    cache_path = Path(cache_dir)
    cache_path.mkdir(exist_ok=True)
    
    package_name = f"apache-dolphinscheduler-{version}-bin.tar.gz"
    local_file = cache_path / package_name
    
    if local_file.exists():
        logger.info(f"使用缓存的安装包: {local_file}")
        return str(local_file)
    
    # 下载
    url = f"https://archive.apache.org/dist/dolphinscheduler/{version}/{package_name}"
    download_file(url, str(local_file))
    
    return str(local_file)
```

## 5. 性能参数调整设计（滚动重启方案）

### 5.1 概述

集群部署成功后，需要支持动态调整性能参数以优化系统性能。采用**滚动重启方案**，确保服务持续可用。

### 5.2 可调整的性能参数

#### 5.2.1 JVM 参数
- 堆内存大小（xms, xmx, xmn）
- GC 策略（G1GC, CMS, ZGC）
- GC 日志配置
- 其他 JVM 参数

#### 5.2.2 DolphinScheduler 服务配置
**Master 配置**:
- `master.exec.threads`: 执行线程数
- `master.dispatch.task.num`: 任务分发数量
- `master.heartbeat.interval`: 心跳间隔
- `master.max.cpu.load.avg`: 最大 CPU 负载
- `master.reserved.memory`: 预留内存比例

**Worker 配置**:
- `worker.exec.threads`: 执行线程数
- `worker.heartbeat.interval`: 心跳间隔
- `worker.max.cpu.load.avg`: 最大 CPU 负载
- `worker.reserved.memory`: 预留内存比例

**API 配置**:
- `server.port`: 服务端口
- `server.session.timeout`: 会话超时时间
- 数据库连接池配置

#### 5.2.3 EC2 实例规格调整
- 实例类型升级/降级（需要停机）
- EBS 卷大小调整

### 5.3 滚动重启流程设计

```
1. 配置验证
   ├── 加载新配置
   ├── 验证参数合法性
   ├── 备份当前配置
   └── 生成差异报告

2. 配置分发
   ├── 生成新的配置文件
   ├── 分发到所有节点
   └── 验证文件完整性

3. 滚动重启（按组件顺序）
   ├── Worker 节点（逐个重启）
   │   ├── 选择一个节点
   │   ├── 停止服务
   │   ├── 等待任务完成
   │   ├── 更新配置
   │   ├── 启动服务
   │   ├── 健康检查
   │   └── 继续下一个节点
   │
   ├── Master 节点（逐个重启）
   │   ├── 确保至少 N-1 个 Master 在线
   │   ├── 停止服务
   │   ├── 更新配置
   │   ├── 启动服务
   │   ├── 健康检查
   │   └── 继续下一个节点
   │
   ├── API 节点（逐个重启）
   │   ├── 从 ALB 移除节点
   │   ├── 等待连接排空
   │   ├── 停止服务
   │   ├── 更新配置
   │   ├── 启动服务
   │   ├── 健康检查
   │   └── 加回 ALB
   │
   └── Alert 节点
       ├── 停止服务
       ├── 更新配置
       ├── 启动服务
       └── 健康检查

4. 验证和监控
   ├── 检查所有服务状态
   ├── 验证集群功能
   ├── 监控性能指标
   └── 生成更新报告
```

### 5.4 配置更新实现

#### 5.4.1 配置版本管理

**注意点：**
- 每次更新前备份当前配置
- 使用时间戳标识配置版本
- 支持查看历史配置
- 支持回滚到指定版本

**代码示例：**
```python
import shutil
from datetime import datetime
from pathlib import Path

class ConfigVersionManager:
    """配置版本管理器"""
    def __init__(self, config_file):
        self.config_file = Path(config_file)
        self.backup_dir = self.config_file.parent / '.config_backups'
        self.backup_dir.mkdir(exist_ok=True)
    
    def backup_current_config(self):
        """备份当前配置"""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_file = self.backup_dir / f"config_{timestamp}.yaml"
        shutil.copy2(self.config_file, backup_file)
        
        logger.info(f"配置已备份: {backup_file}")
        return backup_file
    
    def list_versions(self):
        """列出所有配置版本"""
        backups = sorted(self.backup_dir.glob('config_*.yaml'), reverse=True)
        return [
            {
                'file': str(backup),
                'timestamp': backup.stem.replace('config_', ''),
                'size': backup.stat().st_size
            }
            for backup in backups
        ]
    
    def rollback_to_version(self, version_timestamp):
        """回滚到指定版本"""
        backup_file = self.backup_dir / f"config_{version_timestamp}.yaml"
        
        if not backup_file.exists():
            raise ValueError(f"版本不存在: {version_timestamp}")
        
        # 备份当前配置
        self.backup_current_config()
        
        # 恢复指定版本
        shutil.copy2(backup_file, self.config_file)
        logger.info(f"已回滚到版本: {version_timestamp}")
```

#### 5.4.2 配置差异分析

**代码示例：**
```python
import yaml
from deepdiff import DeepDiff

def analyze_config_diff(old_config, new_config):
    """分析配置差异"""
    diff = DeepDiff(old_config, new_config, ignore_order=True)
    
    changes = {
        'jvm_changes': [],
        'service_changes': [],
        'requires_restart': False
    }
    
    # 分析 JVM 变更
    if 'values_changed' in diff:
        for key, value in diff['values_changed'].items():
            if 'jvm' in key:
                changes['jvm_changes'].append({
                    'path': key,
                    'old': value['old_value'],
                    'new': value['new_value']
                })
                changes['requires_restart'] = True
    
    # 分析服务配置变更
    if 'values_changed' in diff:
        for key, value in diff['values_changed'].items():
            if 'service_config' in key:
                changes['service_changes'].append({
                    'path': key,
                    'old': value['old_value'],
                    'new': value['new_value']
                })
                changes['requires_restart'] = True
    
    return changes

def print_config_diff(changes):
    """打印配置差异"""
    print("\n配置变更摘要:")
    print("=" * 60)
    
    if changes['jvm_changes']:
        print("\nJVM 参数变更:")
        for change in changes['jvm_changes']:
            print(f"  {change['path']}")
            print(f"    旧值: {change['old']}")
            print(f"    新值: {change['new']}")
    
    if changes['service_changes']:
        print("\n服务配置变更:")
        for change in changes['service_changes']:
            print(f"  {change['path']}")
            print(f"    旧值: {change['old']}")
            print(f"    新值: {change['new']}")
    
    if changes['requires_restart']:
        print("\n⚠️  此变更需要重启服务")
    
    print("=" * 60)
```

#### 5.4.3 配置文件生成

**代码示例：**
```python
def generate_component_config(config, component):
    """生成组件配置文件"""
    
    if component == 'master':
        return generate_master_properties(config)
    elif component == 'worker':
        return generate_worker_properties(config)
    elif component == 'api':
        return generate_api_properties(config)
    elif component == 'alert':
        return generate_alert_properties(config)

def generate_master_properties(config):
    """生成 Master 配置"""
    service_config = config.get('service_config', {}).get('master', {})
    
    properties = f"""
# Master Server 配置
master.listen.port={service_config.get('listen_port', 5678)}
master.exec.threads={service_config.get('exec_threads', 100)}
master.dispatch.task.number={service_config.get('dispatch_task_number', 3)}
master.host.selector={service_config.get('host_selector', 'LowerWeight')}
master.heartbeat.interval={service_config.get('heartbeat_interval', 10)}s
master.task.commit.retryTimes={service_config.get('task_commit_retry_times', 5)}
master.task.commit.interval={service_config.get('task_commit_interval', 1)}s
master.max.cpuload.avg={service_config.get('max_cpu_load_avg', -1)}
master.reserved.memory={service_config.get('reserved_memory', 0.3)}
"""
    return properties

def generate_jvm_options(config, component):
    """生成 JVM 参数"""
    jvm_config = config.get('jvm', {}).get(component, {})
    
    xms = jvm_config.get('xms', '2g')
    xmx = jvm_config.get('xmx', '2g')
    xmn = jvm_config.get('xmn', '1g')
    gc = jvm_config.get('gc', 'G1GC')
    extra_opts = jvm_config.get('extra_opts', '')
    
    jvm_opts = f"-Xms{xms} -Xmx{xmx} -Xmn{xmn} -XX:+Use{gc} {extra_opts}"
    
    return jvm_opts
```

### 5.5 滚动重启实现

#### 5.5.1 Worker 滚动重启

**注意点：**
- 逐个重启，确保其他 Worker 可以接管任务
- 等待当前任务完成后再停止
- 重启后验证服务正常
- 如果失败，停止滚动并告警

**代码示例：**
```python
def rolling_restart_workers(config, new_config):
    """滚动重启 Worker 节点"""
    workers = config['cluster']['worker']['nodes']
    install_path = config['deployment']['install_path']
    
    total = len(workers)
    success_count = 0
    
    for index, worker in enumerate(workers, 1):
        logger.info(f"正在重启 Worker {index}/{total}: {worker['host']}")
        
        try:
            ssh = connect_ssh(worker['host'])
            
            # 1. 停止服务
            logger.info(f"  停止服务...")
            execute_remote_command(
                ssh,
                f"cd {install_path} && bash bin/dolphinscheduler-daemon.sh stop worker-server"
            )
            
            # 2. 等待任务完成
            logger.info(f"  等待任务完成...")
            wait_for_tasks_completion(ssh, max_wait_seconds=300)
            
            # 3. 更新配置文件
            logger.info(f"  更新配置...")
            update_worker_config(ssh, install_path, new_config)
            
            # 4. 更新 JVM 参数
            update_jvm_config(ssh, install_path, 'worker', new_config)
            
            # 5. 启动服务
            logger.info(f"  启动服务...")
            execute_remote_command(
                ssh,
                f"cd {install_path} && bash bin/dolphinscheduler-daemon.sh start worker-server"
            )
            
            # 6. 健康检查
            logger.info(f"  健康检查...")
            if not wait_for_service_ready(worker['host'], 1234, max_retries=30):
                raise Exception("服务启动失败")
            
            # 7. 验证服务功能
            if not verify_worker_functionality(worker['host']):
                raise Exception("服务功能验证失败")
            
            success_count += 1
            logger.info(f"✓ Worker {index}/{total} 重启成功")
            
            # 等待一段时间再重启下一个
            if index < total:
                time.sleep(10)
            
        except Exception as e:
            logger.error(f"✗ Worker {index}/{total} 重启失败: {str(e)}")
            
            # 决定是否继续
            if not click.confirm('是否继续重启其他节点？', default=False):
                raise Exception(f"滚动重启中止，已成功重启 {success_count}/{total} 个节点")
    
    logger.info(f"✓ 所有 Worker 节点重启完成 ({success_count}/{total})")

def update_worker_config(ssh, install_path, config):
    """更新 Worker 配置文件"""
    # 生成新配置
    worker_properties = generate_worker_properties(config)
    
    # 创建临时文件
    temp_file = '/tmp/worker.properties'
    with open(temp_file, 'w') as f:
        f.write(worker_properties)
    
    # 上传到服务器
    sftp = ssh.open_sftp()
    sftp.put(temp_file, f"{install_path}/conf/worker.properties")
    sftp.close()
    
    # 删除临时文件
    os.remove(temp_file)

def update_jvm_config(ssh, install_path, component, config):
    """更新 JVM 配置"""
    jvm_opts = generate_jvm_options(config, component)
    
    # 更新 dolphinscheduler_env.sh
    env_file = f"{install_path}/bin/env/dolphinscheduler_env.sh"
    
    command = f"""
    sed -i 's/export {component.upper()}_JAVA_OPTS=.*/export {component.upper()}_JAVA_OPTS="{jvm_opts}"/' {env_file}
    """
    
    execute_remote_command(ssh, command)

def verify_worker_functionality(host):
    """验证 Worker 功能"""
    # 可以通过 API 检查 Worker 是否在线
    # 或者检查日志中是否有错误
    try:
        # 简单检查：端口是否监听
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        result = sock.connect_ex((host, 1234))
        sock.close()
        return result == 0
    except:
        return False
```

#### 5.5.2 Master 滚动重启

**注意点：**
- 确保至少有 N-1 个 Master 在线
- Master 重启会触发重新选举
- 等待选举完成后再重启下一个

**代码示例：**
```python
def rolling_restart_masters(config, new_config):
    """滚动重启 Master 节点"""
    masters = config['cluster']['master']['nodes']
    install_path = config['deployment']['install_path']
    
    if len(masters) < 2:
        raise ValueError("Master 节点少于 2 个，不支持滚动重启")
    
    total = len(masters)
    
    for index, master in enumerate(masters, 1):
        logger.info(f"正在重启 Master {index}/{total}: {master['host']}")
        
        try:
            # 1. 检查其他 Master 是否在线
            online_masters = check_online_masters(masters, exclude=master['host'])
            if len(online_masters) < len(masters) - 1:
                raise Exception("其他 Master 节点不足，无法安全重启")
            
            ssh = connect_ssh(master['host'])
            
            # 2. 停止服务
            logger.info(f"  停止服务...")
            execute_remote_command(
                ssh,
                f"cd {install_path} && bash bin/dolphinscheduler-daemon.sh stop master-server"
            )
            
            # 3. 更新配置
            logger.info(f"  更新配置...")
            update_master_config(ssh, install_path, new_config)
            update_jvm_config(ssh, install_path, 'master', new_config)
            
            # 4. 启动服务
            logger.info(f"  启动服务...")
            execute_remote_command(
                ssh,
                f"cd {install_path} && bash bin/dolphinscheduler-daemon.sh start master-server"
            )
            
            # 5. 健康检查
            logger.info(f"  健康检查...")
            if not wait_for_service_ready(master['host'], 5678, max_retries=30):
                raise Exception("服务启动失败")
            
            # 6. 等待 Master 加入集群
            logger.info(f"  等待加入集群...")
            time.sleep(20)
            
            # 7. 验证集群状态
            if not verify_master_cluster_status(masters):
                raise Exception("集群状态异常")
            
            logger.info(f"✓ Master {index}/{total} 重启成功")
            
            # 等待一段时间再重启下一个
            if index < total:
                time.sleep(30)
            
        except Exception as e:
            logger.error(f"✗ Master {index}/{total} 重启失败: {str(e)}")
            raise
    
    logger.info(f"✓ 所有 Master 节点重启完成")

def check_online_masters(masters, exclude=None):
    """检查在线的 Master 节点"""
    online = []
    for master in masters:
        if exclude and master['host'] == exclude:
            continue
        
        if is_service_running(master['host'], 5678):
            online.append(master)
    
    return online

def verify_master_cluster_status(masters):
    """验证 Master 集群状态"""
    # 可以通过 Zookeeper 检查 Master 注册信息
    # 或者通过 API 检查集群状态
    online_count = len(check_online_masters(masters))
    return online_count == len(masters)
```

#### 5.5.3 API 滚动重启（带 ALB）

**注意点：**
- 从 ALB 目标组中移除节点
- 等待连接排空（draining）
- 重启后加回 ALB
- 等待健康检查通过

**代码示例：**
```python
def rolling_restart_apis(config, new_config):
    """滚动重启 API 节点"""
    apis = config['cluster']['api']['nodes']
    install_path = config['deployment']['install_path']
    
    # 检查是否配置了 ALB
    alb_enabled = config.get('service_config', {}).get('api', {}).get('load_balancer', {}).get('enabled', False)
    
    if alb_enabled:
        elbv2 = boto3.client('elbv2', region_name=config['aws']['region'])
        target_group_arn = get_target_group_arn(config)
    
    total = len(apis)
    
    for index, api in enumerate(apis, 1):
        logger.info(f"正在重启 API {index}/{total}: {api['host']}")
        
        try:
            # 1. 从 ALB 移除
            if alb_enabled:
                logger.info(f"  从 ALB 移除...")
                deregister_target_from_alb(
                    elbv2, target_group_arn, api['instance_id']
                )
                
                # 等待连接排空
                logger.info(f"  等待连接排空...")
                wait_for_target_draining(
                    elbv2, target_group_arn, api['instance_id']
                )
            
            ssh = connect_ssh(api['host'])
            
            # 2. 停止服务
            logger.info(f"  停止服务...")
            execute_remote_command(
                ssh,
                f"cd {install_path} && bash bin/dolphinscheduler-daemon.sh stop api-server"
            )
            
            # 3. 更新配置
            logger.info(f"  更新配置...")
            update_api_config(ssh, install_path, new_config)
            update_jvm_config(ssh, install_path, 'api', new_config)
            
            # 4. 启动服务
            logger.info(f"  启动服务...")
            execute_remote_command(
                ssh,
                f"cd {install_path} && bash bin/dolphinscheduler-daemon.sh start api-server"
            )
            
            # 5. 健康检查
            logger.info(f"  健康检查...")
            if not wait_for_service_ready(api['host'], 12345, max_retries=30):
                raise Exception("服务启动失败")
            
            # 6. 加回 ALB
            if alb_enabled:
                logger.info(f"  加回 ALB...")
                register_target_to_alb(
                    elbv2, target_group_arn, api['instance_id']
                )
                
                # 等待健康检查通过
                logger.info(f"  等待 ALB 健康检查...")
                wait_for_target_healthy(
                    elbv2, target_group_arn, api['instance_id']
                )
            
            logger.info(f"✓ API {index}/{total} 重启成功")
            
            # 等待一段时间再重启下一个
            if index < total:
                time.sleep(10)
            
        except Exception as e:
            logger.error(f"✗ API {index}/{total} 重启失败: {str(e)}")
            
            # 如果失败，尝试加回 ALB
            if alb_enabled:
                try:
                    register_target_to_alb(
                        elbv2, target_group_arn, api['instance_id']
                    )
                except:
                    pass
            
            raise
    
    logger.info(f"✓ 所有 API 节点重启完成")

def deregister_target_from_alb(elbv2, target_group_arn, instance_id):
    """从 ALB 目标组移除实例"""
    elbv2.deregister_targets(
        TargetGroupArn=target_group_arn,
        Targets=[{'Id': instance_id}]
    )

def wait_for_target_draining(elbv2, target_group_arn, instance_id, max_wait=300):
    """等待目标排空"""
    start_time = time.time()
    
    while time.time() - start_time < max_wait:
        response = elbv2.describe_target_health(
            TargetGroupArn=target_group_arn,
            Targets=[{'Id': instance_id}]
        )
        
        if not response['TargetHealthDescriptions']:
            return True
        
        state = response['TargetHealthDescriptions'][0]['TargetHealth']['State']
        if state == 'unused':
            return True
        
        time.sleep(5)
    
    logger.warning("连接排空超时")
    return False

def register_target_to_alb(elbv2, target_group_arn, instance_id):
    """将实例加回 ALB 目标组"""
    elbv2.register_targets(
        TargetGroupArn=target_group_arn,
        Targets=[{'Id': instance_id}]
    )

def wait_for_target_healthy(elbv2, target_group_arn, instance_id, max_wait=300):
    """等待目标健康检查通过"""
    start_time = time.time()
    
    while time.time() - start_time < max_wait:
        response = elbv2.describe_target_health(
            TargetGroupArn=target_group_arn,
            Targets=[{'Id': instance_id}]
        )
        
        if response['TargetHealthDescriptions']:
            state = response['TargetHealthDescriptions'][0]['TargetHealth']['State']
            if state == 'healthy':
                return True
        
        time.sleep(10)
    
    raise Exception("健康检查超时")
```

### 5.6 配置回滚

**代码示例：**
```python
def rollback_config(config_file, version_timestamp):
    """回滚配置并重启服务"""
    version_manager = ConfigVersionManager(config_file)
    
    # 1. 回滚配置文件
    logger.info(f"回滚配置到版本: {version_timestamp}")
    version_manager.rollback_to_version(version_timestamp)
    
    # 2. 加载回滚后的配置
    old_config = load_config(config_file)
    
    # 3. 执行滚动重启
    logger.info("开始滚动重启以应用回滚配置...")
    rolling_restart_all_components(old_config, old_config)
    
    logger.info("✓ 配置回滚完成")
```

## 6. CLI 命令设计

### 6.1 命令行参数

```bash
# 创建集群
python cli.py create --config config.yaml [--dry-run] [--verbose]

# 删除集群
python cli.py delete --config config.yaml [--force] [--keep-data]

# 扩缩容
python cli.py scale --config config.yaml --component worker --count 5
python cli.py scale --config config.yaml --component master --count 3

# 更新配置（滚动重启）
python cli.py config update --config config.yaml [--component master|worker|api|alert|all]

# 更新 JVM 参数
python cli.py config update-jvm --config config.yaml --component worker --xms 8g --xmx 8g

# 更新服务参数
python cli.py config update-service --config config.yaml --component master --param exec_threads=200

# 查看配置历史
python cli.py config history --config config.yaml

# 回滚配置
python cli.py config rollback --config config.yaml --version 20241212_143000

# 查看集群状态
python cli.py status --config config.yaml

# 验证配置
python cli.py validate --config config.yaml
```

### 6.2 CLI 实现示例

```python
import click

@click.group()
def cli():
    """DolphinScheduler EC2 集群管理工具"""
    pass

@cli.command()
@click.option('--config', required=True, help='配置文件路径')
@click.option('--dry-run', is_flag=True, help='仅验证，不实际执行')
@click.option('--verbose', is_flag=True, help='详细输出')
def create(config, dry_run, verbose):
    """创建 DolphinScheduler 集群"""
    setup_logger('create', 'create.log', logging.DEBUG if verbose else logging.INFO)
    
    try:
        # 加载配置
        cfg = load_config(config)
        
        # 验证配置
        validate_config(cfg)
        validate_aws_resources(cfg)
        validate_database_connection(cfg['database'])
        
        if dry_run:
            click.echo("✓ 配置验证通过（dry-run 模式）")
            return
        
        # 创建集群
        click.echo("开始创建集群...")
        instances = create_cluster_with_rollback(cfg)
        
        click.echo(f"✓ 集群创建成功！共创建 {len(instances)} 个实例")
        
    except Exception as e:
        click.echo(f"✗ 创建失败: {str(e)}", err=True)
        raise click.Abort()

@cli.command()
@click.option('--config', required=True, help='配置文件路径')
@click.option('--force', is_flag=True, help='强制删除，不确认')
@click.option('--keep-data', is_flag=True, help='保留数据库和 S3 数据')
def delete(config, force, keep_data):
    """删除 DolphinScheduler 集群"""
    cfg = load_config(config)
    
    if not force:
        click.confirm('确定要删除集群吗？此操作不可恢复', abort=True)
    
    try:
        delete_cluster(cfg, keep_data=keep_data)
        click.echo("✓ 集群删除成功")
    except Exception as e:
        click.echo(f"✗ 删除失败: {str(e)}", err=True)
        raise click.Abort()

@cli.command()
@click.option('--config', required=True, help='配置文件路径')
@click.option('--component', required=True, type=click.Choice(['master', 'worker', 'api']))
@click.option('--count', required=True, type=int, help='目标节点数量')
def scale(config, component, count):
    """扩缩容集群"""
    cfg = load_config(config)
    current_count = cfg['cluster'][component]['count']
    
    if count == current_count:
        click.echo(f"{component} 节点数量已经是 {count}")
        return
    
    if count > current_count:
        click.echo(f"扩容 {component}: {current_count} -> {count}")
        scale_out(cfg, component, count - current_count)
    else:
        click.echo(f"缩容 {component}: {current_count} -> {count}")
        scale_in(cfg, component, current_count - count)
    
    click.echo("✓ 扩缩容完成")

# 配置管理命令组
@cli.group()
def config():
    """配置管理命令"""
    pass

@config.command('update')
@click.option('--config', required=True, help='配置文件路径')
@click.option('--component', type=click.Choice(['master', 'worker', 'api', 'alert', 'all']), default='all')
@click.option('--dry-run', is_flag=True, help='仅显示差异，不实际执行')
def config_update(config, component, dry_run):
    """更新配置并滚动重启"""
    try:
        # 加载新配置
        new_cfg = load_config(config)
        
        # 加载当前运行的配置（从备份中获取最新的）
        version_manager = ConfigVersionManager(config)
        versions = version_manager.list_versions()
        if not versions:
            click.echo("⚠️  未找到历史配置，假设这是首次更新")
            old_cfg = new_cfg
        else:
            old_cfg = load_config(versions[0]['file'])
        
        # 分析差异
        changes = analyze_config_diff(old_cfg, new_cfg)
        print_config_diff(changes)
        
        if dry_run:
            click.echo("\n✓ 配置差异分析完成（dry-run 模式）")
            return
        
        if not changes['requires_restart']:
            click.echo("\n✓ 配置无需重启")
            return
        
        # 确认执行
        if not click.confirm('\n是否继续执行滚动重启？'):
            click.echo("操作已取消")
            return
        
        # 备份当前配置
        version_manager.backup_current_config()
        
        # 执行滚动重启
        click.echo("\n开始滚动重启...")
        
        if component == 'all':
            rolling_restart_all_components(old_cfg, new_cfg)
        elif component == 'worker':
            rolling_restart_workers(old_cfg, new_cfg)
        elif component == 'master':
            rolling_restart_masters(old_cfg, new_cfg)
        elif component == 'api':
            rolling_restart_apis(old_cfg, new_cfg)
        elif component == 'alert':
            rolling_restart_alert(old_cfg, new_cfg)
        
        click.echo("\n✓ 配置更新完成")
        
    except Exception as e:
        click.echo(f"\n✗ 配置更新失败: {str(e)}", err=True)
        click.echo("\n可以使用 'config rollback' 命令回滚配置")
        raise click.Abort()

@config.command('update-jvm')
@click.option('--config', required=True, help='配置文件路径')
@click.option('--component', required=True, type=click.Choice(['master', 'worker', 'api', 'alert']))
@click.option('--xms', help='最小堆内存，如: 4g')
@click.option('--xmx', help='最大堆内存，如: 4g')
@click.option('--xmn', help='新生代内存，如: 2g')
def config_update_jvm(config, component, xms, xmx, xmn):
    """更新 JVM 参数"""
    try:
        cfg = load_config(config)
        
        # 更新 JVM 配置
        if 'jvm' not in cfg:
            cfg['jvm'] = {}
        if component not in cfg['jvm']:
            cfg['jvm'][component] = {}
        
        if xms:
            cfg['jvm'][component]['xms'] = xms
        if xmx:
            cfg['jvm'][component]['xmx'] = xmx
        if xmn:
            cfg['jvm'][component]['xmn'] = xmn
        
        # 保存配置
        save_config(config, cfg)
        
        click.echo(f"✓ JVM 配置已更新")
        click.echo(f"\n使用 'config update --component {component}' 命令应用更改")
        
    except Exception as e:
        click.echo(f"✗ 更新失败: {str(e)}", err=True)
        raise click.Abort()

@config.command('history')
@click.option('--config', required=True, help='配置文件路径')
def config_history(config):
    """查看配置历史"""
    version_manager = ConfigVersionManager(config)
    versions = version_manager.list_versions()
    
    if not versions:
        click.echo("未找到配置历史")
        return
    
    click.echo("\n配置历史版本:")
    click.echo("=" * 60)
    for i, version in enumerate(versions, 1):
        click.echo(f"{i}. 版本: {version['timestamp']}")
        click.echo(f"   文件: {version['file']}")
        click.echo(f"   大小: {version['size']} bytes")
        click.echo()

@config.command('rollback')
@click.option('--config', required=True, help='配置文件路径')
@click.option('--version', required=True, help='版本时间戳，如: 20241212_143000')
def config_rollback(config, version):
    """回滚到指定配置版本"""
    try:
        if not click.confirm(f'确定要回滚到版本 {version} 吗？'):
            click.echo("操作已取消")
            return
        
        rollback_config(config, version)
        click.echo("✓ 配置回滚成功")
        
    except Exception as e:
        click.echo(f"✗ 回滚失败: {str(e)}", err=True)
        raise click.Abort()

if __name__ == '__main__':
    cli()
```

## 6. 测试策略

### 6.1 单元测试
- 配置验证逻辑
- AWS 资源创建逻辑
- SSH 连接和命令执行
- 配置文件生成

### 6.2 集成测试
- 完整的创建流程
- 扩缩容流程
- 删除流程
- 错误恢复

### 6.3 端到端测试
- 在真实 AWS 环境中测试
- 验证服务可用性
- 验证高可用性（模拟 AZ 故障）

## 7. 文档和交付

### 7.1 用户文档
- 快速开始指南
- 配置文件说明
- 常见问题解答
- 故障排查指南

### 7.2 开发文档
- 代码结构说明
- API 文档
- 扩展指南

## 8. 后续优化方向

1. **支持更多部署模式**
   - 单机模式
   - 混合部署（部分组件共享节点）

2. **增强监控**
   - 集成 CloudWatch
   - 集成 Prometheus
   - 告警配置

3. **自动化运维**
   - 自动备份
   - 自动升级
   - 自动故障恢复

4. **成本优化**
   - 使用 Spot 实例
   - 自动缩容策略
   - 成本分析报告

5. **安全增强**
   - 集成 AWS Secrets Manager
   - 启用加密
   - 审计日志

6. **多区域支持**
   - 跨区域部署
   - 灾难恢复

---

**文档版本**: 1.0  
**最后更新**: 2024-12-12  
**作者**: DolphinScheduler EC2 部署工具开发团队


## 6. DolphinScheduler 包目录结构和配置文件创建

### 6.1 DolphinScheduler 3.2.0 包结构

根据实际部署包分析，DolphinScheduler 3.2.0 的目录结构如下：

```
apache-dolphinscheduler-3.2.0-bin/
├── bin/
│   ├── env/                          # 环境配置目录（存在）
│   │   ├── install_env.sh
│   │   └── dolphinscheduler_env.sh
│   ├── dolphinscheduler-daemon.sh
│   ├── install.sh
│   ├── start-all.sh
│   ├── stop-all.sh
│   └── ...
├── conf/                             # 全局配置目录（不存在，需创建）
│   ├── common.properties             # 需要创建
│   └── plugins_config                # 需要创建
├── api-server/
│   ├── bin/
│   ├── conf/                         # 存在
│   │   └── application.yaml          # 需要创建
│   └── libs/
├── master-server/
│   ├── bin/
│   ├── conf/                         # 存在
│   │   └── application.yaml          # 需要创建
│   └── libs/
├── worker-server/
│   ├── bin/
│   ├── conf/                         # 存在
│   │   └── application.yaml          # 需要创建
│   └── libs/
├── alert-server/
│   ├── bin/
│   ├── conf/                         # 存在
│   │   └── application.yaml          # 需要创建
│   └── libs/
├── tools/
│   ├── bin/
│   ├── conf/                         # 存在
│   │   └── application.yaml          # 需要创建
│   ├── libs/
│   └── sql/
├── standalone-server/
│   ├── bin/
│   ├── conf/
│   └── libs/
├── plugins/                          # 插件目录（需创建 S3 插件）
│   └── dolphinscheduler-storage-plugin-s3/
├── licenses/
├── LICENSE
└── NOTICE
```

### 6.2 关键发现

#### 6.2.1 根目录 conf 不存在
- **问题**: 根目录下的 `conf/` 目录在原始包中不存在
- **影响**: `common.properties` 文件无法直接放置
- **解决**: 在上传 `common.properties` 前创建 `conf/` 目录

#### 6.2.2 各组件 conf 目录存在
- `api-server/conf/` ✓ 存在
- `master-server/conf/` ✓ 存在
- `worker-server/conf/` ✓ 存在
- `alert-server/conf/` ✓ 存在
- `tools/conf/` ✓ 存在

#### 6.2.3 bin/env 目录存在
- `bin/env/` ✓ 存在
- 包含 `install_env.sh` 和 `dolphinscheduler_env.sh` 的占位符

### 6.3 配置文件创建修复

#### 6.3.1 修复方案

在上传配置文件前，确保目标目录存在：

```python
# 1. 上传 common.properties 前
execute_remote_command(ssh, f"sudo mkdir -p {extract_dir}/conf")

# 2. 上传 application.yaml 前（每个组件）
execute_remote_command(ssh, f"sudo mkdir -p {extract_dir}/{component_dir}/conf")

# 3. 上传 install_env.sh 和 dolphinscheduler_env.sh 前
execute_remote_command(ssh, f"sudo mkdir -p {extract_dir}/bin/env")
```

#### 6.3.2 实现位置

修复已在以下函数中实现：

1. **`upload_configuration_files()`** - 上传 install_env.sh 和 dolphinscheduler_env.sh
   - 添加: `sudo mkdir -p {extract_dir}/bin/env`

2. **`upload_common_properties()`** - 上传 common.properties
   - 添加: `sudo mkdir -p {extract_dir}/conf`

3. **`configure_components()`** - 上传 application.yaml
   - 添加: `sudo mkdir -p {extract_dir}/{component_dir}/conf` (每个组件)
   - 添加: `sudo mkdir -p {extract_dir}/tools/conf` (tools 组件)

#### 6.3.3 幂等性保证

使用 `mkdir -p` 命令确保幂等性：
- 如果目录已存在，不会报错
- 如果目录不存在，会创建
- 可以安全地重复执行

### 6.4 S3 存储插件配置

#### 6.4.1 插件安装位置
```
apache-dolphinscheduler-3.2.0-bin/
└── plugins/
    └── dolphinscheduler-storage-plugin-s3-3.2.0.jar
```

#### 6.4.2 插件配置文件
```
apache-dolphinscheduler-3.2.0-bin/conf/plugins_config
```

内容示例：
```
--storage-plugins--
dolphinscheduler-storage-plugin-s3
--end--
```

#### 6.4.3 common.properties 中的 S3 配置
```properties
resource.storage.type=S3
resource.aws.region=us-east-2
resource.aws.s3.bucket.name=tx-mageline-eks
resource.aws.s3.upload.folder=/dolphinscheduler
resource.aws.s3.endpoint=https://s3.us-east-2.amazonaws.com
```

### 6.5 部署流程中的目录创建时机

```
部署流程:
  ↓
Step 1: 下载/提取包
  ↓
Step 2: 设置权限
  ↓
Step 3: 创建资源目录 (/tmp/dolphinscheduler)
  ↓
Step 4: 上传配置文件
  ├─ 创建 bin/env 目录 ← 关键
  ├─ 上传 install_env.sh
  ├─ 上传 dolphinscheduler_env.sh
  └─ 验证上传成功
  ↓
Step 5: 上传 common.properties
  ├─ 创建 conf 目录 ← 关键
  ├─ 上传 common.properties
  └─ 验证上传成功
  ↓
Step 5.5: 检查和安装 S3 插件
  ├─ 检查插件是否已安装
  ├─ 如果未安装，下载并安装
  └─ 配置 plugins_config
  ↓
Step 6: 安装 MySQL JDBC 驱动
  ↓
Step 7: 初始化数据库
  ↓
Step 8: 配置组件
  ├─ 创建各组件 conf 目录 ← 关键
  ├─ 上传 application.yaml
  └─ 验证上传成功
```

### 6.6 验证清单

部署时应验证以下目录结构：

```bash
# 验证根目录 conf
ls -la /opt/dolphinscheduler/conf/
# 应该包含: common.properties, plugins_config

# 验证各组件 conf
ls -la /opt/dolphinscheduler/api-server/conf/
ls -la /opt/dolphinscheduler/master-server/conf/
ls -la /opt/dolphinscheduler/worker-server/conf/
ls -la /opt/dolphinscheduler/alert-server/conf/
ls -la /opt/dolphinscheduler/tools/conf/
# 每个目录应该包含: application.yaml

# 验证 bin/env
ls -la /opt/dolphinscheduler/bin/env/
# 应该包含: install_env.sh, dolphinscheduler_env.sh

# 验证 S3 插件
ls -la /opt/dolphinscheduler/plugins/
# 应该包含: dolphinscheduler-storage-plugin-s3-3.2.0.jar
```

### 6.7 故障排除

#### 问题: "No such file or directory" 错误

**症状**:
```
ERROR - Command failed (exit code 1): mv: cannot move '/tmp/common.properties' to '/opt/dolphinscheduler/conf/common.properties': No such file or directory
```

**原因**: 目标目录不存在

**解决方案**:
1. 确保在移动文件前创建目录
2. 使用 `mkdir -p` 确保幂等性
3. 验证目录权限正确

#### 问题: 配置文件未被读取

**症状**: 
- S3 存储未被使用
- 系统仍然使用 HDFS 存储

**原因**: 
- `common.properties` 未被正确放置
- 配置文件权限不正确
- 服务未重启

**解决方案**:
1. 验证文件位置: `ls -la /opt/dolphinscheduler/conf/common.properties`
2. 验证文件权限: `chmod 644 /opt/dolphinscheduler/conf/common.properties`
3. 验证文件内容: `grep resource.storage.type /opt/dolphinscheduler/conf/common.properties`
4. 重启服务: `bash bin/dolphinscheduler-daemon.sh restart api-server`

