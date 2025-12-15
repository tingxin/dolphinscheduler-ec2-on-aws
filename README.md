# DolphinScheduler EC2 集群部署工具

在 AWS EC2 上自动化部署和管理 DolphinScheduler 3.2.0 集群的 Python CLI 工具。

## 📋 部署要求

### 1. 堡垒机环境准备

**推荐堡垒机配置：**
- EC2 实例类型：t3.medium 或更高
- 操作系统：Amazon Linux 2023
- 磁盘空间：至少 20GB
- 网络：位于目标VPC内，可访问互联网

**必需软件安装：**
```bash
# 1. Python 3.12+ 和 conda
sudo yum update -y
sudo yum install -y python3 python3-pip

# 2. AWS CLI v2
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
unzip awscliv2.zip
sudo ./aws/install

# 3. 验证安装
python3 --version  # 应该 >= 3.12
aws --version      # 应该是 v2.x
```

**AWS 权限配置：**
堡垒机需要以下IAM权限（建议使用IAM Role）：
```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "ec2:*",
                "elasticloadbalancing:*",
                "iam:PassRole",
                "s3:GetObject",
                "s3:PutObject",
                "s3:ListBucket"
            ],
            "Resource": "*"
        }
    ]
}
```

**SSH 密钥配置：**
```bash
# 1. 下载EC2 Key Pair私钥到堡垒机
# 2. 设置正确权限
chmod 400 /path/to/your-key.pem

# 3. 验证密钥可用
ssh-keygen -l -f /path/to/your-key.pem
```

### 2. AWS 基础设施准备

**VPC 和网络配置：**
```bash
# 1. 创建或使用现有VPC
# 2. 确保有至少2个不同可用区的子网
# 3. 子网需要有互联网访问（公有子网或配置NAT网关的私有子网）
```

**安全组配置：**
创建安全组并配置以下规则：
```bash
# DolphinScheduler 安全组规则
# 入站规则：
- SSH (22): 来源为堡垒机IP或VPC CIDR
- HTTP (80): 来源为0.0.0.0/0（如果需要公网访问）
- DolphinScheduler API (12345): 来源为VPC CIDR
- DolphinScheduler Master (5678): 来源为VPC CIDR  
- DolphinScheduler Worker (1234): 来源为VPC CIDR
- DolphinScheduler Alert (50052): 来源为VPC CIDR
- MySQL (3306): 来源为VPC CIDR（用于连接RDS）
- Zookeeper (2181): 来源为VPC CIDR

# 出站规则：
- All traffic (0-65535): 目标为0.0.0.0/0
```

**RDS MySQL 准备：**
```bash
# 1. 创建RDS MySQL 8.0实例
# 2. 创建数据库和用户
mysql -h your-rds-endpoint -u root -p
CREATE DATABASE dolphinscheduler DEFAULT CHARACTER SET utf8 DEFAULT COLLATE utf8_general_ci;
CREATE USER 'dsadmin'@'%' IDENTIFIED BY 'ds123456';
GRANT ALL PRIVILEGES ON dolphinscheduler.* TO 'dsadmin'@'%';
FLUSH PRIVILEGES;
```

**Zookeeper 集群准备：**
```bash
# 可以使用Amazon MSK或自建Zookeeper集群
# 确保DolphinScheduler节点可以访问Zookeeper端口2181
```

**S3 配置：**
```bash
# 1. 创建S3 bucket用于资源存储
aws s3 mb s3://your-dolphinscheduler-bucket --region us-east-2

# 2. 上传DolphinScheduler安装包到S3（可选，用于加速部署）
aws s3 cp apache-dolphinscheduler-3.2.0-bin.tar.gz \
    s3://your-bucket/dolphinscheduler-3.2.0/ --region us-east-2

# 3. 创建IAM Role用于EC2访问S3
# Role名称：AdminRole（或在config.yaml中指定）
```

### 3. 安装部署工具

```bash
# 1. 克隆项目到堡垒机
git clone https://github.com/tingxin/dolphinscheduler-ec2-on-aws.git
cd dolphinscheduler-ec2-on-aws

# 2. 创建Python虚拟环境（推荐）
python3 -m venv venv
source venv/bin/activate

# 3. 安装依赖
pip install -r requirements.txt

# 4. 验证安装
python cli.py --help
```

