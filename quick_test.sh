#!/bin/bash
#
# 快速测试脚本 - 适用于已配置好的堡垒机环境
# 使用方法: ./quick_test.sh [commit_message]
#

set -e

# 堡垒机IP地址
BASTION_HOST="43.192.117.205"
BASTION_USER="ec2-user"
REMOTE_PROJECT_DIR="/home/ec2-user/dolphinscheduler-ec2-on-aws"
CONFIG_FILE="config.yaml"

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
print_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
print_error() { echo -e "${RED}[ERROR]${NC} $1"; }
print_step() { echo -e "\n${BLUE}========================================${NC}\n${BLUE}$1${NC}\n${BLUE}========================================${NC}"; }

# 检查堡垒机IP配置
if [ -z "$BASTION_HOST" ]; then
    print_error "BASTION_HOST 变量未设置"
    print_info "请检查脚本配置"
    exit 1
fi

COMMIT_MESSAGE="${1:-快速修复测试}"

print_step "快速开发测试流程"
print_info "堡垒机: $BASTION_USER@$BASTION_HOST"
print_info "提交信息: $COMMIT_MESSAGE"

# 1. Git 提交推送
print_step "1. 提交并推送代码"
if git diff --quiet && git diff --staged --quiet; then
    print_warning "没有检测到更改"
else
    git add .
    git commit -m "$COMMIT_MESSAGE"
    git push origin main
    print_success "代码已推送"
fi

# 2. 堡垒机拉取代码
print_step "2. 堡垒机拉取最新代码"
ssh "$BASTION_USER@$BASTION_HOST" << EOF
    cd "$REMOTE_PROJECT_DIR"
    echo "当前目录: \$(pwd)"
    git pull origin main
    echo "最新提交: \$(git log -1 --oneline)"
EOF

if [ $? -eq 0 ]; then
    print_success "代码拉取成功"
else
    print_error "代码拉取失败"
    exit 1
fi

# 3. 运行验证测试
print_step "3. 运行配置验证"
ssh "$BASTION_USER@$BASTION_HOST" << EOF
    cd "$REMOTE_PROJECT_DIR"
    echo "=========================================="
    echo "开始配置验证"
    echo "时间: \$(date)"
    echo "=========================================="
    
    python3 cli.py validate --config $CONFIG_FILE
    
    echo "=========================================="
    echo "验证完成"
    echo "=========================================="
EOF

if [ $? -eq 0 ]; then
    print_success "🎉 配置验证通过！"
    
    # 询问是否进行实际部署测试
    echo
    read -p "是否要进行实际部署测试？(y/N): " -n 1 -r
    echo
    
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        print_step "4. 运行部署测试"
        ssh "$BASTION_USER@$BASTION_HOST" << EOF
            cd "$REMOTE_PROJECT_DIR"
            echo "=========================================="
            echo "开始部署测试"
            echo "时间: \$(date)"
            echo "=========================================="
            
            python3 cli.py create --config $CONFIG_FILE
            
            echo "=========================================="
            echo "部署测试完成"
            echo "=========================================="
EOF
        
        if [ $? -eq 0 ]; then
            print_success "🎉 部署测试成功！"
            
            # 询问是否清理资源
            echo
            read -p "是否要清理测试资源？(y/N): " -n 1 -r
            echo
            
            if [[ $REPLY =~ ^[Yy]$ ]]; then
                print_info "清理测试资源..."
                ssh "$BASTION_USER@$BASTION_HOST" << EOF
                    cd "$REMOTE_PROJECT_DIR"
                    python3 cli.py delete --config $CONFIG_FILE --force
EOF
                print_success "资源清理完成"
            else
                print_warning "请记得手动清理测试资源"
            fi
        else
            print_error "❌ 部署测试失败"
            exit 1
        fi
    else
        print_info "跳过部署测试"
    fi
else
    print_error "❌ 配置验证失败"
    exit 1
fi

print_success "✅ 测试流程完成！"