Dolphinscheduler-3.2.0分布式集群详细部署


DolPhinScheduler
专栏收录该内容
1 篇文章
订阅专栏
文章目录
一、集群规划
二、集群安装与测试步骤
一、集群规划
主机名	ip	服务	系统	cpu	内存
node01	192.168.10.101	zookeeper-3.8.4、master、worker、apiserver、alertserver和MySQL8.0.26	Centos 7.9	4	9.6
node02	192.168.10.102	zookeeper-3.8.4、master和worker	Centos 7.9	2	4
node03	192.168.10.103	zookeeper-3.8.4和worker	Centos 7.9	2	4
二、集群安装与测试步骤
1、在node01创建免登录用户用于ds集群的安装

# 创建用户需使用root登录
useradd dolphinscheduler
# 添加密码
echo "dolphinscheduler" | passwd --stdin dolphinscheduler
# 配置sudo（系统管理命令）免密
sed -i '$adolphinscheduler  ALL=(ALL)  NOPASSWD: NOPASSWD: ALL' /etc/sudoers
sed -i 's/Defaults   requirett/#Defaults   requirett/g' /etc/sudoers
AI写代码
sh
1
2
3
4
5
6
7
2、做dolphinscheduler用户的免登录

[root@node01 ~]# su dolphinscheduler
[dolphinscheduler@node01 root]$ ssh-keygen -t rsa  #一路回车即可
[dolphinscheduler@node01 root]$ ssh-copy-id master  #密码为dolphinscheduler
#测试是否可以免登录自己
[dolphinscheduler@node01 root]$ ssh node01   #如果不需要输入密码则OK
#切回root用户进行后续操作
[dolphinscheduler@node01 ~]$ su
密码：        #输入root的用户密码
AI写代码
sh
1
2
3
4
5
6
7
8
3、创建源数据库

[root@node01 dolphinscheduler]# mysql -uroot -p123456
-- 创建dolphinscheduler的元数据库，并指定编码
mysql> CREATE DATABASE dolphinscheduler2204 DEFAULT CHARACTER SET utf8 DEFAULT COLLATE utf8_general_ci;
AI写代码
sql
1
2
3
4、解压安装包

[root@node01 ~]# tar -zxvf /home/apache-dolphinscheduler-3.2.0-bin.tar.gz -C /home/
[root@node01 ~]# chown -R dolphinscheduler:dolphinscheduler /home/apache-dolphinscheduler-3.2.0-bin
AI写代码
sh
1
2
5、配置install_env.sh

[root@node01 ~]# vi /home/apache-dolphinscheduler-3.2.0-bin/bin/env/install_env.sh

#修改如下内容
ips="node01,node02,node03"
sshPort="22"
masters="node01,node02"
workers="node01:default,node02:default,node03:default"
alertServer="node01"
apiServers="node01"
installPath=/usr/local/dolphinscheduler-3.2.0
deployUser="dolphinscheduler"
zkRoot="/dolphinscheduler2204"
AI写代码
properties
1
2
3
4
5
6
7
8
9
10
6、配置dolphinscheduler_env.sh

[root@node01 ~]# vi /home/apache-dolphinscheduler-3.2.0-bin/bin/env/dolphinscheduler_env.sh

#文件末尾追加
export JAVA_HOME=${JAVA_HOME:-/usr/local/jdk-1.8.0}

export DATABASE=${DATABASE:-mysql}
export SPRING_PROFILES_ACTIVE=${DATABASE}
export SPRING_DATASOURCE_URL="jdbc:mysql://node01:3306/dolphinscheduler2204?useUnicode=true&characterEncoding=UTF-8&useSSL=false&allowPublicKeyRetrieval=true"
export SPRING_DATASOURCE_USERNAME=root
export SPRING_DATASOURCE_PASSWORD=123456

export SPRING_CACHE_TYPE=${SPRING_CACHE_TYPE:-none}
export SPRING_JACKSON_TIME_ZONE=${SPRING_JACKSON_TIME_ZONE:-UTC}
export MASTER_FETCH_COMMAND_NUM=${MASTER_FETCH_COMMAND_NUM:-10}
export REGISTRY_TYPE=${REGISTRY_TYPE:-zookeeper}
export REGISTRY_ZOOKEEPER_CONNECT_STRING=${REGISTRY_ZOOKEEPER_CONNECT_STRING:-node01:2181,node02:2181,node03:2181}

