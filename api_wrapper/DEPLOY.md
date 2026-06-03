# a-stock-data API Wrapper — 阿里云函数计算 部署指南

## 概述

本目录包含将 A 股数据 API 部署到阿里云函数计算（FC）的全部配置。
部署后，DeepSeek 等大模型可通过 HTTP 调用龙虎榜、概念板块、资金流向三个核心数据接口。

---

## 文件清单

```
api_wrapper/
├── app.py            # Flask 应用（3个GET路由 + 健康检查）
├── stock_core.py     # 核心抓取模块（6个函数，从SKILL.md提取）
├── requirements.txt  # Python 依赖
├── openapi.yaml      # OpenAPI 3.0 接口文档（给大模型读的工具说明书）
├── s.yaml            # Serverless Devs 部署配置
├── bootstrap         # FC 自定义运行时启动脚本
├── .fcignore         # 部署忽略文件清单
└── DEPLOY.md         # 本文件
```

---

## 前置准备

### 1. 安装 Serverless Devs

```bash
npm install -g @serverless-devs/s
```

### 2. 配置阿里云账号

```bash
s config add \
  --AccountID <你的阿里云AccountID> \
  --AccessKeyID <你的AccessKey ID> \
  --AccessKeySecret <你的AccessKey Secret>
```

> 建议使用 RAM 子账号，最小权限：AliyunFCFullAccess + AliyunOSSFullAccess

### 3. 给 bootstrap 添加执行权限（macOS/Linux）

```bash
chmod +x api_wrapper/bootstrap
```

> Windows 用户跳过此步，FC 运行环境是 Linux，部署时会自动处理

---

## 部署（三种方案）

### 方案一：Serverless Devs 一键部署（推荐）

```bash
cd api_wrapper
s deploy
```

部署成功后，终端会输出公网访问 URL，格式类似：
```
https://<id>.<region>.fc.aliyuncs.com
```

测试：
```bash
curl https://<你的域名>/api/v1/health
curl "https://<你的域名>/api/v1/stock/concept?code=688017"
```

### 方案二：阿里云 FC 控制台手动部署

1. 将 `api_wrapper/` 内容打包为 zip（不含 s.yaml 和 .fcignore）
2. 登录 [FC 控制台](https://fc.console.aliyun.com)
3. 创建函数 → 选择「自定义运行时」→ runtime 选 `custom.debian10`
4. 上传 zip 包
5. 启动命令填：`./bootstrap`
6. 环境变量加：`PORT=9000`
7. 创建 HTTP 触发器 → 认证方式选「无需认证」

### 方案三：自定义容器（Docker）

创建 `Dockerfile`：
```dockerfile
FROM python:3.10-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
EXPOSE 9000
CMD ["python", "app.py"]
```

构建并推送到阿里云容器镜像服务：
```bash
docker build -t registry.cn-hangzhou.aliyuncs.com/<namespace>/a-stock-api:latest .
docker push registry.cn-hangzhou.aliyuncs.com/<namespace>/a-stock-api:latest
```

然后在 FC 控制台创建函数时选择「使用容器镜像」。

---

## 配置调整

### 内存和超时

编辑 `s.yaml` 中的 `vars` 区块：
```yaml
vars:
  memorySize: 512    # MB，东财接口有时响应慢可以调到 1024
  timeout: 60        # 秒，120日资金流数据量大可以调到 120
```

### 实例并发

`instanceConcurrency: 10` 表示每个实例最多同时处理 10 个请求。
注意：东财接口有内置串行节流，建议并发不要太高。

### 自定义域名

1. 在 FC 控制台 → 域名管理 → 添加自定义域名
2. 绑定你的域名并配置 SSL 证书
3. 将 DeepSeek 的 API 工具 URL 指向自定义域名

---

## 限流与成本

### 东财防封

东财接口有频率限制，本 API 已内置 `em_get()` 串行节流：
- 最小间隔 1 秒 + 随机抖动 0.1~0.5 秒
- 会话 Keep-Alive 复用
- **批量并发调用建议限制在 5 QPS 以内**

### FC 成本预估

| 配置 | 单价 | 月调用量 | 月成本 |
|------|------|---------|--------|
| 512MB / 0.5vCPU | ~0.00006元/次 | 10万次 | ~6元 |
| 512MB / 0.5vCPU | ~0.00006元/次 | 100万次 | ~60元 |

---

## 作为 DeepSeek 工具注册

将以下配置加入 DeepSeek 的 Function Calling / Tool Use 定义：

```json
{
  "type": "function",
  "function": {
    "name": "query_concept_blocks",
    "description": "查询A股个股所属的概念板块、行业分类和地域归属",
    "parameters": {
      "type": "object",
      "properties": {
        "code": {
          "type": "string",
          "description": "6位股票代码，如 688017"
        }
      },
      "required": ["code"]
    }
  }
}
```

> 完整的三个工具定义见 `openapi.yaml`，可直接转换为 OpenAI/DeepSeek tool schema。

---

## 本地调试

```bash
cd api_wrapper
pip install -r requirements.txt
python app.py
```

访问 `http://localhost:9000/api/v1/health` 确认正常运行。
