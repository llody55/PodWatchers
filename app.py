import os
import time
import json
import logging
import threading
from datetime import datetime
from typing import Dict, List, Optional
from kubernetes import client, config, watch
from kubernetes.client.rest import ApiException
import requests

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 环境变量配置类
class AppConfig:
    def __init__(self):
        self.mute_seconds = int(os.getenv("MUTE_SECONDS", 30)) # 静默时间
        self.ignore_restart_count = int(os.getenv("IGNORE_RESTART_COUNT", 1)) # 忽略重启次数
        self.watched_namespaces = self._parse_namespaces(os.getenv("WATCHED_NAMESPACES", "")) # 监控的命名空间
        self.ignore_exit_zero = os.getenv("IGNORE_EXIT_CODE_ZERO", "true").lower() == "true" # 忽略退出码为0的重启
        self.webhook_url = os.getenv("WEBHOOK_URL", "") # 企业微信机器人Webhook地址
        self.cluster_name = os.getenv("CLUSTER_NAME", "default-cluster") # 集群名称
        self.log_lines = int(os.getenv("LOG_LINES", 20))  # 日志行数配置
        self.event_entries = int(os.getenv("EVENT_ENTRIES", 5))  # 事件条数配置
        self.watch_timeout = int(os.getenv("WATCH_TIMEOUT", 300))  # 命名空间超时时间配置
    
    @staticmethod
    def _parse_namespaces(raw: str) -> List[str]:
        return [ns.strip() for ns in raw.split(",") if ns.strip()] if raw else []

CONFIG = AppConfig()

# 告警状态管理
class AlertState:
    def __init__(self):
        self.history: Dict[str, datetime] = {}
        self.lock = threading.Lock()
    
    def should_alert(self, pod_uid: str) -> bool:
        with self.lock:
            last_alert = self.history.get(pod_uid)
            if last_alert and (datetime.now() - last_alert).total_seconds() < CONFIG.mute_seconds:
                return False
            self.history[pod_uid] = datetime.now()
            return True

alert_state = AlertState()

def load_k8s_config():
    try:
        config.load_incluster_config()
        logger.info("Loaded in-cluster Kubernetes config")
    except config.ConfigException:
        config.load_kube_config()
        logger.info("Loaded local Kubernetes config")

def get_restart_info(pod: client.V1Pod) -> dict:
    """获取重启相关完整信息"""
    if not pod.status.container_statuses:
        return {"count": 0, "exit_code": None, "reasons": []}
    
    restart_count = sum(c.restart_count for c in pod.status.container_statuses)
    exit_code = None
    reasons = []
    last_restart_time = None
    
    for cs in pod.status.container_statuses:
        if cs.last_state.terminated:
            exit_code = exit_code or cs.last_state.terminated.exit_code
            # logger.warning(f"restart time: {cs.last_state.terminated.finished_at}")
            reasons.append({
                "container": cs.name,
                "reason": cs.last_state.terminated.reason,
                "exit_code": cs.last_state.terminated.exit_code
            })
            last_restart_time = cs.last_state.terminated.finished_at
    
    return {
        "count": restart_count,
        "exit_code": exit_code,
        "reasons": reasons,
        "last_restart_time": last_restart_time
    }

def fetch_resource(
    func, 
    resource_type: str,
    **kwargs
) -> Optional[List[dict]]:
    """通用资源获取函数"""
    try:
        resp = func(**kwargs)
        return [item.to_dict() for item in resp.items]
    except ApiException as e:
        logger.error(f"Failed to get {resource_type}: {str(e)}")
        return None

