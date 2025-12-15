# DolphinScheduler HDFS 存储配置修复指南

## 问题描述

DolphinScheduler 3.2.0 资源中心上传文件时报错：
```
Mkdirs failed to create file:/dolphinscheduler/default/resources
java.io.IOException: Mkdirs failed to create file:/dolphinscheduler/default/resources
at org.apache.hadoop.fs.RawLocalFileSystem.create(RawLocalFileSystem.java:319)
```

## 根本原因

1. **配置文件位置错误**：DolphinScheduler 3.2.0 使用 `api-server/conf/common.properties` 而不是根目录的 `conf/common.properties`
2. **HDFS 配置未正确设置**：
   - `resource.storage.type=LOCAL` 应为 `HDFS`
   - `resource.hdfs.fs.defaultFS` 地址不正确
   - `resource.hdfs.root.user` 用户名不正确
3. **Hadoop 配置文件**：需要将 `core-site.xml` 和 `hdfs-site.xml` 复制到 `api-server/conf/` 目录

## 修复步骤

### 1. 复制 Hadoop 配置文件（从 EMR Master）

```bash
EMR_MASTER="172.31.6.163"
KEY_FILE="/path/to/ec2-ohio.pem"
INSTALL_PATH="/opt/dolphinscheduler"

# 从 EMR master 复制配置文件
scp -o StrictHostKeyChecking=no -i ${KEY_FILE} hadoop@${EMR_MASTER}:/etc/hadoop/conf/core-site.xml /tmp/
scp -o StrictHostKeyChecking=no -i ${KEY_FILE} hadoop@${EMR_MASTER}:/etc/hadoop/conf/hdfs-site.xml /tmp/

# 复制到所有组件目录
for component in api-server master-server worker-server alert-server tools; do
    sudo mkdir -p ${INSTALL_PATH}/${component}/conf
    sudo cp /tmp/core-site.xml ${INSTALL_PATH}/${component}/conf/
    sudo cp /tmp/hdfs-site.xml ${INSTALL_PATH}/${component}/conf/
    sudo chown -R dolphinscheduler:dolphinscheduler ${INSTALL_PATH}/${component}/conf
done
```

### 2. 获取正确的 HDFS NameNode 地址

从 `core-site.xml` 中获取：
```bash
cat /opt/dolphinscheduler/api-server/conf/core-site.xml | grep -A 1 "fs.defaultFS"
# 输出示例: hdfs://ip-172-31-6-163.us-east-2.compute.internal:8020
```

### 3. 修改 common.properties（每个组件目录）

```bash
HDFS_ADDRESS="hdfs://ip-172-31-6-163.us-east-2.compute.internal:8020"
HDFS_USER="hadoop"

for component in api-server master-server worker-server alert-server; do
    CONF_FILE="${INSTALL_PATH}/${component}/conf/common.properties"
    
    sudo sed -i "s|resource.storage.type=LOCAL|resource.storage.type=HDFS|g" ${CONF_FILE}
    sudo sed -i "s|resource.hdfs.fs.defaultFS=.*|resource.hdfs.fs.defaultFS=${HDFS_ADDRESS}|g" ${CONF_FILE}
    sudo sed -i "s|resource.hdfs.root.user=.*|resource.hdfs.root.user=${HDFS_USER}|g" ${CONF_FILE}
done
```

### 4. 重启服务

```bash
sudo -u dolphinscheduler /opt/dolphinscheduler/bin/dolphinscheduler-daemon.sh stop api-server
sleep 3
sudo -u dolphinscheduler /opt/dolphinscheduler/bin/dolphinscheduler-daemon.sh start api-server
```

## 关键配置项

| 配置项 | 值 | 说明 |
|--------|-----|------|
| resource.storage.type | HDFS | 存储类型 |
| resource.storage.upload.base.path | /dolphinscheduler | HDFS 上的存储路径 |
| resource.hdfs.root.user | hadoop | HDFS 用户（EMR 默认是 hadoop） |
| resource.hdfs.fs.defaultFS | hdfs://hostname:8020 | HDFS NameNode 地址（使用 core-site.xml 中的地址） |

## 注意事项

1. **地址一致性**：`resource.hdfs.fs.defaultFS` 必须与 `core-site.xml` 中的 `fs.defaultFS` 一致
2. **配置文件位置**：DolphinScheduler 3.2.0 读取的是各组件目录下的 `conf/common.properties`，不是根目录
3. **不需要安装 Hadoop 客户端**：只需要配置文件，DolphinScheduler 自带 Hadoop 库
