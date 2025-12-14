---
inclusion: always
---
<!------------------------------------------------------------------------------------
   Add rules to this file or a short description and have Kiro refine them for you.
   
   Learn about inclusion modes: https://kiro.dev/docs/steering/#inclusion-modes
-------------------------------------------------------------------------------------> 
请记住 这个地址ec2-user@18.221.252.182,这是aws上可以运行该项目的堡垒机，安装了必要的依赖，配置文件也配置好了，包括秘钥。后续请修复代码后，自动git add , git commit -m"update", 所有的commit的内容都是 "update", 然后git push到branch 3.2.0dev. 

然后自动ssh ec2-user@18.221.252.182, 登入堡垒机
请在堡垒机上请使用conda 执行conda activate py312, 进去虚拟环境py312。这个虚拟环境已经装好了所需的依赖。
进入目录/home/ec2-user/work/dolphinscheduler-ec2-on-aws， 然后git pull ，更新堡垒机上的 3.2.0dev 分支，然后执行python cli.py create --config config.yaml 

整体命令为（ssh ec2-user@18.221.252.182 "cd /home/ec2-user/work/dolphinscheduler-ec2-on-aws && git pull && conda activate py312 && python cli.py create --config config.yaml"）
验证是否修复了bug ,如果没有，则根据程序输出，继续分析问题和修复，直到集群部署成功，验证集群可用



