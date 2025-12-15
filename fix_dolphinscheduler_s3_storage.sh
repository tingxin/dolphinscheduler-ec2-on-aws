#!/bin/bash

# DolphinScheduler S3存储配置修复脚本
# 用于修复缺少resource-storage配置的问题

set -e

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 日志函数
log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 配置参数（根据你的config.yaml）
S3_REGION="us-east-2"
S3_BUCKET="tx-mageline-eks"
S3_FOLDER="/dolphinscheduler"
S3_ENDPOINT="https://s3.us-east-2.amazonaws.com"
DOLPHINSCHEDULER_HOME="/opt/dolphinscheduler"

# 检查是否为root用户或有sudo权限
check_permissions() {
    if [[ $EUID -eq 0 ]]; then
        SUDO=""
    elif sudo -n true 2>/dev/null; then
        SUDO="sudo"
    else
        log_error "需要root权限或sudo权限来执行此脚本"
        exit 1
    fi
}

# 检查DolphinScheduler安装
check_dolphinscheduler() {
    log_info "检查DolphinScheduler安装..."
    
    if [ ! -d "$DOLPHINSCHEDULER_HOME" ]; then
        log_error "DolphinScheduler未安装在 $DOLPHINSCHEDULER_HOME"
        exit 1
    fi
    
    log_info "✓ DolphinScheduler安装目录存在"
}

# 检查AWS CLI和权限
check_aws_access() {
    log_info "检查AWS访问权限..."
    
    if ! command -v aws &> /dev/null; then
        log_error "AWS CLI未安装"
        exit 1
    fi
    
    # 测试AWS权限
    if ! aws sts get-caller-identity &> /dev/null; then
        log_error "AWS权限配置有问题"
        exit 1
    fi
    
    # 测试S3访问
    if ! aws s3 ls s3://$S3_BUCKET --region $S3_REGION &> /dev/null; then
        log_error "无法访问S3 bucket: $S3_BUCKET"
        exit 1
    fi
    
    log_info "✓ AWS访问权限正常"
}

# 备份配置文件
backup_configs() {
    log_info "备份现有配置文件..."
    
    local backup_dir="$DOLPHINSCHEDULER_HOME/config_backup_$(date +%Y%m%d_%H%M%S)"
    $SUDO mkdir -p "$backup_dir"
    
    for component in api-server master-server worker-server alert-server; do
        if [ -f "$DOLPHINSCHEDULER_HOME/$component/conf/application.yaml" ]; then
            $SUDO cp "$DOLPHINSCHEDULER_HOME/$component/conf/application.yaml" "$backup_dir/$component-application.yaml"
            log_info "✓ 备份 $component 配置"
        fi
    done
    
    log_info "✓ 配置文件已备份到: $backup_dir"
}

# 检查配置文件是否已有resource-storage配置
check_existing_config() {
    local config_file="$1"
    
    if grep -q "resource-storage:" "$config_file" 2>/dev/null; then
        return 0  # 已存在
    else
        return 1  # 不存在
    fi
}

# 添加S3存储配置
add_s3_config() {
    local config_file="$1"
    local component="$2"
    
    log_info "为 $component 添加S3存储配置..."
    
    if check_existing_config "$config_file"; then
        log_warn "$component 配置文件已包含resource-storage配置，跳过"
        return 0
    fi
    
    # 添加S3配置到文件末尾
    $SUDO tee -a "$config_file" << EOF

# Resource Storage Configuration (Added by fix script)
resource-storage:
  type: S3
  s3:
    region: $S3_REGION
    bucket-name: $S3_BUCKET
    folder: $S3_FOLDER
    access-key-id: 
    secret-access-key: 
    endpoint: $S3_ENDPOINT
EOF
    
    log_info "✓ $component S3配置添加完成"
}

# 验证YAML格式
validate_yaml() {
    local config_file="$1"
    local component="$2"
    
    log_info "验证 $component 配置文件格式..."
    
    if command -v python3 &> /dev/null; then
        if python3 -c "import yaml; yaml.safe_load(open('$config_file'))" 2>/dev/null; then
            log_info "✓ $component YAML格式正确"
        else
            log_error "$component YAML格式错误"
            return 1
        fi
    else
        log_warn "Python3未安装，跳过YAML格式验证"
    fi
}

