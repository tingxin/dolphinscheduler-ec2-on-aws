#!/bin/bash
# 清理损坏的缓存文件

echo "Cleaning corrupted cache files..."

# 删除损坏的 DolphinScheduler 包
if [ -f /tmp/ds-cache/apache-dolphinscheduler-3.2.0-bin.tar.gz ]; then
    echo "Removing corrupted package..."
    rm -f /tmp/ds-cache/apache-dolphinscheduler-3.2.0-bin.tar.gz
    echo "✓ Removed"
fi

# 重新下载（使用国内镜像）
echo ""
echo "Downloading fresh package from Tsinghua mirror..."
mkdir -p /tmp/ds-cache

wget -O /tmp/ds-cache/apache-dolphinscheduler-3.2.0-bin.tar.gz \
    https://mirrors.tuna.tsinghua.edu.cn/apache/dolphinscheduler/3.2.0/apache-dolphinscheduler-3.2.0-bin.tar.gz

# 验证下载
echo ""
echo "Verifying downloaded file..."
if gzip -t /tmp/ds-cache/apache-dolphinscheduler-3.2.0-bin.tar.gz; then
    echo "✓ File is valid"
    ls -lh /tmp/ds-cache/apache-dolphinscheduler-3.2.0-bin.tar.gz
else
    echo "✗ File is still corrupted"
    exit 1
fi

echo ""
echo "✓ Cache cleaned and fresh package downloaded"
echo "You can now run: python cli.py create --config config.yaml"
