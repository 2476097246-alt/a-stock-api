# a-stock-data API Wrapper — 部署指南

## 方案 A：阿里云 ECS 部署（推荐，无额度限制）

### 前提
- 阿里云 ECS 一台（CentOS 7/8 或 Ubuntu 20.04/22.04，1C2G 足够）
- 安全组已放行 9000 端口（或你要用的端口）
- 本机可通过 SSH 连接 ECS

### 1. 连接 ECS

```bash
ssh root@<你的ECS公网IP>
```

### 2. 安装 Python 3（如果没有）

**Ubuntu/Debian：**
```bash
apt update && apt install -y python3 python3-pip
```

**CentOS：**
```bash
yum install -y python3 python3-pip
```

### 3. 克隆仓库

```bash
cd /opt
git clone https://github.com/2476097246-alt/a-stock-api.git
cd a-stock-api/api_wrapper
```

> 如果 GitHub 连不上（国内 ECS 常见），先在本地 `git clone` 然后用 `scp` 传上去：
> ```bash
> # 在本地执行
> scp -r api_wrapper root@<IP>:/opt/a-stock-api/
> ```

### 4. 安装依赖

```bash
cd /opt/a-stock-api/api_wrapper
pip3 install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com
```

### 5. 快速启动（前台测试）

```bash
export PORT=9000
python3 app.py
```

另开一个终端测试：
```bash
curl http://localhost:9000/api/v1/health
# 应返回 {"status":"success","data":{"message":"a-stock-data API Wrapper is running"...}}
```

### 6. 生产级部署（gunicorn + systemd 保活）

创建 systemd 服务文件：
```bash
cat > /etc/systemd/system/a-stock-api.service << 'EOF'
[Unit]
Description=A-Stock Data API
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/a-stock-api/api_wrapper
Environment=PORT=9000
ExecStart=/usr/bin/python3 -m gunicorn -w 4 -b 0.0.0.0:9000 app:app --timeout 60 --access-logfile /var/log/a-stock-api.log
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable a-stock-api
systemctl start a-stock-api
```

检查状态：
```bash
systemctl status a-stock-api
curl http://localhost:9000/api/v1/health
```

### 7. （可选）Nginx 反向代理 + 域名 + HTTPS

```bash
apt install -y nginx certbot python3-certbot-nginx
```

Nginx 配置 `/etc/nginx/sites-available/a-stock-api`：
```nginx
server {
    listen 80;
    server_name api.yourdomain.com;

    location / {
        proxy_pass http://127.0.0.1:9000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 120s;
    }
}
```

启用并申请 SSL：
```bash
ln -s /etc/nginx/sites-available/a-stock-api /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
certbot --nginx -d api.yourdomain.com
```

### 8. 阿里云安全组放行

登录阿里云控制台 → ECS → 安全组 → 添加规则：
- 端口：9000（或 80/443 如果用了 Nginx）
- 授权对象：0.0.0.0/0

### 9. Dify 接入

Dify 工具配置中，将 API endpoint 指向：
```
http://<ECS公网IP>:9000/api/v1/stock/...
```
或如果配置了域名：
```
https://api.yourdomain.com/api/v1/stock/...
```

### 常用运维命令

```bash
systemctl status a-stock-api     # 查看状态
systemctl restart a-stock-api    # 重启
journalctl -u a-stock-api -f     # 实时日志
tail -f /var/log/a-stock-api.log # 访问日志
```

### ECS 成本参考

| 配置 | 月费 | 够用吗 |
|------|------|--------|
| 1C1G (ecs.t6) | ~30元 | 够，QPS<10 |
| 1C2G (ecs.s6) | ~60元 | 充裕 |
| 2C4G | ~150元 | 绰绰有余 |

---

## 方案 B：阿里云 FC（需额度，暂不可用）

> 你的 FC 额度已用完，跳过此方案。恢复后可继续使用 `s deploy` 一键部署。

---

## 本地调试

```bash
cd api_wrapper
pip install -r requirements.txt
python app.py
# 访问 http://localhost:9000/api/v1/health
```
