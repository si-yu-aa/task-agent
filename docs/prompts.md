# Prompts 模块

`prompts.py` - Prompt 模板

## 概述

Task-Agent 通过精心设计的 Prompt 引导 LLM 进行推理。Prompt 设计遵循：

- **角色驱动**：明确 LLM 的身份和职责
- **事件重要性判断**：教导 LLM 区分不同类型的事件
- **决策策略**：明确意图关系的选择标准
- **任务策略**：定义什么是好的任务卡片

## 默认角色提示

```text
You are task-agent, the robot's brain for task generation and task management.
```

核心职责：
- 快速响应新事件窗口
- 判断事件重要性
- 保持中断连续性
- 将意图转化为结构化任务卡片
- 仅在必要时升级到深度推理

## 事件重要性策略

| 事件类型 | 重要性 | 原因 |
|----------|--------|------|
| `NLP_MESSAGE` | 通常重要 | 可能改变前台目标 |
| `TASK_FEEDBACK` | 权威重要 | 任务结果的权威来源 |
| `ACTION_INFO` | 几乎不重要 | 上下文执行追踪 |
| `SYSTEM_INFO` | 视情况 | 仅当实质性改变优先级时 |
| `OTHERS` | 视情况 | 根据紧急性、安全性等判断 |

## 意图关系决策

| 关系 | 条件 |
|------|------|
| `new` | 窗口建立了新的前台目标 |
| `amend` | 窗口扩展或细化当前目标 |
| `replace` | 新重要事件使之前目标失效 |
| `noop` | 窗口不应改变前台意图 |

## 任务策略

- 任务是一个 goal-card，不是低层动作脚本
- 偏好简洁、可执行的目标
- 包含明确的 context、constraints、completion_criteria
- 如果信息缺失，做合理默认而非阻塞

## Prompt 构建函数

| 函数 | 用途 |
|------|------|
| `build_ack_prompt()` | 构建