# DolphinScheduler EC2 集群部署工具

在 AWS EC2 上自动化部署和管理 DolphinScheduler 3.2.0 集群的 Python CLI 工具。

## 📋 部署要求

### 1. 堡垒机环境准备

**创建堡垒机：**
```bash
# 1. 在AWS控制台创建EC2实例
# - AMI: Amazon Linux 2023 (ami-058a8a5ab36292159)
# - 实例类型: t3.medium 或更高
# - 存储: 20GB gp3
# - VPC: 选择目标VPC
# - 子网: 选择公有子网（需要互联网访问）
# - 安全组: 允许SSH (22)和必要的出站流量
# - Key Pair: 选择或创建SSH密钥对

# 2. 分配弹性IP（可选，便于固定访问）
aws ec2 allocate-address --domain vpc
aws ec2 associate-address --instance-id i-xxxxxxxxx --allocation-id eipalloc-xxxxxxxxx
```

**堡垒机软件环境设置：**
```bash
# SSH连接到堡垒机
ssh -i /path/to/your-key.pem ec2-user@your-bastion-ip

# 1. 系统更新
sudo yum update -y

# 2. 安装基础工具
sudo yum install -y git wget curl unzip htop mysql

# 3. 安装Python 3.12和conda
# 下载Miniconda
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
bash Miniconda3-latest-Linux-x86_64.sh -b -p $HOME/miniconda3
echo 'export PATH="$HOME/miniconda3/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc

# 创建Python 3.12环境
conda create -n py312 python=3.12 -y
conda activate py312

# 4. 安装AWS CLI v2
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
unzip awscliv2.zip
sudo ./aws/install

# 5. 验证安装
python --version    # 应该是 3.12.x
aws --version      # 应该是 v2.x
conda --version    # 验证conda可用
```

**AWS权限配置：**
为堡垒机创建IAM Role并附加以下策略：
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
                "s3:*",
                "rds:DescribeDBInstances",
                "elasticmapreduce:ListClusters",
                "elasticmapreduce:DescribeCluster"
            ],
            "Resource": "*"
        }
    ]
}
```

**SSH密钥配置：**
```bash
# 1. 将EC2 Key Pair私钥上传到堡垒机
scp -i /path/to/your-key.pem /path/to/your-key.pem ec2-user@bastion-ip:~/

# 2. 在堡垒机上设置密钥权限
chmod 400 ~/your-key.pem

# 3. 配置SSH客户端（可选）
cat >> ~/.ssh/config << EOF
Host *
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null
    ServerAliveInterval 60
    ServerAliveCountMax 3
EOF

# 4. 验证密钥可用
ssh-keygen -l -f ~/your-key.pem
```

**项目代码准备：**
```bash
# 在堡垒机上克隆项目
cd /home/ec2-user/work
git clone https://github.com/tingxin/dolphinscheduler-ec2-on-aws.git
cd dolphinscheduler-ec2-on-aws
git checkout 3.2.0dev

# 安装Python依赖
conda activate py312
pip install -r requirements.txt

# 验证工具可用
python cli.py --help
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

**RDS MySQL 数据库准备：**

*步骤1：创建RDS实例*
```bash
# 在AWS控制台创建RDS MySQL 8.0实例
# - 引擎版本: MySQL 8.0.35 或更高
# - 实例类型: db.t3.medium 或更高
# - 存储: 100GB gp3（可根据需要调整）
# - 多可用区: 建议启用（生产环境）
# - VPC: 选择与DolphinScheduler相同的VPC
# - 子网组: 选择数据库子网组
# - 安全组: 允许来自DolphinScheduler安全组的3306端口访问
# - 数据库名称: 留空（稍后手动创建）
# - 主用户名: root
# - 主密码: 设置强密码
```

*步骤2：配置数据库和用户权限*
```bash
# 从堡垒机连接到RDS
mysql -h your-rds-endpoint.cbore8wpy3mc.us-east-2.rds.amazonaws.com -u root -p

# 创建DolphinScheduler数据库
CREATE DATABASE dolphinscheduler DEFAULT CHARACTER SET utf8mb4 DEFAULT COLLATE utf8mb4_unicode_ci;

# 创建专用用户并授权
CREATE USER 'dsadmin'@'%' IDENTIFIED BY 'ds123456';
GRANT ALL PRIVILEGES ON dolphinscheduler.* TO 'dsadmin'@'%';

# 授予必要的系统权限（DolphinScheduler需要）
GRANT SELECT ON mysql.proc TO 'dsadmin'@'%';
GRANT SELECT ON information_schema.* TO 'dsadmin'@'%';
GRANT PROCESS ON *.* TO 'dsadmin'@'%';

# 刷新权限
FLUSH PRIVILEGES;

# 验证用户权限
SHOW GRANTS FOR 'dsadmin'@'%';

# 测试连接
mysql -h your-rds-endpoint -u dsadmin -p dolphinscheduler
SHOW DATABASES;
USE dolphinscheduler;
SHOW TABLES;  # 应该为空（初始状态）
```

*步骤3：优化MySQL配置（可选）*
```sql
-- 在RDS参数组中设置以下参数（推荐值）
-- max_connections = 1000
-- innodb_buffer_pool_size = 70% of available memory
-- innodb_log_file_size = 256M
-- query_cache_size = 0 (MySQL 8.0中已废弃)
-- sql_mode = STRICT_TRANS_TABLES,NO_ZERO_DATE,NO_ZERO_IN_DATE,ERROR_FOR_DIVISION_BY_ZERO
```

