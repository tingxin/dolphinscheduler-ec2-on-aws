#!/bin/bash

# DolphinScheduler 插件安装修复脚本
# 解决 Maven wrapper 缺失和 MySQL JDBC 驱动下载失败的问题

set -e

# 配置变量
INSTALL_PATH="/opt/dolphinscheduler"
DEPLOY_USER="dolphinscheduler"
VERSION="3.3.2"

echo "=========================================="
echo "DolphinScheduler 插件安装修复脚本"
echo "=========================================="

# 检查是否以 root 权限运行
if [[ $EUID -ne 0 ]]; then
   echo "此脚本需要 root 权限运行"
   echo "请使用: sudo $0"
   exit 1
fi

# 检查安装路径是否存在
if [ ! -d "$INSTALL_PATH" ]; then
    echo "错误: DolphinScheduler 安装路径不存在: $INSTALL_PATH"
    echo "请先运行 create 命令创建集群"
    exit 1
fi

cd "$INSTALL_PATH"

echo "1. 检查和修复 Maven wrapper..."

# 创建 .mvn/wrapper 目录
mkdir -p .mvn/wrapper

# 下载 Maven wrapper 配置文件
echo "下载 maven-wrapper.properties..."
if ! wget -O .mvn/wrapper/maven-wrapper.properties "https://repo.maven.apache.org/maven2/org/apache/maven/wrapper/maven-wrapper/3.2.0/maven-wrapper-3.2.0.properties" 2>/dev/null; then
    echo "wget 失败，尝试 curl..."
    if ! curl -L -o .mvn/wrapper/maven-wrapper.properties "https://repo.maven.apache.org/maven2/org/apache/maven/wrapper/maven-wrapper/3.2.0/maven-wrapper-3.2.0.properties" 2>/dev/null; then
        echo "创建基本的 maven-wrapper.properties..."
        cat > .mvn/wrapper/maven-wrapper.properties << 'EOF'
distributionUrl=https://repo.maven.apache.org/maven2/org/apache/maven/apache-maven/3.8.8/apache-maven-3.8.8-bin.zip
wrapperUrl=https://repo.maven.apache.org/maven2/org/apache/maven/wrapper/maven-wrapper/3.2.0/maven-wrapper-3.2.0.jar
EOF
    fi
fi

# 下载 Maven wrapper JAR 文件
echo "下载 maven-wrapper.jar..."
if ! wget -O .mvn/wrapper/maven-wrapper.jar "https://repo.maven.apache.org/maven2/org/apache/maven/wrapper/maven-wrapper/3.2.0/maven-wrapper-3.2.0.jar" 2>/dev/null; then
    echo "wget 失败，尝试 curl..."
    if ! curl -L -o .mvn/wrapper/maven-wrapper.jar "https://repo.maven.apache.org/maven2/org/apache/maven/wrapper/maven-wrapper/3.2.0/maven-wrapper-3.2.0.jar" 2>/dev/null; then
        echo "警告: 无法下载 maven-wrapper.jar"
        echo "检查是否有系统 Maven..."
        if command -v mvn >/dev/null 2>&1; then
            echo "找到系统 Maven，创建替代的 mvnw 脚本..."
            cat > mvnw << 'EOF'
#!/bin/bash
exec mvn "$@"
EOF
            chmod +x mvnw
        else
            echo "错误: 没有可用的 Maven"
            exit 1
        fi
    fi
fi

# 设置权限
chmod +x mvnw 2>/dev/null || true
chown -R $DEPLOY_USER:$DEPLOY_USER .mvn/ 2>/dev/null || true
chown $DEPLOY_USER:$DEPLOY_USER mvnw 2>/dev/null || true

echo "✓ Maven wrapper 修复完成"

echo ""
echo "2. 手动下载 MySQL JDBC 驱动..."

# 创建 libs 目录
mkdir -p libs

cd /tmp

# 尝试下载 MySQL Connector/J (新版本)
DRIVER_DOWNLOADED=false

echo "尝试下载 MySQL Connector/J 8.0.33..."
if wget -O mysql-connector-j-8.0.33.jar "https://repo1.maven.org/maven2/com/mysql/mysql-connector-j/8.0.33/mysql-connector-j-8.0.33.jar" 2>/dev/null; then
    cp mysql-connector-j-8.0.33.jar "$INSTALL_PATH/libs/"
    chown $DEPLOY_USER:$DEPLOY_USER "$INSTALL_PATH/libs/mysql-connector-j-8.0.33.jar"
    DRIVER_DOWNLOADED=true
    echo "✓ MySQL Connector/J 8.0.33 下载成功"