def prepare_alert_data(pod: client.V1Pod) -> Optional[dict]:
    """准备告警数据"""
    restart_info = get_restart_info(pod)
    
    # 过滤条件
    if CONFIG.ignore_exit_zero and restart_info["exit_code"] == 0:
        logger.debug(f"Ignoring pod {pod.metadata.name} with exit code 0")
        return None
    
    if restart_info["count"] <= CONFIG.ignore_restart_count:
        logger.debug(f"Ignoring pod {pod.metadata.name} with restart count {restart_info['count']}")
        return None
    
    if not alert_state.should_alert(pod.metadata.uid):
        logger.info(f"Pod {pod.metadata.name} in mute period")
        return None
    
    # 收集数据
    alert_data = {
        "metadata": pod.metadata.to_dict(),
        "restart_info": restart_info,
        "events": fetch_resource(
            client.CoreV1Api().list_namespaced_event,
            "events",
            namespace=pod.metadata.namespace,
            field_selector=f"involvedObject.name={pod.metadata.name}"
        ),
        "logs": fetch_pod_logs(pod.metadata.namespace, pod.metadata.name)
    }
    # 添加节点信息
    alert_data["node_name"] = pod.spec.node_name if pod.spec else "Unknown"

    return alert_data

def format_events(events: List[dict]) -> str:
    """格式化事件信息"""
    if not events:
        return "No recent events"
    
    # 合并同类事件
    event_counts = {}
    for e in events:
        key = (e.get('reason'), e.get('message'))
        event_counts[key] = event_counts.get(key, 0) + 1

    output = []
    for (reason, msg), count in event_counts.items():
        if count > 1:
            output.append(f"× [{count}次] {reason}: {msg}")
        else:
            output.append(f"× {reason}: {msg}")
    
    return "\n".join(output[:CONFIG.event_entries])  # 默认显示最多5条关键事件

def fetch_pod_logs(namespace: str, name: str) -> str:
    """获取容器日志"""
    try:
        raw_logs = client.CoreV1Api().read_namespaced_pod_log(
            name=name,
            namespace=namespace,
            previous=True
        )
        # 按行数截断
        lines = raw_logs.split('\n')
        # logger.warning(f"历史 Raw logs: {raw_logs}")
        return '\n'.join(lines[-CONFIG.log_lines:])
    except ApiException as e:
        logger.error(f"Failed to get previous logs: {str(e)}")
        # 尝试获取当前日志
        try:
            raw_logs = client.CoreV1Api().read_namespaced_pod_log(
                name=name,
                namespace=namespace,
                previous=False
            )
            lines = raw_logs.split('\n')
            # logger.warning(f"当前 Raw logs: {raw_logs}")
            return '\n'.join(lines[-CONFIG.log_lines:]) + "\n[Note: Previous logs unavailable, showing current logs]"
        except ApiException as e2:
            logger.error(f"Failed to get current logs: {str(e2)}")
            return f"Logs unavailable: {e.reason}"

def format_logs(logs: str) -> str:
    """格式化日志信息"""
    if not logs:
        return "No logs available"
    
    return  logs  # 截断长日志
    # 提取关键错误行
    # error_keywords = ['error', 'exception', 'failed', 'warning']
    # filtered = [line for line in logs.split('\n') 
    #            if any(kw in line.lower() for kw in error_keywords)]
    # logger.warning(f"Filtered logs: {filtered}")
    # return "\n".join(filtered[-10:]) or "No obvious error logs"  # 显示最多3条关键日志

# 自定义 JSON 序列化函数
def json_serializable(obj):
    """处理无法直接序列化为 JSON 的对象"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")

def format_reasons(reasons: List[dict]) -> str:
    """格式化终止原因"""
    if not reasons:
        return "No termination reasons available"
    return "\n".join([
        f"容器 {r['container']}：{r['reason']} (Exit {r['exit_code']})"
        for r in reasons
    ])

def human_readable_time(seconds: float) -> str:
    """转换可读时间"""
    mins, sec = divmod(seconds, 60)
    hrs, min = divmod(mins, 60)
    return f"{int(hrs)}h {int(min)}m {int(sec)}s"

def send_alert(data: dict):
    """发送告警"""
    try:
        # 记录日志
        logger.warning(
            f"Alerting for pod {data['metadata']['name']} "
            f"in {data['metadata']['namespace']}. "
            f"Restart count: {data['restart_info']['count']}"
        )
        # 新增字段提取
        pod = data['metadata']
        node_name = data.get('node_name', 'Unknown')
        restart_info = data['restart_info']
        reasons = restart_info.get('reasons', [])
        # 格式化时间
        restart_time = restart_info.get('last_restart_time')
        
        send_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # 构造 Markdown
        md_content = f"""