export HADOOP_HOME=${HADOOP_HOME:-/usr/local/hadoop-3.3.1}
export HADOOP_CONF_DIR=${HADOOP_CONF_DIR:-/usr/local/hadoop-3.3.1/etc/hadoop}
export SPARK_HOME=${SPARK_HOME:-/usr/local/spark-3.5.3}
export PYTHON_LAUNCHER=${PYTHON_LAUNCHER:-/usr/bin/python}
export HIVE_HOME=${HIVE_HOME:-/usr/local/hive-3.1.2}
export FLINK_HOME=${FLINK_HOME:-/opt/soft/flink}
export DATAX_LAUNCHER=${DATAX_LAUNCHER:-/usr/local/datax/bin/datax.py}

export PATH=$HADOOP_HOME/bin:$SPARK_HOME/bin:$PYTHON_LAUNCHER:$JAVA_HOME/bin:$HIVE_HOME/bin:$FLINK_HOME/bin:$DATAX_LAUNCHER:$PATH
AI写代码
properties
1
2
3
4
5
6
7
8
9
10
11
12
13
14
15
16
17
18
19
20
21
22
23
24
7、添加mysql的依赖jar包拷贝到master-server、worker-server、api-server、alert-server和tools模块的libs目录中。
#添加mysql-8.0.26驱动依赖到/home


#复制到不同模块目录

[root@node01 ~]# cp /home/mysql-connector-java-8.0.26.jar /home/apache-dolphinscheduler-3.2.0-bin/master-server/libs/
[root@node01 ~]# cp /home/mysql-connector-java-8.0.26.jar /home/apache-dolphinscheduler-3.2.0-bin/worker-server/libs/
[root@node01 ~]# cp /home/mysql-connector-java-8.0.26.jar /home/apache-dolphinscheduler-3.2.0-bin/api-server/libs/
[root@node01 ~]# cp /home/mysql-connector-java-8.0.26.jar /home/apache-dolphinscheduler-3.2.0-bin/alert-server/libs/
[root@node01 ~]# cp /home/mysql-connector-java-8.0.26.jar /home/apache-dolphinscheduler-3.2.0-bin/tools/libs/
AI写代码
sh
1
2
3
4
5
#初始化ds的元数据

[root@node01 ~]# sh /home/apache-dolphinscheduler-3.2.0-bin/tools/bin/upgrade-schema.sh


#查看元数据


8、配置master-server、worker-server、api-server、alert-server和tools模块下的conf目录下的application.yaml文件

[root@node01 ~]# vi /home/apache-dolphinscheduler-3.2.0-bin/master-server/conf/application.yaml

 datasource:
  driver-class-name: com.mysql.cj.jdbc.Driver
  url: jdbc:mysql://node01:3306/dolphinscheduler2204?useUnicode=true&characterEncoding=UTF-8&useSSL=false&allowPublicKeyRetrieval=true
  username: root
  password: 123456

registry:
 type: zookeeper
 zookeeper:
  namespace: dolphinscheduler2204
  connect-string: node01:2181,node02:2181,node03:2181

max-cpu-load-avg: 3
reserved-memory: 0.1
max-waiting-time: 150s

 datasource:
  driver-class-name: com.mysql.cj.jdbc.Driver
  url: jdbc:mysql://node01:3306/dolphinscheduler2204?useUnicode=true&characterEncoding=UTF-8&useSSL=false&allowPublicKeyRetrieval=true
  username: root
  password: 123456
AI写代码
yaml
1
2
3
4
5
6
7
8
9
10
11
12
13
14
15
16
17
18
19
20
21
[root@node01 ~]# vi /home/apache-dolphinscheduler-3.2.0-bin/worker-server/conf/application.yaml

registry:
 type: zookeeper
 zookeeper:
  namespace: dolphinscheduler2204
  connect-string: node01:2181,node02:2181,node03:2181
max-cpu-load-avg: 3
reserved-memory: 0.1
max-waiting-time: 150s
AI写代码
yaml
1
2
3
4
5
6
7
8
[root@node01 ~]# vi /home/apache-dolphinscheduler-3.2.0-bin/api-server/conf/application.yaml

