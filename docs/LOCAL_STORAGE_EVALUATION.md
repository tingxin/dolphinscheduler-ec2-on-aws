# DolphinScheduler 本地目录存储替代 HDFS 可行性评估

## 1. 背景

### 1.1 当前问题
DolphinScheduler 3.2.0 使用 HDFS 作为资源存储时，上传文件报错：
```
Mkdirs failed to create file:/dolphinscheduler/default/resources
java.io.IOException: Mkdirs failed to create file:/dolphinscheduler/default/resources
```

### 1.2 问题根因
1. **HDFS 连接配置复杂**：需要从 EMR 复制 `core-site.xml` 和 `hdfs-site.xml`
2. **配置文件位置问题**：DolphinScheduler 3.2.0 读取 `api-server/conf/common.properties`，而非根目录
3. **HDFS 地址不一致**：`resource.hdfs.fs.defaultFS` 必须与 `core-site.xml` 中的 `fs.defaultFS` 完全一致
4. **依赖外部服务**：需要 EMR 集群持续运行，增加运维复杂度和成本

### 1.3 当前解决方案
采用"先部署 → 更新配置 → 重启 API Server"的方式，通过 `apply_hdfs_config_to_api_servers()` 函数实现。

---

## 2. 本地存储方案评估

### 2.1 可行性结论

**✅ 完全可行**

DolphinScheduler 3.2.0 原生支持 `LOCAL` 存储类型，现有代码已包含基础实现。

### 2.2 现有代码支持情况

| 模块 | 文件 | 支持状态 | 说明 |
|------|------|----------|------|
| 配置生成 | `config_generator.py` | ✅ 已支持 | `generate_application_yaml_v320()` 和 `generate_common_properties_v320()` 已包含 LOCAL 配置 |
| 目录创建 | `installer.py` | ✅ 已支持 | `create_resource_directories()` 创建 `/tmp/dolphinscheduler` 目录 |
| 服务管理 | `service_manager.py` | ⚠️ 需调整 | 当前包含 HDFS 特定逻辑，需要条件判断 |
| 配置文件 | `config.yaml` | ⚠️ 需修改 | 当前配置为 HDFS，需改为 LOCAL |

### 2.3 现有 LOCAL 存储配置

**application.yaml 配置：**
```yaml
resource-storage:
  type: LOCAL
  local:
    base-dir: /opt/dolphinscheduler/resources
```

**common.properties 配置：**
```properties
resource.storage.type=LOCAL
resource.local.basedir=/tmp/dolphinscheduler
```

---

## 3. 实现方案对比

### 3.1 方案 A：简单本地存储（单节点）

**架构：**
```
┌─────────────────┐
│   API Server    │
│  ┌───────────┐  │
│  │ /opt/ds/  │  │  ← 资源文件存储在 API Server 本地
│  │ resources │  │
│  └───────────┘  │
└─────────────────┘
        ↓ 任务分发
┌─────────────────┐
│  Worker Nodes   │  ← Worker 无法直接访问资源文件
└─────────────────┘
```

**优点：**
- 实现简单，无需额外基础设施
- 部署快速，配置简单
- 无外部依赖

**缺点：**
- Worker 节点无法直接访问上传的资源文件
- 仅适用于不需要资源文件的任务（如 Shell、HTTP 任务）
- 不支持 UDF 函数、SQL 脚本等需要资源文件的场景

**适用场景：**
- 开发/测试环境
- 仅运行简单任务的场景

**实现复杂度：** ⭐ 低

---

### 3.2 方案 B：共享存储（AWS EFS）

**架构：**
```
                    ┌─────────────────┐
                    │    AWS EFS      │
                    │ /efs/dolphin/   │
                    │   resources     │
                    └────────┬────────┘
                             │ NFS Mount
        ┌────────────────────┼────────────────────┐
        │                    │                    │
        ▼                    ▼                    ▼
┌───────────────┐   ┌───────────────┐   ┌───────────────┐
│  API Server   │   │   Worker 1    │   │   Worker 2    │
│ /mnt/efs/ds/  │   │ /mnt/efs/ds/  │   │ /mnt/efs/ds/  │
└───────────────┘   └───────────────┘   └───────────────┘
```

**优点：**
- 所有节点共享同一存储
- 支持所有 DolphinScheduler 功能
- AWS 托管服务，高可用
- 自动扩展，无需管理存储容量

**缺点：**
- 需要额外 AWS 资源（EFS）
- 增加少量成本（约 $0.30/GB/月）
- 需要配置 VPC 和安全组

**适用场景：**
- 生产环境
- 需要完整资源中心功能的场景