### 🚨 Pod异常重启告警 - {CONFIG.cluster_name}

#### 基本信息
> 集群：<font color="warning">{CONFIG.cluster_name}</font>
> 命名空间：<font color="comment">{pod['namespace']}</font>
> Pod名称: <font color="warning">{pod['name']}</font>
> 运行节点：<font color="comment">{node_name}</font>
> 重启时间：<font color="warning">{restart_time}</font>
> 发送时间：<font color="comment">{send_time}</font>

#### 异常状态
>  重启次数：<font color="comment">{data['restart_info']['count'] or 'Unknown'}</font>
>  最后退出码：<font color="comment">{data['restart_info']['exit_code'] or 'Unknown'}</font>
>  最后容器：<font color="comment">{data['restart_info']['reasons'][0]['container'] or 'Unknown'}</font>

#### 终止原因

> {format_reasons(data['restart_info']['reasons'])} 

#### 最近事件
> {format_events(data['events'])} 

#### 关键日志

> {format_logs(data['logs'])[-CONFIG.log_lines*50:]} 
        """
        logger.warning(md_content)
        # 检查消息长度是否超限
        if len(md_content.encode('utf-8')) > 4096:
            logger.warning("Markdown content exceeds 4096 bytes, truncating logs...")
            # 截断日志内容
            logs = format_logs(data['logs'])
            max_log_length = 4096 - len(md_content) + len(logs)
            truncated_logs = logs[:max_log_length] + "\n[Logs truncated due to length limit]"
            md_content = md_content.replace(logs, truncated_logs)
        # 发送 Webhook
        resp = requests.post(
            CONFIG.webhook_url,
            json={"msgtype": "markdown", "markdown": {"content": md_content}},
            timeout=10
        )
        resp.raise_for_status()
        logger.info("Alert sent successfully")
        
    except Exception as e:
        logger.error(f"Failed to send alert: {str(e)}")
        raise  # 确保异常传播，便于调试

def watch_namespace(namespace: str):
    """监控指定命名空间或所有命名空间"""
    v1 = client.CoreV1Api()
    watcher = watch.Watch()
    while True:
        try:
            if namespace:  # 如果指定了具体的命名空间
                logger.info(f"Starting watch on namespace: {namespace}")
                stream = watcher.stream(
                    v1.list_namespaced_pod,
                    namespace=namespace,
                    timeout_seconds=CONFIG.watch_timeout
                )
            else:  # 如果 namespace 为空，监听所有命名空间
                logger.info("Starting watch on all namespaces")
                stream = watcher.stream(
                    v1.list_pod_for_all_namespaces,
                    timeout_seconds=CONFIG.watch_timeout
                )

            for event in stream:
                if event["type"] == "MODIFIED":
                    if data := prepare_alert_data(event["object"]):
                        send_alert(data)
        
        except Exception as e:
            logger.error(f"Watch error: {str(e)}")
            time.sleep(5)


if __name__ == "__main__":
    load_k8s_config()
    # 启动监控线程
    threads = []
    # 如果没有指定命名空间，则监听所有命名空间
    namespaces = CONFIG.watched_namespaces if CONFIG.watched_namespaces else [""]
    for ns in namespaces:
        thread = threading.Thread(
            target=watch_namespace,
            args=(ns,),
            daemon=True
        )
        thread.start()
        threads.append(thread)
        #logger.info(f"Started watcher for namespace: {ns or 'all'}")

    # 保持主线程存活
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("Shutting down monitor")
