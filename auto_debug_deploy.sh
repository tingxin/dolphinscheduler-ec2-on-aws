#!/bin/bash
#
# 自动调试部署脚本 - 监控堡垒机日志并自动修复问题
# 使用方法: ./auto_debug_deploy.sh "初始测试"
#

set -e

# 配置
BASTION_HOST="43.192.117.205"
BASTION_USER="ec2-user"
REMOTE_PROJECT_DIR="/home/ec2-user/dolphinscheduler-ec2-on-aws"
CONFIG_FILE="config.yaml"
GITHUB_BRANCH="main"
MAX_RETRY_ATTEMPTS=5

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

print_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
print_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
print_error() { echo -e "${RED}[ERROR]${NC} $1"; }
print_debug() { echo -e "${CYAN}[DEBUG]${NC} $1"; }
print_step() { echo -e "\n${BLUE}========================================${NC}\n${BLUE}$1${NC}\n${BLUE}========================================${NC}"; }

# 日志文件
LOG_FILE="auto_debug_$(date +%Y%m%d_%H%M%S).log"

# 记录日志函数
log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "$LOG_FILE"
}

# 函数：提交并推送代码
commit_and_push() {
    local commit_message="$1"
    
    print_step "提交代码到 GitHub"
    log "开始提交代码: $commit_message"
    
    if git diff --quiet && git diff --staged --quiet; then
        print_warning "没有检测到更改，跳过提交"
        log "没有更改需要提交"
        return 0
    fi
    
    git add .
    git commit -m "$commit_message"
    git push origin "$GITHUB_BRANCH"
    
    print_success "代码已推送到 GitHub"
    log "代码推送成功: $commit_message"
}

# 函数：在堡垒机上拉取代码
pull_code() {
    print_step "堡垒机拉取最新代码"
    log "开始在堡垒机拉取代码"
    
    ssh "$BASTION_USER@$BASTION_HOST" << 'EOF'
        set -e
        cd /home/ec2-user/dolphinscheduler-ec2-on-aws
        echo "=== 拉取代码开始 ==="
        echo "当前目录: $(pwd)"
        echo "拉取前提交: $(git rev-parse --short HEAD)"
        
        git fetch origin
        git reset --hard origin/main
        
        echo "拉取后提交: $(git rev-parse --short HEAD)"
        echo "最新提交信息: $(git log -1 --oneline)"
        
        # 检查 Python 依赖
        if [ -f requirements.txt ]; then
            echo "安装 Python 依赖..."
            pip3 install -r requirements.txt --user --quiet
        fi
        
        echo "=== 拉取代码完成 ==="
EOF
    
    if [ $? -eq 0 ]; then
        print_success "代码拉取成功"
        log "堡垒机代码拉取成功"
    else
        print_error "代码拉取失败"
        log "ERROR: 堡垒机代码拉取失败"
        exit 1
    fi
}

