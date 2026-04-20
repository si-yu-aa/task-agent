# Service 模块

`service.py` - 服务容器

## 职责

`TaskAgentService` 是整个系统的服务容器，负责：

1. **管理会话生命周期**：创建、获取、复用 Session
2. **管理 Brain 实例**：所有 Session 共享同一个 Brain
3. **管理追踪器**：统一的追踪配置

## 架构

```
TaskAgentService
├── tracer              # 统一追踪器
├── fast_brain          # 共享 Fast Brain 实例
├── deep_brain          # 共享 Deep Brain 实例
└── _sessions: dict     # 会话池 {session_id: TaskAgentSession}
```

## 单例模式

Service 采用延迟初始化模式：

1. 首次调用 `get_session(session_id)` 时创建 Session
2. 同一 session_id 返回相同实例
3. 所有 Session 共享 Brain 和 Tracer

## 使用方式

```python
from task_agent.service import TaskAgentService

service = TaskAgentService()

# 获取或创建会话
session = service.get_session("user-123")

# 多次调用返回同一实例
session2 = service.get_session("user-123")
assert session is session2
```

## 生命周期管理

### 创建

```python
service = TaskAgentService()
```

- 从环境变量加载配置
- 初始化 Brain（如果未提供）
- 初始化 Tracer

### 获取会话

```python
session = service.get_session(session_id)
```

- 如果会话不存在，创建新会话
- 新会话分配新的 Blackboard

### 关闭

```python
service.flush()
```

- 刷新追踪器（确保所有 span 上报）
- 应该在程序退出前调用

## 自定义 Brain

```python
service = TaskAgentService(
    fast_brain=custom_fast_brain,
    deep_brain=custom_deep_brain,
)
```

支持注入自定义 Brain 实现，用于：
- 单元测试（注入 mock）
- 不同的推理策略
