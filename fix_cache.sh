#!/bin/bash
# 清理损坏的缓存文件

echo "Cleaning corrupted cache files..."

# 删除损坏的 DolphinScheduler 包
if [ -f /tmp/ds-cache/apache-dolphinscheduler-3.2.0-bin.tar.gz ]; then
    echo "Removing corrupted package..."
    rm -f /tmp/ds-cache/apache-dolphinscheduler-3.2.0-bin.tar.gz
    echo "✓ Removed"
fi

# 重新下载（使用 Apache 官方源）
echo ""
echo "Downloading fresh package from Apache archive..."
mkdir -p /tmp/ds-cache

# 尝试多个镜像源
URLS=(
    "https://archive.apache.org/dist/dolphinscheduler/3.2.0/apache-dolphinscheduler-3.2.0-bin.tar.gz"
    "https://dlcdn.apache.org/dolphinscheduler/3.2.0/apache-dolphinscheduler-3.2.0-bin.tar.gz"
    "https://repo.huaweicloud.com/apache/dolphinscheduler/3.2.0/apache-dolphinscheduler-3.2.0-bin.tar.gz"
)

for url in "${URLS[@]}"; do
    echo "Trying: $url"
    if wget -O /tmp/ds-cache/apache-dolphinscheduler-3.2.0-bin.tar.gz "$url"; then
        echo "✓ Download successful"
        break
    else
        echo "✗ Failed, trying next mirror..."
        rm -f /tmp/ds-cache/apache-dolphinscheduler-3.2.0-bin.tar.gz
    fi
done

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
