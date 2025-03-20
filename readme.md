# Kubernetes Pod 异常重启监控告警系统

[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

基于Kubernetes API开发的Pod异常重启监控系统，实时检测容器异常状态，并通过企业微信机器人发送结构化告警信息。

## 功能特性

- 🚨 **实时监控**：监听所有命名空间Pod状态变化
- 🔍 **智能过滤**：
  - 屏蔽正常退出（Exit Code 0）
  - 忽略低频率重启（可配置阈值）
  - 静默期防骚扰（默认30秒）
- 📦 **多维度信息收集**：
  - 容器终止原因
  - 最近K8s事件（可配置显示条数）
  - 关键日志片段（可配置显示行数）
- 📡 **企业微信集成**：支持Markdown格式告警
- ⚙️ **灵活配置**：全部参数通过环境变量控制

## 环境要求

- Python 3.9+
- Kubernetes 1.20+ 集群
- 有效的kubeconfig配置（in-cluster或本地配置）
- 企业微信机器人Webhook地址

## 快速开始

### 安装依赖

```bash
pip install -r requirements.txt
```

### 基础配置

```bash
# 必需配置
export WEBHOOK_URL="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=your_key"

# 可选配置（默认值展示）
export CLUSTER_NAME="dev-cluster"         # 集群标识名称
export MUTE_SECONDS=30                     # 静默时间（秒）
export LOG_LINES=20                        # 显示日志行数
export EVENT_ENTRIES=5                     # 显示事件条数
export IGNORE_RESTART_COUNT=1              # 重启次数阈值
export WATCHED_NAMESPACES="default,dev"    # 监控的命名空间（空=全部）
```

### 运行监控

```bash
python3 pod_monitor.py
```

## Docker 运行

```bash
docker run -d \
  -v ~/.kube/config:/root/.kube/config \
  -e WEBHOOK_URL="your_webhook_url" \
  -e CLUSTER_NAME="prod-cluster" \
  -e LOG_LINES=15 \
  -e EVENT_ENTRIES=3 \
  --name k8s-monitor \
  swr.cn-southwest-2.myhuaweicloud.com/llody/podwatchers:v1.0-amd64
```

## K8S运行

```yaml
---
apiVersion: v1
kind: Namespace
metadata:
  name: podwatchers
---
# ServiceAccount
apiVersion: v1
kind: ServiceAccount
metadata:
  name: podwatchers
  namespace: podwatchers
---
# ClusterRole
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: podwatchers
rules:
- apiGroups: [""]
  resources: ["pods", "events", "nodes"]
  verbs: ["get", "list", "watch"]
- apiGroups: [""]
  resources: ["pods/log"]
  verbs: ["get"]
- apiGroups: [""]
  resources: ["pods/status"]
  verbs: ["update"]
---
# ClusterRoleBinding
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: podwatchers
subjects:
- kind: ServiceAccount
  name: podwatchers
  namespace: podwatchers
roleRef:
  kind: ClusterRole
  name: podwatchers
  apiGroup: rbac.authorization.k8s.io
---
apiVersion: v1
kind: Secret
metadata:
  name: podwatchers-secrets
  namespace: podwatchers
type: Opaque
stringData:
  WEBHOOK_URL: "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key="  
  CLUSTER_NAME: "prod-cluster"  
  MUTE_SECONDS: "600"  
  IGNORE_RESTART_COUNT: "1"
  LOG_LINES: "200"
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: podwatchers
  namespace: podwatchers
spec:
  replicas: 1
  selector:
    matchLabels:
      app: podwatchers
  template:
    metadata:
      labels:
        app: podwatchers
    spec:
      serviceAccountName: podwatchers
      containers:
      - name: collector
        image: swr.cn-southwest-2.myhuaweicloud.com/llody/podwatchers:v1.0-amd64
        imagePullPolicy: Always
        env:
        - name: CLUSTER_NAME
          valueFrom:
            secretKeyRef:
              name: podwatchers-secrets
              key: CLUSTER_NAME
        - name: WEBHOOK_URL
          valueFrom:
            secretKeyRef:
              name: podwatchers-secrets
              key: WEBHOOK_URL
        - name: MUTE_SECONDS
          valueFrom:
            secretKeyRef:
              name: podwatchers-secrets
              key: MUTE_SECONDS
        - name: IGNORE_RESTART_COUNT
          valueFrom:
            secretKeyRef:
              name: podwatchers-secrets
              key: IGNORE_RESTART_COUNT
        resources:
          requests:
            cpu: 100m
            memory: 128Mi
          limits:
            cpu: 200m
            memory: 256Mi

```

## 目前镜像

```bash
# adm64
swr.cn-southwest-2.myhuaweicloud.com/llody/podwatchers:v1.0-amd64

# arm64
swr.cn-southwest-2.myhuaweicloud.com/llody/podwatchers:v1.0-arm64
```

## 告警示例

```markdown
### 🚨 Pod异常重启告警 - prod-cluster

#### 基本信息
> 集群：prod-cluster
> 命名空间：dev
> Pod名称: app-6666c886df-xhb4n
> 运行节点：node-03 (192.168.10.3)
> 最近重启：2024-03-18 09:12:35
> 发送时间：2024-03-18 09:12:37

#### 异常状态
> 重启次数：8
> 最后退出码：137
> 触发容器：spring-boot

#### 终止原因
> 容器 spring-boot：OOMKilled (Exit 137)

#### 最近事件（最近3条）
× Unhealthy: Liveness probe failed
× Killing: Container failed liveness probe
× BackOff: Container restarting

#### 关键日志（最近15行）
2024-03-18 09:12:35.894 ERROR [main] o.s.c.a.AnnotationConfigApplicationContext 
2024-03-18 09:12:35.895  WARN [main] o.s.b.f.s.DefaultListableBeanFactory 
```

## 配置参数

| 环境变量              | 必须 | 默认值          | 说明                   |
| --------------------- | ---- | --------------- | ---------------------- |
| CLUSTER_NAME          | 建议 | default-cluster | 集群名称               |
| WEBHOOK_URL           | 必须 | ""              | Webhook地址            |
| IGNORE_EXIT_CODE_ZERO |      | true            | 忽略正常退出(0)        |
| WATCHED_NAMESPACES    |      | ""              | 监控命名空间(逗号分隔) |
| LOG_LINES             |      | 20              | 显示日志行数（新增）   |
| EVENT_ENTRIES         |      | 5               | 显示事件条数（新增）   |
| WEBHOOK_TIMEOUT       |      | 10              | 微信接口超时(秒)       |
| WATCH_TIMEOUT         |      | 300             | 命名空间超时时间配置   |

## 注意事项

1. **权限要求** ：

* 需要集群范围的Pod读权限
* 需要访问Events API的权限

1. **日志截断策略** ：

* 仅保留最后N行（由LOG_LINES控制）
* 日志最大长度不超过企业微信接口限制（约4096字符）

1. **性能建议** ：

* 生产环境建议每个集群部署单个实例
* 监控大量命名空间时适当调高 `timeout_seconds`

## 许可证

[Apache-2.0](https://license/)