### 4. 配置文件准备

```bash
# 1. 复制配置模板
cp config.example.yaml config.yaml

# 2. 编辑配置文件
vim config.yaml
```

**必填配置项：**
```yaml
# 数据库配置
database:
  host: your-rds-endpoint.rds.amazonaws.com
  username: dsadmin
  password: ds123456
  database: dolphinscheduler

# Zookeeper配置  
registry:
  servers:
    - your-zk-host:2181

# S3存储配置
storage:
  bucket: your-dolphinscheduler-bucket
  region: us-east-2

# AWS基础配置
aws:
  region: us-east-2
  vpc_id: vpc-xxxxxxxxx
  subnets:
    - subnet_id: subnet-xxxxxxxxx
      availability_zone: us-east-2a
    - subnet_id: subnet-yyyyyyyyy  
      availability_zone: us-east-2b
  key_name: your-ec2-keypair-name
  iam_instance_profile: AdminRole
  security_groups:
    master: sg-xxxxxxxxx
    worker: sg-xxxxxxxxx
    api: sg-xxxxxxxxx
    alert: sg-xxxxxxxxx

# 集群配置
cluster:
  master:
    count: 2
    instance_type: m7i.xlarge
  worker:
    count: 3
    instance_type: m7i.xlarge
  api:
    count: 2
    instance_type: m7i.large
  alert:
    count: 1
    instance_type: m7i.large
```

### 5. 环境变量配置（可选）

```bash
# 设置SSH密钥路径
export SSH_KEY_PATH=/path/to/your-key.pem

# 设置AWS区域
export AWS_DEFAULT_REGION=us-east-2
```

## 🚀 部署命令

### 1. 验证配置

部署前先验证配置文件是否正确：

```bash
python cli.py validate --config config.yaml
```

此命令会检查：
- ✅ 配置文件格式和必填项
- ✅ AWS 资源可访问性（VPC、子网、安全组、Key Pair）
- ✅ RDS MySQL 连接和数据库权限
- ✅ Zookeeper 集群连接
- ✅ S3 bucket 访问权限
- ✅ IAM Role 权限

### 2. 创建集群

**推荐使用堡垒机部署：**
```bash
# 在堡垒机上执行（推荐方式）
# 堡垒机地址：ec2-user@18.221.252.182
ssh ec2-user@18.221.252.182 "cd /home/ec2-user/work/dolphinscheduler-ec2-on-aws && git pull && conda activate py312 && python cli.py create --config config.yaml"
```

**本地部署（需要网络连通性）：**
```bash
# 试运行（仅验证，不实际创建）
python cli.py create --config config.yaml --dry-run

# 正式创建集群
python cli.py create --config config.yaml
```

**部署过程详解：**
1. **[1/5] 加载配置** - 验证配置文件
2. **[2/5] 验证配置** - 检查AWS资源和外部依赖
3. **[3/5] 创建EC2实例** - 跨可用区创建实例（使用AMI: ami-058a8a5ab36292159）
4. **[4/5] 等待SSH访问** - 等待实例启动完成
5. **[5/5] 初始化节点** - 安装Java、MySQL client等依赖
6. **[6/6] 配置集群** - 设置SSH密钥互信和hosts文件
7. **[7/7] 部署DolphinScheduler** - 下载、配置、启动服务

**部署时间估算：**
- 小型集群（2M+3W+2A+1Alert）：约15-20分钟
- 中型集群（3M+5W+3A+2Alert）：约25-30分钟

**部署成功标志：**
```
======================================================================
✓ Cluster Creation Completed!
======================================================================
API Endpoint: http://172.31.x.x:12345/dolphinscheduler
Default credentials:
  Username: admin
  Password: dolphinscheduler123
```

## 🌐 访问和验证

### 1. Web UI 访问

**获取访问地址：**
部署成功后，控制台会显示访问信息：
```
API Endpoint: http://172.31.x.x:12345/dolphinscheduler
Default credentials:
  Username: admin
  Password: dolphinscheduler123
```

**访问方式：**