datasource:
  driver-class-name: com.mysql.cj.jdbc.Driver
  url: jdbc:mysql://node01:3306/dolphinscheduler2204?useUnicode=true&characterEncoding=UTF-8&useSSL=false&allowPublicKeyRetrieval=true
  username: root
  password: 123456

registry:
 type: zookeeper
 zookeeper:
  namespace: dolphinscheduler2204
  connect-string: node01:2181,node02:2181,node03:2181

 datasource:
  driver-class-name: com.mysql.cj.jdbc.Driver
  url: jdbc:mysql://node01:3306/dolphinscheduler2204?useUnicode=true&characterEncoding=UTF-8&useSSL=false&allowPublicKeyRetrieval=true
  username: root
  password: 123456
AI写代码
yaml
1
2
3
4
5
6
7
8
9
10
11
12
13
14
15
16
17
[root@node01 ~]# vi /home/apache-dolphinscheduler-3.2.0-bin/alert-server/conf/application.yaml

datasource:
  driver-class-name: com.mysql.cj.jdbc.Driver
  url: jdbc:mysql://node01:3306/dolphinscheduler2204?useUnicode=true&characterEncoding=UTF-8&useSSL=false&allowPublicKeyRetrieval=true
  username: root
  password: 123456

registry:
 type: zookeeper
 zookeeper:
  namespace: dolphinscheduler2204
  connect-string: node01:2181,node02:2181,node03:2181

 datasource:
  driver-class-name: com.mysql.cj.jdbc.Driver
  url: jdbc:mysql://node01:3306/dolphinscheduler2204?useUnicode=true&characterEncoding=UTF-8&useSSL=false&allowPublicKeyRetrieval=true
  username: root
  password: 123456
AI写代码
yaml
1
2
3
4
5
6
7
8
9
10
11
12
13
14
15
16
17
[root@node01 ~]# vi /home/apache-dolphinscheduler-3.2.0-bin/tools/conf/application.yaml

 datasource:
  driver-class-name: com.mysql.cj.jdbc.Driver
  url: jdbc:mysql://node01:3306/dolphinscheduler2204?useUnicode=true&characterEncoding=UTF-8&useSSL=false&allowPublicKeyRetrieval=true
  username: root
  password: 123456

 datasource:
  driver-class-name: com.mysql.cj.jdbc.Driver
  url: jdbc:mysql://node01:3306/dolphinscheduler2204?useUnicode=true&characterEncoding=UTF-8&useSSL=false&allowPublicKeyRetrieval=true
AI写代码
yaml
1
2
3
4
5
6
7
8
9
9、安装ds集群(一定启动zk(zkServer.sh start)和mysql，并保障可用)

[root@node01 ~]# sh /home/apache-dolphinscheduler-3.2.0-bin/bin/install.sh
AI写代码
sh
1


10、查看服务

[root@node01 ~]# jps
3377 MasterServer
3170 QuorumPeerMain
3714 Jps
3411 WorkerServer
3447 AlertServer
3481 ApiApplicationServer

[root@node02 ~]# jps
3042 Jps
2773 MasterServer
2805 WorkerServer
2622 QuorumPeerMain

[root@node03 ~]# jps
2546 QuorumPeerMain
2713 WorkerServer
2810 Jps
AI写代码
sh
1
2
3
4
5
6
7
8
9
10
11
12
13
14
15
16
17
18
11、Web UI访问

访问地址：http://192.168.10.101:12345/dolphinscheduler/ui
默认用户名/密码：admin/dolphinscheduler123


12、查看监控中心

#问题，假设node02的master启动后挂掉，则查看对一个日志

[root@node02 ~]# tail -200 /usr/local/dolphinscheduler-3.2.0/master-server/logs/dolphinscheduler-master.log
AI写代码
sh
1
#如果错误为zookeeper.connect.timeout等错误，重新单独启动挂掉的服务即可！！！
————————————————
版权声明：本文为CSDN博主「大数据东哥(Aidon)」的原创文章，遵循CC 4.0 BY-SA版权协议，转载请附上原文出处链接及本声明。
原文链接：https://blog.csdn.net/u010839779/article/details/148279439