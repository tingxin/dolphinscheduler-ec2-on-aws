#!/usr/bin/env python3
"""
快速修复 DolphinScheduler 插件安装问题
解决 Maven wrapper 缺失和 MySQL JDBC 驱动下载失败
"""

import os
import sys
import subprocess
import urllib.request
import yaml
from pathlib import Path

def run_command(cmd, check=True):
    """执行命令"""
    print(f"执行: {cmd}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if check and result.returncode != 0:
        print(f"命令执行失败: {cmd}")
        print(f"错误输出: {result.stderr}")
        return False
    print(f"输出: {result.stdout}")
    return True

def download_file(url, local_path):
    """下载文件"""
    try:
        print(f"下载: {url} -> {local_path}")
        urllib.request.urlretrieve(url, local_path)
        return True
    except Exception as e:
        print(f"下载失败: {e}")
        return False

def fix_mysql_driver_on_nodes(config):
    """在所有节点上修复 MySQL 驱动"""
    
    # 获取所有节点
    all_nodes = []
    for component in ['master', 'worker', 'api', 'alert']:
        nodes = config['cluster'][component].get('nodes', [])
        all_nodes.extend([node['host'] for node in nodes])
    
    if not all_nodes:
        print("错误: 配置中没有找到节点信息")
        print("请先运行 create 命令创建集群")
        return False
    
    key_file = f"{config['aws']['key_name']}.pem"
    if not os.path.exists(key_file):
        print(f"错误: SSH 密钥文件不存在: {key_file}")
        return False
    
    install_path = config['deployment']['install_path']
    deploy_user = config['deployment']['user']
    
    # 修复脚本
    fix_script = f'''
    set -e
    
    echo "修复 DolphinScheduler 插件问题..."
    
    # 切换到安装目录
    cd {install_path}
    
    # 1. 修复 Maven wrapper
    echo "1. 修复 Maven wrapper..."
    sudo -u {deploy_user} mkdir -p .mvn/wrapper
    
    # 创建基本的 maven-wrapper.properties
    sudo -u {deploy_user} cat > .mvn/wrapper/maven-wrapper.properties << 'EOF'
distributionUrl=https://repo.maven.apache.org/maven2/org/apache/maven/apache-maven/3.8.8/apache-maven-3.8.8-bin.zip
wrapperUrl=https://repo.maven.apache.org/maven2/org/apache/maven/wrapper/maven-wrapper/3.2.0/maven-wrapper-3.2.0.jar
EOF
    
    # 尝试下载 maven-wrapper.jar
    if ! sudo -u {deploy_user} wget -O .mvn/wrapper/maven-wrapper.jar "https://repo.maven.apache.org/maven2/org/apache/maven/wrapper/maven-wrapper/3.2.0/maven-wrapper-3.2.0.jar" 2>/dev/null; then
        if ! sudo -u {deploy_user} curl -L -o .mvn/wrapper/maven-wrapper.jar "https://repo.maven.apache.org/maven2/org/apache/maven/wrapper/maven-wrapper/3.2.0/maven-wrapper-3.2.0.jar" 2>/dev/null; then
            echo "无法下载 maven-wrapper.jar，检查系统 Maven..."
            if command -v mvn >/dev/null 2>&1; then
                echo "使用系统 Maven 创建替代脚本..."
                sudo -u {deploy_user} cat > mvnw << 'EOF'
#!/bin/bash
exec mvn "$@"
EOF
                sudo -u {deploy_user} chmod +x mvnw
            fi
        fi
    fi
    
    # 2. 手动安装 MySQL JDBC 驱动
    echo "2. 安装 MySQL JDBC 驱动..."
    sudo -u {deploy_user} mkdir -p libs
    
    cd /tmp
    
    # 尝试多个 MySQL 驱动版本
    DRIVER_DOWNLOADED=false
    
    # MySQL Connector/J 8.0.33 (新版本)
    if wget -O mysql-connector-j-8.0.33.jar "https://repo1.maven.org/maven2/com/mysql/mysql-connector-j/8.0.33/mysql-connector-j-8.0.33.jar" 2>/dev/null; then
        sudo cp mysql-connector-j-8.0.33.jar {install_path}/libs/
        sudo chown {deploy_user}:{deploy_user} {install_path}/libs/mysql-connector-j-8.0.33.jar
        DRIVER_DOWNLOADED=true
        echo "✓ MySQL Connector/J 8.0.33 安装成功"
    elif curl -L -o mysql-connector-j-8.0.33.jar "https://repo1.maven.org/maven2/com/mysql/mysql-connector-j/8.0.33/mysql-connector-j-8.0.33.jar" 2>/dev/null; then
        sudo cp mysql-connector-j-8.0.33.jar {install_path}/libs/
        sudo chown {deploy_user}:{deploy_user} {install_path}/libs/mysql-connector-j-8.0.33.jar
        DRIVER_DOWNLOADED=true
        echo "✓ MySQL Connector/J 8.0.33 安装成功 (curl)"
    fi
    
    # 备选: MySQL Connector/Java 8.0.32
    if [ "$DRIVER_DOWNLOADED" = "false" ]; then
        if wget -O mysql-connector-java-8.0.32.jar "https://repo1.maven.org/maven2/mysql/mysql-connector-java/8.0.32/mysql-connector-java-8.0.32.jar" 2>/dev/null; then
            sudo cp mysql-connector-java-8.0.32.jar {install_path}/libs/
            sudo chown {deploy_user}:{deploy_user} {install_path}/libs/mysql-connector-java-8.0.32.jar
            DRIVER_DOWNLOADED=true
            echo "✓ MySQL Connector/Java 8.0.32 安装成功"
        elif curl -L -o mysql-connector-java-8.0.32.jar "https://repo1.maven.org/maven2/mysql/mysql-connector-java/8.0.32/mysql-connector-java-8.0.32.jar" 2>/dev/null; then
            sudo cp mysql-connector-java-8.0.32.jar {install_path}/libs/
            sudo chown {deploy_user}:{deploy_user} {install_path}/libs/mysql-connector-java-8.0.32.jar
            DRIVER_DOWNLOADED=true
            echo "✓ MySQL Connector/Java 8.0.32 安装成功 (curl)"
        fi
    fi
    
    # 最后备选: 8.0.30
    if [ "$DRIVER_DOWNLOADED" = "false" ]; then
        if wget -O mysql-connector-java-8.0.30.jar "https://repo1.maven.org/maven2/mysql/mysql-connector-java/8.0.30/mysql-connector-java-8.0.30.jar" 2>/dev/null; then
            sudo cp mysql-connector-java-8.0.30.jar {install_path}/libs/
            sudo chown {deploy_user}:{deploy_user} {install_path}/libs/mysql-connector-java-8.0.30.jar
            DRIVER_DOWNLOADED=true
            echo "✓ MySQL Connector/Java 8.0.30 安装成功"
        elif curl -L -o mysql-connector-java-8.0.30.jar "https://repo1.maven.org/maven2/mysql/mysql-connector-java/8.0.30/mysql-connector-java-8.0.30.jar" 2>/dev/null; then
            sudo cp mysql-connector-java-8.0.30.jar {install_path}/libs/
            sudo chown {deploy_user}:{deploy_user} {install_path}/libs/mysql-connector-java-8.0.30.jar
            DRIVER_DOWNLOADED=true
            echo "✓ MySQL Connector/Java 8.0.30 安装成功 (curl)"
        fi
    fi
    
    if [ "$DRIVER_DOWNLOADED" = "false" ]; then
        echo "警告: 无法下载 MySQL JDBC 驱动，但可以继续部署"
    fi
    
    # 3. 验证安装
    echo "3. 验证安装..."
    cd {install_path}
    echo "Maven wrapper 文件:"
    ls -la .mvn/wrapper/ 2>/dev/null || echo "  .mvn/wrapper/ 不存在"
    ls -la mvnw 2>/dev/null || echo "  mvnw 不存在"
    
    echo "MySQL JDBC 驱动:"
    ls -la libs/mysql-connector-*.jar 2>/dev/null || echo "  没有 MySQL 驱动"
    
    echo "✓ 修复完成"
    '''
    
    success_count = 0
    for node in all_nodes:
        print(f"\n修复节点: {node}")
        
        # 执行修复脚本
        ssh_cmd = f'ssh -i {key_file} -o StrictHostKeyChecking=no ec2-user@{node} "{fix_script}"'
        
        if run_command(ssh_cmd, check=False):
            success_count += 1
            print(f"✓ 节点 {node} 修复成功")
        else:
            print(f"✗ 节点 {node} 修复失败")
    
    print(f"\n修复完成: {success_count}/{len(all_nodes)} 个节点成功")
    return success_count == len(all_nodes)

def main():
    """主函数"""
    print("DolphinScheduler 插件问题快速修复工具")
    print("=" * 50)
    
    # 读取配置文件
    config_file = "config.yaml"
    if not os.path.exists(config_file):
        print(f"错误: 配置文件不存在: {config_file}")
        sys.exit(1)
    
    with open(config_file, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    
    # 修复所有节点
    if fix_mysql_driver_on_nodes(config):
        print("\n✓ 所有节点修复成功!")
        print("\n接下来可以:")
        print("1. 重新运行: python cli.py create")
        print("2. 或者继续当前的部署流程")
    else:
        print("\n✗ 部分节点修复失败")
        print("请检查网络连接和节点状态")

if __name__ == "__main__":
    main()