**EMR Zookeeper 集群准备：**

*方式1：使用Amazon EMR（推荐）*
```bash
# 1. 在AWS控制台创建EMR集群
# - EMR版本: 6.15.0 或更高
# - 应用程序: 选择Zookeeper
# - 实例类型: 
#   - Master: m5.xlarge
#   - Core: m5.large (至少3个节点，奇数个)
# - VPC: 选择与DolphinScheduler相同的VPC
# - 子网: 选择私有子网
# - 安全组: 允许来自DolphinScheduler安全组的2181端口访问

# 2. 获取Zookeeper连接信息
aws emr describe-cluster --cluster-id j-xxxxxxxxx
# 记录Master节点的私有IP地址

# 3. 测试Zookeeper连接
telnet master-private-ip 2181
# 输入: ruok
# 应该返回: imok
```

*方式2：使用Amazon MSK（Kafka自带Zookeeper）*
```bash
# 1. 创建MSK集群
aws kafka create-cluster \
    --cluster-name dolphinscheduler-zk \
    --broker-node-group-info file://broker-info.json \
    --kafka-version "2.8.1"

# 2. 获取Zookeeper连接字符串
aws kafka describe-cluster --cluster-arn arn:aws:kafka:region:account:cluster/name
```

*方式3：自建Zookeeper集群*
```bash
# 在3个EC2实例上安装Zookeeper
# 实例配置: t3.medium, Amazon Linux 2023
# 确保跨不同可用区部署

# 每个节点执行：
sudo yum install -y java-11-amazon-corretto
wget https://downloads.apache.org/zookeeper/zookeeper-3.8.3/apache-zookeeper-3.8.3-bin.tar.gz
tar -xzf apache-zookeeper-3.8.3-bin.tar.gz
sudo mv apache-zookeeper-3.8.3-bin /opt/zookeeper

# 配置zoo.cfg（每个节点）
sudo tee /opt/zookeeper/conf/zoo.cfg << EOF
tickTime=2000
dataDir=/var/lib/zookeeper
clientPort=2181
initLimit=5
syncLimit=2
server.1=zk1-private-ip:2888:3888
server.2=zk2-private-ip:2888:3888
server.3=zk3-private-ip:2888:3888
EOF

# 设置节点ID（每个节点不同）
sudo mkdir -p /var/lib/zookeeper
echo "1" | sudo tee /var/lib/zookeeper/myid  # 节点1
# echo "2" | sudo tee /var/lib/zookeeper/myid  # 节点2
# echo "3" | sudo tee /var/lib/zookeeper/myid  # 节点3

# 启动Zookeeper
sudo /opt/zookeeper/bin/zkServer.sh start
```

**S3 存储和安装包准备：**

*步骤1：创建S3 Bucket*
```bash
# 创建专用bucket
aws s3 mb s3://your-dolphinscheduler-bucket --region us-east-2

# 配置bucket策略（可选，用于访问控制）
aws s3api put-bucket-policy --bucket your-dolphinscheduler-bucket --policy file://bucket-policy.json
```

*步骤2：预下载DolphinScheduler安装包到S3（强烈推荐）*
```bash
# 在堡垒机上下载官方安装包
cd /tmp
wget https://archive.apache.org/dist/dolphinscheduler/3.2.0/apache-dolphinscheduler-3.2.0-bin.tar.gz

# 验证下载完整性
ls -lh apache-dolphinscheduler-3.2.0-bin.tar.gz
# 应该约859MB

# 上传到S3（加速后续部署）
aws s3 cp apache-dolphinscheduler-3.2.0-bin.tar.gz \
    s3://your-bucket/dolphinischeduler-3.2.0/apache-dolphinscheduler-3.2.0-bin.tar.gz \
    --region us-east-2

# 验证上传成功
aws s3 ls s3://your-bucket/dolphinischeduler-3.2.0/
```

*步骤3：创建IAM Role用于EC2访问S3*
```bash
# 创建信任策略文件
cat > trust-policy.json << EOF
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {
                "Service": "ec2.amazonaws.com"
            },
            "Action": "sts:AssumeRole"
        }
    ]
}
EOF

# 创建权限策略文件
cat > s3-access-policy.json << EOF
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "s3:GetObject",
                "s3:PutObject",
                "s3:DeleteObject",
                "s3:ListBucket"
            ],
            "Resource": [
                "arn:aws:s3:::your-dolphinscheduler-bucket",
                "arn:aws:s3:::your-dolphinscheduler-bucket/*"
            ]
        }
    ]
}
EOF

# 创建IAM Role
aws iam create-role --role-name DolphinSchedulerS3Role --assume-role-policy-document file://trust-policy.json
aws iam put-role-policy --role-name DolphinSchedulerS3Role --policy-name S3Access --policy-document file://s3-access-policy.json

# 创建实例配置文件
aws iam create-instance-profile --instance-profile-name DolphinSchedulerS3Role
aws iam add-role-to-instance-profile --instance-profile-name DolphinSchedulerS3Role --role-name DolphinSchedulerS3Role
```

*步骤4：配置S3 VPC端点（可选，提升性能）*
```bash
# 创建S3 VPC端点以提升访问速度
aws ec2 create-vpc-endpoint \
    --vpc-id vpc-xxxxxxxxx \
    --service-name com.amazonaws.us-east-2.s3 \
    --route-table-ids rtb-xxxxxxxxx
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