**方式1：通过堡垒机访问（推荐）**
```bash
# 1. SSH到堡垒机
ssh -i /path/to/key.pem ec2-user@18.221.252.182

# 2. 在堡垒机上使用curl测试
curl http://172.31.x.x:12345/dolphinscheduler/ui/

# 3. 设置SSH隧道进行本地访问
ssh -i /path/to/key.pem -L 8080:172.31.x.x:12345 ec2-user@18.221.252.182
# 然后在本地浏览器访问：http://localhost:8080/dolphinscheduler
```

**方式2：配置ALB公网访问**
```yaml
# 在config.yaml中启用ALB
service_config:
  api:
    load_balancer:
      enabled: true
      type: application
      scheme: internet-facing
      subnets:
        - subnet-xxxxxxxxx  # 公有子网
        - subnet-yyyyyyyyy  # 公有子网
```

### 2. 服务验证

**检查所有服务状态：**
```bash
# 查看集群状态
python cli.py status --config config.yaml

# 详细状态检查
python cli.py status --config config.yaml --detailed
```

**手动验证各组件：**
```bash
# 1. 验证Master服务（端口5678）
curl http://172.31.x.x:5678/actuator/health

# 2. 验证Worker服务（端口1234）  
curl http://172.31.x.x:1234/actuator/health

# 3. 验证API服务（端口12345）
curl http://172.31.x.x:12345/dolphinscheduler/actuator/health

# 4. 验证Alert服务（端口50052）
# Alert服务使用gRPC，需要特殊工具验证
```

**数据库验证：**
```bash
# 连接到RDS检查表结构
mysql -h your-rds-endpoint -u dsadmin -p dolphinscheduler
SHOW TABLES;  # 应该看到约50+个DolphinScheduler表
```

### 3. 功能验证

**登录Web UI：**
1. 访问 `http://your-api-endpoint:12345/dolphinscheduler`
2. 使用默认凭据登录：
   - 用户名：`admin`
   - 密码：`dolphinscheduler123`

**创建测试工作流：**
1. 点击"项目管理" → "创建项目"
2. 项目名称：`test-project`
3. 点击"工作流定义" → "创建工作流"
4. 拖拽一个Shell任务节点
5. 配置Shell脚本：`echo "Hello DolphinScheduler"`
6. 保存并运行工作流

**验证任务执行：**
1. 查看"工作流实例"页面
2. 确认任务状态为"成功"
3. 查看任务日志输出

### 4. 集群管理命令

```bash
# 查看集群详细信息
python cli.py info --config config.yaml

# 扩容Worker节点
python cli.py scale --config config.yaml --component worker --count 5

# 查看成本估算
python cli.py cost --config config.yaml

# 导出集群配置
python cli.py export --config config.yaml --output cluster-backup.json
```

### 5. 监控和日志

**查看服务日志：**
```bash
# SSH到任意节点查看日志
ssh -i /path/to/key.pem ec2-user@172.31.x.x

# 查看Master日志
sudo tail -f /opt/dolphinscheduler/master-server/logs/dolphinscheduler-master.log

# 查看Worker日志  
sudo tail -f /opt/dolphinscheduler/worker-server/logs/dolphinscheduler-worker.log

# 查看API日志
sudo tail -f /opt/dolphinscheduler/api-server/logs/dolphinscheduler-api.log
```

**系统监控：**
```bash
# 查看系统资源使用
htop
df -h
free -h

# 查看Java进程
jps -l
```

## 🔧 集群管理

### 1. 扩缩容操作

```bash
# 扩容Worker节点到5个
python cli.py scale --config config.yaml --component worker --count 5

# 缩容API节点到1个
python cli.py scale --config config.yaml --component api --count 1
```

### 2. 服务重启

```bash
# 重启所有服务
python cli.py restart --config config.yaml

# 重启特定组件
python cli.py restart --config config.yaml --component master
```

### 3. 配置更新

```bash
# 更新配置并重启服务
python cli.py update --config config.yaml
```

### 4. 删除集群

```bash
# 删除集群（保留数据）
python cli.py delete --config config.yaml --keep-data

# 完全删除（包括数据）
python cli.py delete --config config.yaml --force
```

## ❗ 常见问题

### 1. SSH连接失败

