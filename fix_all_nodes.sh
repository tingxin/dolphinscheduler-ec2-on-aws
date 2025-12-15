#!/bin/bash

# 在所有DolphinScheduler节点上执行S3存储配置修复
# 此脚本在跳板机上运行

set -e

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 检查必要文件
check_files() {
    if [ ! -f "config.yaml" ]; then
        log_error "config.yaml 文件不存在"
        exit 1
    fi
    
    if [ ! -f "fix_dolphinscheduler_s3_storage.sh" ]; then
        log_error "fix_dolphinscheduler_s3_storage.sh 文件不存在"
        exit 1
    fi
    
    if [ ! -f "ec2-ohio.pem" ]; then
        log_error "SSH密钥文件 ec2-ohio.pem 不存在"
        exit 1
    fi
    
    chmod 600 ec2-ohio.pem
}

# 从config.yaml获取节点IP
get_node_ips() {
    log_info "从config.yaml获取节点IP..."
    
    python3 -c "
import yaml
with open('config.yaml', 'r') as f:
    config = yaml.safe_load(f)

all_nodes = set()
for component in ['master', 'worker', 'api', 'alert']:
    nodes = config.get('cluster', {}).get(component, {}).get('nodes', [])
    for node in nodes:
        if 'host' in node:
            all_nodes.add(node['host'])

for ip in sorted(all_nodes):
    print(ip)
" > /tmp/node_ips.txt
    
    if [ ! -s /tmp/node_ips.txt ]; then
        log_error "无法从config.yaml获取节点IP"
        exit 1
    fi
    
    log_info "找到以下DolphinScheduler节点:"
    cat /tmp/node_ips.txt
}

# 在单个节点上执行修复
fix_node() {
    local node_ip="$1"
    log_info "正在修复节点: $node_ip"
    
    # 复制修复脚本到节点
    if scp -i ec2-ohio.pem -o StrictHostKeyChecking=no fix_dolphinscheduler_s3_storage.sh ec2-user@$node_ip:~/; then
        log_info "✓ 脚本已复制到 $node_ip"
    else
        log_error "✗ 无法复制脚本到 $node_ip"
        return 1
    fi
    
    # 在节点上执行修复脚本
    if ssh -i ec2-ohio.pem -o StrictHostKeyChecking=no ec2-user@$node_ip "chmod +x fix_dolphinscheduler_s3_storage.sh && ./fix_dolphinscheduler_s3_storage.sh"; then
        log_info "✓ 节点 $node_ip 修复成功"
    else
        log_error "✗ 节点 $node_ip 修复失败"
        return 1
    fi
}

# 主函数
main() {
    log_info "开始修复所有DolphinScheduler节点的S3存储配置..."
    
    # 检查文件
    check_files
    
    # 获取节点IP
    get_node_ips
    
    # 修复每个节点
    local success_count=0
    local total_count=0
    
    while IFS= read -r node_ip; do
        if [ -n "$node_ip" ]; then
            total_count=$((total_count + 1))
            echo ""
            echo "=========================================="
            if fix_node "$node_ip"; then
                success_count=$((success_count + 1))
            fi
            echo "=========================================="
        fi
    done < /tmp/node_ips.txt
    
    # 清理临时文件
    rm -f /tmp/node_ips.txt
    
    # 显示结果
    echo ""
    log_info "修复完成统计:"
    log_info "  总节点数: $total_count"
    log_info "  成功节点数: $success_count"
    log_info "  失败节点数: $((total_count - success_count))"
    
    if [ $success_count -eq $total_count ]; then
        log_info "🎉 所有节点修复成功！"
        echo ""
        echo "现在可以:"
        echo "1. 登录DolphinScheduler Web UI"
        echo "2. 进入 资源中心 -> 文件管理"
        echo "3. 上传SSH密钥文件 (如 ec2-ohio.pem)"
    else
        log_warn "⚠️  部分节点修复失败，请检查日志"
    fi
}

# 执行主函数
main "$@"