# 函数：运行部署并捕获详细日志
run_deployment_with_logs() {
    print_step "运行部署测试并捕获详细日志"
    log "开始运行部署测试"
    
    # 创建远程日志文件名
    local remote_log="deployment_$(date +%Y%m%d_%H%M%S).log"
    
    print_info "执行命令: python3 cli.py create --config $CONFIG_FILE"
    print_info "日志将保存到堡垒机: $remote_log"
    
    # 运行部署并捕获所有输出
    ssh "$BASTION_USER@$BASTION_HOST" << EOF
        set +e  # 不要在错误时立即退出，我们需要捕获错误信息
        cd "$REMOTE_PROJECT_DIR"
        
        echo "=========================================="
        echo "开始 DolphinScheduler 部署测试"
        echo "时间: \$(date)"
        echo "配置文件: $CONFIG_FILE"
        echo "日志文件: $remote_log"
        echo "=========================================="
        
        # 检查配置文件
        if [ ! -f "$CONFIG_FILE" ]; then
            echo "ERROR: 配置文件不存在: $CONFIG_FILE"
            echo "可用的配置文件:"
            ls -la *.yaml *.yml 2>/dev/null || echo "  无 YAML 配置文件"
            exit 1
        fi
        
        # 显示配置文件内容（脱敏）
        echo "=========================================="
        echo "配置文件内容预览:"
        echo "=========================================="
        grep -v -E "(password|secret|key)" "$CONFIG_FILE" | head -20
        echo "=========================================="
        
        # 运行部署命令并捕获所有输出
        echo "开始执行部署命令..."
        python3 cli.py create --config "$CONFIG_FILE" 2>&1 | tee "$remote_log"
        
        # 保存退出代码
        exit_code=\${PIPESTATUS[0]}
        
        echo "=========================================="
        echo "部署命令执行完成"
        echo "退出代码: \$exit_code"
        echo "时间: \$(date)"
        echo "=========================================="
        
        # 显示日志文件大小
        if [ -f "$remote_log" ]; then
            echo "日志文件大小: \$(wc -l < $remote_log) 行"
            echo "日志文件路径: \$(pwd)/$remote_log"
        fi
        
        # 返回原始退出代码
        exit \$exit_code
EOF
    
    local deployment_exit_code=$?
    
    # 下载日志文件到本地进行分析
    print_info "下载堡垒机日志进行分析..."
    scp "$BASTION_USER@$BASTION_HOST:$REMOTE_PROJECT_DIR/$remote_log" "./bastion_$remote_log" 2>/dev/null || {
        print_warning "无法下载日志文件，直接从堡垒机获取最后50行"
        ssh "$BASTION_USER@$BASTION_HOST" "cd $REMOTE_PROJECT_DIR && tail -50 $remote_log" > "./bastion_$remote_log"
    }
    
    # 显示日志内容
    print_step "堡垒机部署日志内容"
    if [ -f "./bastion_$remote_log" ]; then
        echo "=== 完整日志内容 ==="
        cat "./bastion_$remote_log"
        echo "=== 日志内容结束 ==="
        
        # 保存到主日志
        log "堡垒机部署日志:"
        cat "./bastion_$remote_log" >> "$LOG_FILE"
    fi
    
    return $deployment_exit_code
}

# 函数：分析错误并自动修复
analyze_and_fix_errors() {
    local log_file="$1"
    
    print_step "分析错误并尝试自动修复"
    log "开始分析错误: $log_file"
    
    if [ ! -f "$log_file" ]; then
        print_error "日志文件不存在: $log_file"
        return 1
    fi
    
    local fixed_something=false
    
    # 检查常见错误模式并修复
    
    # 1. 检查模块导入错误
    if grep -q "ModuleNotFoundError\|ImportError" "$log_file"; then
        print_warning "发现模块导入错误"
        log "发现模块导入错误，检查 requirements.txt"
        
        # 检查是否缺少依赖
        if grep -q "pymysql" "$log_file" && ! grep -q "pymysql" requirements.txt; then
            print_info "添加缺失的 pymysql 依赖"
            echo "pymysql>=1.0.2" >> requirements.txt
            fixed_something=true
            log "修复: 添加 pymysql 依赖"
        fi
        
        if grep -q "boto3" "$log_file" && ! grep -q "boto3" requirements.txt; then
            print_info "添加缺失的 boto3 依赖"
            echo "boto3>=1.26.0" >> requirements.txt
            fixed_something=true
            log "修复: 添加 boto3 依赖"
        fi
    fi
    
    # 2. 检查配置文件错误
    if grep -q "KeyError\|配置.*不存在\|config.*not found" "$log_file"; then
        print_warning "发现配置文件错误"
        log "发现配置文件错误"
        
        # 检查配置文件是否存在
        if [ ! -f "config.yaml" ]; then
            print_info "创建默认配置文件"
            cp "config.example.yaml" "config.yaml" 2>/dev/null || {
                print_error "无法创建配置文件，请检查 config.example.yaml"
                return 1
            }
            fixed_something=true
            log "修复: 创建默认配置文件"
        fi
    fi
    
    # 3. 检查权限错误
    if grep -q "Permission denied\|权限.*拒绝" "$log_file"; then
        print_warning "发现权限错误"
        log "发现权限错误"
        
        # 这类错误通常需要在部署脚本中修复
        print_info "权限错误可能需要修改部署脚本中的文件操作方式"
    fi
    
    # 4. 检查网络连接错误
    if grep -q "Connection.*refused\|timeout\|网络.*错误" "$log_file"; then
        print_warning "发现网络连接错误"
        log "发现网络连接错误"
        
        print_info "网络错误可能是临时的，建议重试"
    fi
    
    # 5. 检查 AWS 认证错误
    if grep -q "AWS.*credentials\|boto.*auth\|Access.*denied" "$log_file"; then
        print_warning "发现 AWS 认证错误"
        log "发现 AWS 认证错误"
        
        print_info "请检查 AWS 凭证配置"
    fi
    
    # 6. 检查语法错误
    if grep -q "SyntaxError\|IndentationError" "$log_file"; then
        print_warning "发现 Python 语法错误"
        log "发现 Python 语法错误"
        
        # 运行语法检查
        print_info "运行 Python 语法检查..."
        python3 -m py_compile cli.py src/**/*.py 2>/dev/null || {
            print_error "发现语法错误，需要手动修复"
        }
    fi
    
    if [ "$fixed_something" = true ]; then
        print_success "已自动修复一些问题"
        log "自动修复完成"
        return 0
    else
        print_info "未发现可自动修复的问题"
        log "未发现可自动修复的问题"
        return 1
    fi
}