elif curl -L -o mysql-connector-j-8.0.33.jar "https://repo1.maven.org/maven2/com/mysql/mysql-connector-j/8.0.33/mysql-connector-j-8.0.33.jar" 2>/dev/null; then
    cp mysql-connector-j-8.0.33.jar "$INSTALL_PATH/libs/"
    chown $DEPLOY_USER:$DEPLOY_USER "$INSTALL_PATH/libs/mysql-connector-j-8.0.33.jar"
    DRIVER_DOWNLOADED=true
    echo "✓ MySQL Connector/J 8.0.33 下载成功 (curl)"
fi

# 如果新版本失败，尝试旧版本
if [ "$DRIVER_DOWNLOADED" = "false" ]; then
    echo "尝试下载 MySQL Connector/Java 8.0.32..."
    if wget -O mysql-connector-java-8.0.32.jar "https://repo1.maven.org/maven2/mysql/mysql-connector-java/8.0.32/mysql-connector-java-8.0.32.jar" 2>/dev/null; then
        cp mysql-connector-java-8.0.32.jar "$INSTALL_PATH/libs/"
        chown $DEPLOY_USER:$DEPLOY_USER "$INSTALL_PATH/libs/mysql-connector-java-8.0.32.jar"
        DRIVER_DOWNLOADED=true
        echo "✓ MySQL Connector/Java 8.0.32 下载成功"
    elif curl -L -o mysql-connector-java-8.0.32.jar "https://repo1.maven.org/maven2/mysql/mysql-connector-java/8.0.32/mysql-connector-java-8.0.32.jar" 2>/dev/null; then
        cp mysql-connector-java-8.0.32.jar "$INSTALL_PATH/libs/"
        chown $DEPLOY_USER:$DEPLOY_USER "$INSTALL_PATH/libs/mysql-connector-java-8.0.32.jar"
        DRIVER_DOWNLOADED=true
        echo "✓ MySQL Connector/Java 8.0.32 下载成功 (curl)"
    fi
fi

# 最后尝试 8.0.30
if [ "$DRIVER_DOWNLOADED" = "false" ]; then
    echo "尝试下载 MySQL Connector/Java 8.0.30..."
    if wget -O mysql-connector-java-8.0.30.jar "https://repo1.maven.org/maven2/mysql/mysql-connector-java/8.0.30/mysql-connector-java-8.0.30.jar" 2>/dev/null; then
        cp mysql-connector-java-8.0.30.jar "$INSTALL_PATH/libs/"
        chown $DEPLOY_USER:$DEPLOY_USER "$INSTALL_PATH/libs/mysql-connector-java-8.0.30.jar"
        DRIVER_DOWNLOADED=true
        echo "✓ MySQL Connector/Java 8.0.30 下载成功"
    elif curl -L -o mysql-connector-java-8.0.30.jar "https://repo1.maven.org/maven2/mysql/mysql-connector-java/8.0.30/mysql-connector-java-8.0.30.jar" 2>/dev/null; then
        cp mysql-connector-java-8.0.30.jar "$INSTALL_PATH/libs/"
        chown $DEPLOY_USER:$DEPLOY_USER "$INSTALL_PATH/libs/mysql-connector-java-8.0.30.jar"
        DRIVER_DOWNLOADED=true
        echo "✓ MySQL Connector/Java 8.0.30 下载成功 (curl)"
    fi
fi

if [ "$DRIVER_DOWNLOADED" = "false" ]; then
    echo "错误: 无法从所有源下载 MySQL JDBC 驱动"
    exit 1
fi

echo ""
echo "3. 验证安装..."

cd "$INSTALL_PATH"

echo "Maven wrapper 文件:"
ls -la .mvn/wrapper/ 2>/dev/null || echo "  .mvn/wrapper/ 目录不存在"
ls -la mvnw 2>/dev/null || echo "  mvnw 脚本不存在"

echo ""
echo "MySQL JDBC 驱动:"
ls -la libs/mysql-connector-*.jar 2>/dev/null || echo "  没有找到 MySQL 驱动"

echo ""
echo "4. 尝试重新运行插件安装..."

# 切换到部署用户并运行插件安装
sudo -u $DEPLOY_USER bash -c "cd $INSTALL_PATH && bash bin/install-plugins.sh $VERSION"

if [ $? -eq 0 ]; then
    echo "✓ 插件安装成功!"
else
    echo "插件安装仍然失败，但 MySQL JDBC 驱动已手动安装"
    echo "可以继续进行数据库初始化"
fi

echo ""
echo "=========================================="
echo "修复完成!"
echo "=========================================="
echo ""
echo "接下来可以:"
echo "1. 重新运行 create 命令"
echo "2. 或者手动继续数据库初始化步骤"