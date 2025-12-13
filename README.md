# DolphinScheduler EC2 集群部署工具

在 AWS EC2 上自动化部署和管理 DolphinScheduler 3.2.0 集群的 Python CLI 工具。

## 快速开始

### 1. 堡垒机环境准备

在运行此工具的堡垒机上需要配置以下环境：

**必需软件：**
```bash
# Python 3.12+
python3 --version

# AWS CLI（已配置权限）
aws --version
aws sts get-caller-identity  # 验证 AWS 权限
```

**AWS 权限要求：**
- EC2: 创建/删除实例、查询实例状态
- ELB: 创建/删除 ALB 和 Target Group
- VPC: 查询 VPC、子网、安全组信息
- IAM: PassRole（如果使用 IAM Instance Profile）

**SSH 密钥配置：**
```bash
# 确保 SSH 私钥文件权限正确
chmod 400 /path/to/your-key.pem

# 密钥必须对应 AWS EC2 中已存在的 Key Pair
```

### 2. 安装工具

```bash
cd dolphinscheduler-ec2-on-aws

# 安装 Python 依赖
pip3 install -r requirements.txt
```

### 3. 准备配置文件

```bash
# 复制配置模板
cp config.yaml my-cluster.yaml

# 编辑配置文件，填写以下必填项：
# - database: RDS MySQL 连接信息
# - registry: Zookeeper 集群地址
# - storage: S3 bucket 名称
# - aws.region: AWS 区域
# - aws.vpc_id: VPC ID
# - aws.subnets: 子网列表（建议跨多个可用区）
# - aws.key_name: EC2 Key Pair 名称（如：ec2-ohio）
# - aws.iam_instance_profile: IAM Role 名称（用于 S3 访问）
# - aws.security_groups: 各组件的安全组 ID
# - cluster: 各组件节点数量和实例类型
```

**关键配置说明：**
- `key_name`: AWS 中的 Key Pair 名称（不含 .pem 后缀）
- `ssh_key_path`: 堡垒机上对应私钥的本地路径
- `iam_instance_profile`: 用于 EC2 访问 S3 的 IAM Role

### 4. 环境变量配置（可选）

```bash
cp .env.example .env
# 编辑 .env 文件
```

`.env` 文件示例：
```bash
AWS_DEFAULT_REGION=us-east-2
SSH_KEY_PATH=/path/to/your-key.pem
LOG_LEVEL=INFO
DS_VERSION=3.2.0
```

## 命令使用

### 1. 验证配置

部署前先验证配置文件是否正确：

```bash
python cli.py validate --config my-cluster.yaml
```

此命令会检查：
- 配置文件格式
- AWS 资源可访问性（VPC、子网、安全组）
- RDS MySQL 连接
- Zookeeper 连接
- S3 访问权限

### 2. 创建集群

```bash
# 试运行（仅验证，不实际创建）
python cli.py create --config my-cluster.yaml --dry-run

# 正式创建集群
python cli.py create --config my-cluster.yaml
```

创建过程包括：
1. 创建 EC2 实例（跨多个可用区）
2. 初始化节点（安装 Java、MySQL client 等依赖）
3. 部署 DolphinScheduler
4. 配置并启动服务
5. 创建 ALB（如果启用）

创建完成后会显示 Web UI 访问地址和默认凭据。

### 3. 查看集群状态

```bash
# 基本状态
python cli.py status --config my-cluster.yaml

# 详细状态（包含实例 ID、子网等）
python cli.py status --config my-cluster.yaml --detailed

# 查看集群详细信息（含成本估算）
python cli.py info --config my-cluster.yaml

# 导出集群信息到 JSON
python cli.py info --config my-cluster.yaml --export cluster-info.json
```

### 4. 扩缩容

```bash
# 扩容 Worker 节点到 5 个
python cli.py scale --config my-cluster.yaml --component worker --count 5

# 缩容 Master 节点到 2 个
python cli.py scale --config my-cluster.yaml --component master --count 2
```

支持扩缩容的组件：`master`、`worker`、`api`

### 5. 删除集群

```bash
# 删除集群（需要确认）
python cli.py delete --config my-cluster.yaml

# 强制删除（无需确认）
python cli.py delete --config my-cluster.yaml --force

# 删除集群但保留数据（RDS 和 S3 数据不删除）
python cli.py delete --config my-cluster.yaml --keep-data
```

删除过程：
1. 停止所有服务
2. 删除 ALB 和 Target Group
3. 终止 EC2 实例
4. 删除 EBS 卷（自动）

### 6. 清理孤立资源

如果配置文件丢失，可以通过标签清理所有资源：

```bash
python cli.py cleanup --region us-east-2
```

此命令会查找并删除所有标记为 `ManagedBy=dolphinscheduler-cli` 的资源。

## 常见问题

### SSH 连接失败

```bash
# 检查密钥权限
chmod 400 /path/to/your-key.pem

# 检查密钥名称是否匹配
# config.yaml 中的 key_name 必须是 AWS 中的 Key Pair 名称（不含 .pem）
# 例如：key_name: ec2-ohio（不是 ec2-ohio.pem）
```

### AWS 权限不足

确保运行堡垒机的 IAM 角色或用户具有以下权限：
- `ec2:*` (创建、查询、终止实例)
- `elasticloadbalancing:*` (创建、删除 ALB)
- `iam:PassRole` (如果使用 IAM Instance Profile)

### 配置文件中的 key_name 作用

- `key_name`: AWS EC2 Key Pair 的名称，用于创建实例时注入公钥
- `ssh_key_path`: 堡垒机上对应私钥的本地路径，CLI 用它连接实例

示例：
```yaml
aws:
  key_name: ec2-ohio              # AWS 中的 Key Pair 名称
  ssh_key_path: /path/to/ec2-ohio.pem  # 本地私钥路径（在 .env 中配置）
```

### 数据库连接测试

```bash
# 从堡垒机测试 RDS 连接
mysql -h your-rds-endpoint.rds.amazonaws.com -u dolphinscheduler -p
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

## License

Apache License 2.0