**问题现象：**
```
Permission denied (publickey)
```

**解决方案：**
```bash
# 1. 检查密钥权限
chmod 400 /path/to/your-key.pem

# 2. 验证密钥格式
ssh-keygen -l -f /path/to/your-key.pem

# 3. 检查config.yaml中的key_name配置
# key_name必须是AWS中的Key Pair名称（不含.pem后缀）
aws:
  key_name: ec2-ohio  # ✅ 正确
  # key_name: ec2-ohio.pem  # ❌ 错误
```

### 2. "No space left on device"错误

**问题现象：**
```
tar: Cannot write: No space left on device
```

**解决方案：**
已在最新版本中修复，使用`/home/ec2-user`目录而非`/tmp`：
```bash
# 确保使用最新代码
git pull origin 3.2.0dev

# 检查磁盘配置（应该是200GB）
ec2_advanced:
  master:
    root_volume_size: 200
```

### 3. S3下载速度慢

**问题现象：**
```
Downloading from S3... (very slow)
```

**解决方案：**
```bash
# 1. 检查VPC是否有S3 VPC Endpoint
aws ec2 describe-vpc-endpoints --region us-east-2

# 2. 使用优化的S3配置（已内置）
aws configure set default.s3.max_concurrent_requests 20
aws configure set default.s3.max_bandwidth 100MB/s
```

### 4. 数据库连接失败

**问题现象：**
```
Database connection failed
```

**解决方案：**
```bash
# 1. 从堡垒机测试RDS连接
mysql -h your-rds-endpoint.rds.amazonaws.com -u dsadmin -p

# 2. 检查安全组规则
# RDS安全组必须允许来自DolphinScheduler安全组的3306端口访问

# 3. 验证数据库和用户存在
SHOW DATABASES;
SELECT User, Host FROM mysql.user WHERE User='dsadmin';
```

### 5. Zookeeper连接失败

**问题现象：**
```
Zookeeper connection failed
```

**解决方案：**
```bash
# 1. 测试Zookeeper连接
telnet your-zk-host 2181

# 2. 检查安全组规则
# Zookeeper安全组必须允许来自DolphinScheduler安全组的2181端口访问
```

### 6. Web UI无法访问

**问题现象：**
```
Connection refused or timeout
```

**解决方案：**
```bash
# 1. 检查API服务状态
curl http://172.31.x.x:12345/dolphinscheduler/actuator/health

# 2. 使用SSH隧道访问
ssh -i /path/to/key.pem -L 8080:172.31.x.x:12345 ec2-user@堡垒机IP
# 然后访问 http://localhost:8080/dolphinscheduler

# 3. 检查安全组规则
# 确保12345端口对VPC CIDR开放
```

### 7. 服务启动失败

**问题现象：**
```
Service failed to start
```

**解决方案：**
```bash
# 1. 查看服务日志
sudo tail -f /opt/dolphinscheduler/*/logs/*.log

# 2. 检查Java进程
jps -l

# 3. 手动重启服务
sudo systemctl restart dolphinscheduler-master
sudo systemctl restart dolphinscheduler-worker
sudo systemctl restart dolphinscheduler-api
sudo systemctl restart dolphinscheduler-alert
```

### 8. 配置文件错误

**常见配置错误：**
```yaml
# ❌ 错误的配置
aws:
  key_name: ec2-ohio.pem  # 不应包含.pem后缀
  vpc_id: vpc-123         # VPC ID格式错误
  
database:
  host: localhost         # 应该是RDS端点
  
# ✅ 正确的配置  
aws:
  key_name: ec2-ohio
  vpc_id: vpc-0c9a0d81e8f5ca012
  
database:
  host: your-rds.cbore8wpy3mc.us-east-2.rds.amazonaws.com
```

## 架构说明

工具会自动创建跨多可用区的高可用集群：
- Master/Worker/API 节点分布在不同可用区
- ALB 提供 API 负载均衡
- 使用外部 RDS MySQL 和 Zookeeper
- 资源存储在 S3
- 所有资源打上 `ManagedBy=dolphinscheduler-cli` 标签便于管理

## 技术文档

详细设计和实现请参考 [DESIGN.md](DESIGN.md)

