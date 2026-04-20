# 日志系统

Task-Agent 提供了结构化的日志系统，支持多级别日志、上下文感知和可配置输出。

## 快速开始

```python
from task_agent.logging_config import get_logger

logger = get_logger(__name__)
logger.info("Processing event", extra={"event_id": "xxx"})
```

## 配置

通过环境变量配置日志级别：

| 环境变量 | 说明 | 默认值 |
|----------|------|--------|
| `TASK_AGENT_LOG_LEVEL` | 日志级别（DEBUG/INFO/WARNING/ERROR） | INFO |
| `TASK_AGENT_LOG_DEBUG` | 启用 DEBUG 级别（设为 1） | 关闭 |

```bash
# 方式 1: 直接指定级别
export TASK_AGENT_LOG_LEVEL=DEBUG

# 方式 2: 通过标志启用
export TASK_AGENT_LOG_DEBUG=1
```

## 模块化日志器

每个模块有专用的日志器：

```python
from task_agent.logging_config import (
    get_session_logger,      # session.py
    get_blackboard_logger,    # blackboard.py
    get_brains_logger,       # brains.py
    get_handoff_logger,      # handoff.py
    get_service_logger,      # service.py
    get_tracing_logger,      # tracing.py
)

session_logger = get_session_logger()
blackboard_logger = get_blackboard_logger()
```

## 上下文感知日志

使用 `set_log_context()` 设置上下文，后续日志自动包含：

```python
from task_agent.logging_config import set_log_context, get_logger

set_log_context(session_id="session-123", generation=5)
logger = get_logger(__name__)
logger.info("Processing window")
# 输出: 2026-04-20 10:30:00 [INFO] task_agent.session: Processing window [session_id=session-123 gen=5]
```

## 结构化日志辅助函数

提供便捷的日志函数，自动合并上下文：

```python
from task_agent.logging_config import get_logger, info, debug, warning, error

logger = get_logger(__name__)

# 基本用法
info(logger, "Task created", task_id="task-xxx")

# 带额外字段
warning(logger, "Task superseded",
         old_task_id="old-xxx", new_task_id="new-xxx")

# 带上下文（自动从 contextvars 合并）
set_log_context(session_id="sess-1")
error(logger, "Processing failed", generation=3)
# 自动包含 session_id 和 generation
```

## 日志输出示例

默认输出格式：
```
2026-04-20 10:30:00 [INFO] task_agent.session: Session initialized [session_id=default]
2026-04-20 10:30:01 [INFO] task_agent.session: Starting Fast Brain processing [generation=1 window_id=window-a1b2c3d4]
2026-04-20 10:30:02 [DEBUG] task_agent.blackboard: Window recorded: window-a1b2c3d4, total_windows=1 [window_id=window-a1b2c3d4 event_count=1]
```

DEBUG 模式下包含文件名和行号：
```
2026-04-20 10:30:00 [DEBUG] task_agent.session (session.py:88): Window recorded: window-a1b2c3d4 [window_id=window-a1b2c3d4]
```

## 日志级别使用指南

| 级别 | 使用场景 |
|------|----------|
| DEBUG | 详细调试信息、函数入口/出口、状态变化 |
| INFO | 重要流程节点：会话初始化、任务创建、脑启动 |
| WARNING | 任务替代、中断、异常但可恢复的情况 |
| ERROR | 处理失败、异常、API 错误 |

## 日志内容覆盖

| 模块 | 记录的日志 |
|------|-----------|
| session.py | 会话初始化、窗口提交、中断、Fast/Deep Brain 状态、事件发送 |
| blackboard.py | 窗口记录、处理状态更新、中断记录、意图变化、任务发布 |
| brains.py | 配置加载、模型调用、LLM 请求/响应 |
| handoff.py | Handoff 构建、任务交接 |
| service.py | 服务初始化、会话创建/获取、tracer 刷新 |

## 与 Tracing 的关系

日志系统与 Tracing（Langfuse）是互补的：

- **日志**: 本地持久化，适合调试和问题排查
- **Tracing**: 分布式追踪，适合性能分析和调用链可视化

两者可以同时启用，互不影响。