# 创建S3目录结构
create_s3_structure() {
    log_info "创建S3目录结构..."
    
    # 创建主目录
    aws s3api put-object --bucket "$S3_BUCKET" --key "dolphinscheduler/" --region "$S3_REGION" || true
    
    # 创建子目录
    for subdir in resources udfs; do
        aws s3api put-object --bucket "$S3_BUCKET" --key "dolphinscheduler/$subdir/" --region "$S3_REGION" || true
    done
    
    # 验证目录创建
    if aws s3 ls "s3://$S3_BUCKET/dolphinscheduler/" --region "$S3_REGION" &> /dev/null; then
        log_info "✓ S3目录结构创建成功"
        aws s3 ls "s3://$S3_BUCKET/dolphinscheduler/" --region "$S3_REGION"
    else
        log_error "S3目录创建失败"
        return 1
    fi
}

# 重启DolphinScheduler服务
restart_services() {
    log_info "重启DolphinScheduler服务..."
    
    local services=("dolphinscheduler-api" "dolphinscheduler-master" "dolphinscheduler-worker" "dolphinscheduler-alert")
    
    for service in "${services[@]}"; do
        if systemctl is-active --quiet "$service" 2>/dev/null; then
            log_info "重启 $service..."
            $SUDO systemctl restart "$service"
            
            # 等待服务启动
            sleep 5
            
            if systemctl is-active --quiet "$service"; then
                log_info "✓ $service 重启成功"
            else
                log_warn "$service 重启后状态异常"
            fi
        else
            log_info "$service 未在此节点运行，跳过"
        fi
    done
}

# 测试S3存储功能
test_s3_storage() {
    log_info "测试S3存储功能..."
    
    local test_file="/tmp/ds_s3_test_$(date +%s).txt"
    local s3_test_path="s3://$S3_BUCKET/dolphinscheduler/resources/test_$(date +%s).txt"
    
    # 创建测试文件
    echo "DolphinScheduler S3 storage test - $(date)" > "$test_file"
    
    # 上传测试
    if aws s3 cp "$test_file" "$s3_test_path" --region "$S3_REGION"; then
        log_info "✓ S3上传测试成功"
        
        # 清理测试文件
        aws s3 rm "$s3_test_path" --region "$S3_REGION" &> /dev/null || true
        rm -f "$test_file"
    else
        log_error "S3上传测试失败"
        rm -f "$test_file"
        return 1
    fi
}

# 显示修复结果
show_results() {
    log_info "修复完成！现在可以："
    echo "1. 登录DolphinScheduler Web UI"
    echo "2. 进入 资源中心 -> 文件管理"
    echo "3. 上传SSH密钥文件 (如 ec2-ohio.pem)"
    echo "4. 在Shell任务中使用上传的密钥文件"
    echo ""
    echo "S3存储配置:"
    echo "  Bucket: $S3_BUCKET"
    echo "  Region: $S3_REGION"
    echo "  Folder: $S3_FOLDER"
}

# 主函数
main() {
    log_info "开始修复DolphinScheduler S3存储配置..."
    
    # 检查权限
    check_permissions
    
    # 检查环境
    check_dolphinscheduler
    check_aws_access
    
    # 备份配置
    backup_configs
    
    # 为每个组件添加S3配置
    for component in api-server master-server worker-server alert-server; do
        config_file="$DOLPHINSCHEDULER_HOME/$component/conf/application.yaml"
        
        if [ -f "$config_file" ]; then
            add_s3_config "$config_file" "$component"
            validate_yaml "$config_file" "$component"
        else
            log_warn "$component 配置文件不存在，跳过"
        fi
    done
    
    # 创建S3目录结构
    create_s3_structure
    
    # 重启服务
    restart_services
    
    # 测试S3存储
    test_s3_storage
    
    # 显示结果
    show_results
    
    log_info "修复脚本执行完成！"
}

# 脚本入口
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi