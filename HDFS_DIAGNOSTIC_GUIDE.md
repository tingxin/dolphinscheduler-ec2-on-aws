# HDFS Diagnostic Guide for DolphinScheduler

## Issue Summary
File upload to DolphinScheduler resource center is failing with error:
```
Mkdirs failed to create file:/dolphinscheduler/default/resources
```

This indicates that DolphinScheduler cannot create the required HDFS directory structure.

## Root Causes to Check

1. **HDFS NameNode not accessible** - DolphinScheduler nodes cannot reach EMR NameNode
2. **HDFS path not created** - `/dolphinscheduler` directory doesn't exist in HDFS
3. **Permission issues** - HDFS user doesn't have write permissions
4. **Configuration mismatch** - HDFS connection details not correctly configured in DolphinScheduler

## Diagnostic Commands

### Step 1: SSH to API Server and Check HDFS Connectivity

```bash
# SSH to API server
ssh -i <your-key.pem> ec2-user@<api-server-ip>

# Test HDFS connectivity
hdfs dfs -ls /

# If this fails, HDFS NameNode is not reachable
```

### Step 2: Check if /dolphinscheduler Directory Exists

```bash
# List HDFS root
hdfs dfs -ls /

# Check if /dolphinscheduler exists
hdfs dfs -ls /dolphinscheduler

# If not found, create it
hdfs dfs -mkdir -p /dolphinscheduler
hdfs dfs -chmod 755 /dolphinscheduler
```

### Step 3: View DolphinScheduler API Server Logs

```bash
# SSH to API server
ssh -i <your-key.pem> ec2-user@<api-server-ip>

# View API server logs (real-time)
tail -f /opt/dolphinscheduler/logs/dolphinscheduler-api-server.log

# Or view recent logs
tail -100 /opt/dolphinscheduler/logs/dolphinscheduler-api-server.log

# Search for HDFS-related errors
grep -i "hdfs\|resource\|storage" /opt/dolphinscheduler/logs/dolphinscheduler-api-server.log
```

### Step 4: Check HDFS Configuration in DolphinScheduler

```bash
# SSH to API server
ssh -i <your-key.pem> ec2-user@<api-server-ip>

# Check common.properties
cat /opt/dolphinscheduler/conf/common.properties | grep -i hdfs

# Check application.yaml
cat /opt/dolphinscheduler/api-server/conf/application.yaml | grep -A 10 'resource-storage'
```

### Step 5: Test HDFS Write Permission

```bash
# SSH to API server
ssh -i <your-key.pem> ec2-user@<api-server-ip>

# Test write permission
hdfs dfs -touchz /dolphinscheduler/test.txt

# If successful, delete test file
hdfs dfs -rm /dolphinscheduler/test.txt

# If fails, check permissions
hdfs dfs -ls -la /dolphinscheduler
```

### Step 6: Check EMR NameNode Status

```bash
# SSH to EMR master node
ssh -i <your-key.pem> hadoop@<emr-master-ip>

# Check HDFS status
hdfs dfsadmin -report

# Check NameNode logs
tail -100 /var/log/hadoop-hdfs/hadoop-hdfs-namenode-*.log
```

### Step 7: Verify Network Connectivity

```bash
# From DolphinScheduler API server, test connection to EMR NameNode
ssh -i <your-key.pem> ec2-user@<api-server-ip>

# Test port 8020 (HDFS NameNode)
nc -zv <emr-namenode-ip> 8020

# Test port 50070 (HDFS Web UI)
curl -I http://<emr-namenode-ip>:50070
```

## Common Issues and Solutions

### Issue 1: HDFS NameNode Not Reachable

**Error**: `java.net.ConnectException: Connection refused`

**Solution**:
1. Verify EMR cluster is running
2. Check security group rules allow port 8020 from DolphinScheduler nodes
3. Verify HDFS NameNode IP/hostname in config.yaml is correct

### Issue 2: /dolphinscheduler Directory Doesn't Exist

**Error**: `Mkdirs failed to create file:/dolphinscheduler/default/resources`

**Solution**:
```bash
# Create directory manually
hdfs dfs -mkdir -p /dolphinscheduler
hdfs dfs -chmod 755 /dolphinscheduler

# Verify
hdfs dfs -ls /dolphinscheduler
```

### Issue 3: Permission Denied

**Error**: `Permission denied: user=ec2-user, access=WRITE`

**Solution**:
```bash
# Check current permissions
hdfs dfs -ls -la /dolphinscheduler

# Fix permissions (if needed)
hdfs dfs -chmod 777 /dolphinscheduler

# Or create with correct user
hdfs dfs -mkdir -p /dolphinscheduler
hdfs dfs -chown hadoop:hadoop /dolphinscheduler
hdfs dfs -chmod 755 /dolphinscheduler
```

### Issue 4: HDFS Configuration Not Correct

**Check configuration**:
```bash
# View current HDFS config
cat /opt/dolphinscheduler/conf/common.properties | grep -i hdfs

# Expected output should show:
# resource.storage.type=HDFS
# resource.hdfs.fs.defaultFS=hdfs://<namenode-ip>:8020
# resource.hdfs.path.prefix=/dolphinscheduler
# resource.hdfs.username=hadoop
```

## Configuration Verification Checklist

- [ ] EMR cluster is running and accessible
- [ ] HDFS NameNode IP/port is correct in config.yaml
- [ ] Security groups allow port 8020 from DolphinScheduler nodes
- [ ] `/dolphinscheduler` directory exists in HDFS
- [ ] Directory has correct permissions (755 or 777)
- [ ] DolphinScheduler configuration has correct HDFS settings
- [ ] API server logs show successful HDFS connection
- [ ] Test file can be created/deleted in HDFS

## Next Steps

1. Run diagnostic commands above to identify the specific issue
2. Check logs for detailed error messages
3. Fix the identified issue
4. Retry file upload to resource center
5. If still failing, check DolphinScheduler API server logs for more details

## Useful Log Locations

- **API Server**: `/opt/dolphinscheduler/logs/dolphinscheduler-api-server.log`
- **Master Server**: `/opt/dolphinscheduler/logs/dolphinscheduler-master-server.log`
- **Worker Server**: `/opt/dolphinscheduler/logs/dolphinscheduler-worker-server.log`
- **EMR NameNode**: `/var/log/hadoop-hdfs/hadoop-hdfs-namenode-*.log`