**实现复杂度：** ⭐⭐ 中等

**成本估算：**
| 存储量 | 月成本 (us-east-2) |
|--------|-------------------|
| 10 GB  | ~$3.00           |
| 50 GB  | ~$15.00          |
| 100 GB | ~$30.00          |

---

### 3.3 方案 C：本地存储 + 文件同步

**架构：**
```
┌─────────────────┐
│   API Server    │
│  ┌───────────┐  │
│  │ resources │──┼──→ rsync/scp 同步
│  └───────────┘  │
└─────────────────┘
                    ↓
        ┌───────────┴───────────┐
        ▼                       ▼
┌───────────────┐       ┌───────────────┐
│   Worker 1    │       │   Worker 2    │
│  resources/   │       │  resources/   │
└───────────────┘       └───────────────┘
```

**优点：**
- 无需额外基础设施
- 每个节点独立存储，故障隔离

**缺点：**
- 实现复杂，需要同步机制
- 同步延迟可能导致任务失败
- 存储空间浪费（每个节点都有副本）
- 需要处理同步冲突

**适用场景：**
- 不推荐用于生产环境

**实现复杂度：** ⭐⭐⭐ 高

---

## 4. 推荐方案

### 4.1 开发/测试环境
**推荐：方案 A（简单本地存储）**

配置修改：
```yaml
# config.yaml
storage:
  type: LOCAL
  local:
    base_path: /opt/dolphinscheduler/resources
```

### 4.2 生产环境
**推荐：方案 B（AWS EFS 共享存储）**

配置修改：
```yaml
# config.yaml
storage:
  type: LOCAL
  local:
    base_path: /mnt/efs/dolphinscheduler/resources
  efs:
    enabled: true
    file_system_id: fs-xxxxxxxx
    mount_point: /mnt/efs
```

---

## 5. 实现计划

### 5.1 Phase 1：基础 LOCAL 存储支持（方案 A）

**修改文件：**

1. **config.yaml**
```yaml
storage:
  type: LOCAL  # 从 HDFS 改为 LOCAL
  local:
    base_path: /opt/dolphinscheduler/resources
    create_if_not_exists: true
```

2. **src/deploy/config_generator.py**
   - 增强 `generate_common_properties_v320()` 支持自定义本地路径
   - 更新 `generate_application_yaml_v320()` 的 LOCAL 配置

3. **src/deploy/installer.py**
   - 修改 `create_resource_directories()` 支持自定义路径
   - 确保在所有节点创建目录

4. **src/deploy/service_manager.py**
   - 添加存储类型判断，LOCAL 时跳过 HDFS 配置

**预计工作量：** 2-4 小时

### 5.2 Phase 2：EFS 共享存储支持（方案 B）

**新增文件：**
- `src/aws/efs.py` - EFS 创建和管理

**修改文件：**
1. **config.yaml** - 添加 EFS 配置项
2. **src/deploy/node_initializer.py** - 添加 EFS 挂载逻辑
3. **src/commands/create.py** - 集成 EFS 创建流程

**预计工作量：** 4-8 小时

---

## 6. 风险评估

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| Worker 无法访问资源文件（方案 A） | 高 | 明确文档说明限制，或升级到方案 B |
| EFS 挂载失败 | 中 | 添加重试机制和健康检查 |
| 存储空间不足 | 低 | EFS 自动扩展，或配置监控告警 |
| 权限问题 | 中 | 确保 dolphinscheduler 用户有读写权限 |

---

## 7. 结论

1. **本地存储替代 HDFS 完全可行**，DolphinScheduler 原生支持
2. **开发/测试环境**建议使用简单本地存储（方案 A）
3. **生产环境**建议使用 AWS EFS 共享存储（方案 B）
4. 实现工作量较小，Phase 1 可在 2-4 小时内完成
5. 相比 HDFS，本地/EFS 存储配置更简单，运维成本更低

---

## 8. 附录

### 8.1 DolphinScheduler 存储类型对比

| 特性 | LOCAL | HDFS | S3 |
|------|-------|------|-----|
| 配置复杂度 | 低 | 高 | 中 |
| 外部依赖 | 无 | EMR/Hadoop | AWS S3 |
| 跨节点共享 | 需 NFS/EFS | 原生支持 | 原生支持 |
| 成本 | 低 | 高（EMR） | 中 |
| 运维复杂度 | 低 | 高 | 低 |

### 8.2 相关代码位置

- 配置生成：`src/deploy/config_generator.py`
- 安装部署：`src/deploy/installer.py`
- 服务管理：`src/deploy/service_manager.py`
- 节点初始化：`src/deploy/node_initializer.py`