# 主循环函数
main_loop() {
    local commit_message="$1"
    local attempt=1
    
    print_step "开始自动调试部署流程"
    print_info "最大重试次数: $MAX_RETRY_ATTEMPTS"
    print_info "日志文件: $LOG_FILE"
    
    log "开始自动调试部署流程: $commit_message"
    
    while [ $attempt -le $MAX_RETRY_ATTEMPTS ]; do
        print_step "第 $attempt 次尝试 (共 $MAX_RETRY_ATTEMPTS 次)"
        log "开始第 $attempt 次尝试"
        
        # 1. 提交代码
        commit_and_push "$commit_message (尝试 $attempt)"
        
        # 2. 堡垒机拉取代码
        pull_code
        
        # 3. 运行部署测试
        if run_deployment_with_logs; then
            print_success "🎉 部署测试成功！"
            log "部署测试成功完成"
            
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
                log "测试资源清理完成"
            else
                print_warning "请记得手动清理测试资源"
                log "用户选择不清理测试资源"
            fi
            
            return 0
        else
            print_error "第 $attempt 次部署测试失败"
            log "第 $attempt 次部署测试失败"
            
            # 分析错误并尝试修复
            local latest_log=$(ls -t bastion_deployment_*.log 2>/dev/null | head -1)
            if [ -n "$latest_log" ]; then
                if analyze_and_fix_errors "$latest_log"; then
                    print_info "已修复一些问题，准备重试..."
                    log "已修复问题，准备重试"
                    commit_message="自动修复问题"
                else
                    print_warning "无法自动修复问题"
                    log "无法自动修复问题"
                    
                    if [ $attempt -eq $MAX_RETRY_ATTEMPTS ]; then
                        print_error "已达到最大重试次数，停止尝试"
                        log "已达到最大重试次数，停止尝试"
                        
                        print_info "请查看日志文件进行手动调试: $LOG_FILE"
                        print_info "最新堡垒机日志: $latest_log"
                        return 1
                    fi
                fi
            fi
        fi
        
        attempt=$((attempt + 1))
        
        if [ $attempt -le $MAX_RETRY_ATTEMPTS ]; then
            print_info "等待 10 秒后重试..."
            sleep 10
        fi
    done
    
    print_error "所有尝试都失败了"
    log "所有尝试都失败了"
    return 1
}

# 主函数
main() {
    local commit_message="${1:-自动调试部署测试}"
    
    print_info "DolphinScheduler 自动调试部署脚本"
    print_info "开始时间: $(date)"
    print_info "堡垒机: $BASTION_USER@$BASTION_HOST"
    print_info "提交信息: $commit_message"
    
    # 检查必需工具
    for tool in git ssh python3 scp; do
        if ! command -v $tool &> /dev/null; then
            print_error "缺少必需工具: $tool"
            exit 1
        fi
    done
    
    # 测试 SSH 连接
    print_info "测试 SSH 连接..."
    if ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=no "$BASTION_USER@$BASTION_HOST" "echo 'SSH 连接成功'" &>/dev/null; then
        print_success "SSH 连接测试成功"
    else
        print_error "SSH 连接失败，请检查网络和认证配置"
        exit 1
    fi
    
    # 运行主循环
    if main_loop "$commit_message"; then
        print_success "🎉 自动调试部署完成！"
        log "自动调试部署成功完成"
    else
        print_error "❌ 自动调试部署失败"
        log "自动调试部署失败"
        exit 1
    fi
    
    print_info "结束时间: $(date)"
    print_info "完整日志: $LOG_FILE"
}

# 运行主函数
main "$@"