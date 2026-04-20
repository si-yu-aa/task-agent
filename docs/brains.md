# Brains 模块

`brains.py` 定义了双脑协议、模型调用封装，以及 deep brain 的流式解析逻辑。

## 协议层

代码里优先使用 `Protocol`，这样：

- 单测可以轻松注入脚本化 brain
- 后续可以平滑替换成别的模型后端
- session 只依赖行为约定，不依赖具体实现

## FastBrain

```python
class FastBrain(Protocol):
    def acknowledge(self, window, snapshot) -> str | None: ...
    async def think(self, request) -> AsyncIterator[FastBrainChunk]: ...
    def react_to_deep_chunk(self, chunk, snapshot) -> ChatMessage | None: ...
```

### 当前职责

- 对最新窗口做快速判断
- 决定意图关系 `new/amend/replace/noop`
- 直接回答简单请求
- 快速创建任务
- 决定是否拉起 deep brain
- 决定 deep chunk 中哪些内容值得对外说

## DeepBrain

```python
class DeepBrain(Protocol):
    async def stream_think(self, request) -> AsyncIterator[DeepBrainChunk]: ...
```

### 当前职责

- 持续进行深度思考
- 流式输出结构化 chunk
- 尽早产出 `stage_task`
- 在执行进行时继续推进规划

## 模型策略

### 1. ChatGPT 家族统一关闭 provider-side reasoning

对于 ChatGPT 家族模型，请求会统一带：

- `reasoning_effort=none`

系统通过 prompt 中的标签协议显式引导模型输出可消费的思考结果。

### 2. fast brain 与 deep brain 使用不同调用方式

- fast brain：
  - `acknowledge()` 用文本补全
  - `think()` 用 JSON 结构化结果
- deep brain：
  - `stream_think()` 用流式文本
  - 再通过标签解析器转换成 `DeepBrainChunk`

## ModelFastBrain

### `acknowledge()`

目标是尽快给出一句自然语言确认。

### `think()`

fast brain 返回的是结构化 JSON，核心字段包括：

- `intent_summary`
- `relation`
- `response_text`
- `task`
- `delegate_to_deep`
- `delegation_message`

## ModelDeepBrain

`ModelDeepBrain.stream_think()` 是这次重构的关键。

它会：

1. 构建 deep-stream prompt
2. 通过 OpenAI 兼容接口做流式请求
3. 把流式 token 交给 `TaggedStreamParser`
4. 按标签吐出结构化 chunk

## TaggedStreamParser

解析器当前识别以下标签：

- `reasoning`
- `milestone`
- `stage_task`
- `warning`
- `final_summary`

其中 `stage_task` 内部要求是 JSON，对应一个可落地的 `TaskGoalCard`。
