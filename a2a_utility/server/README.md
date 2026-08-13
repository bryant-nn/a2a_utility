# a2a_utility.server

`a2a_utility` 的 server 端子套件——見 [`../../README.md`](../../README.md) 拿架構總覽、`schema`／
`client` 的說明、`HandlerResult`／`AgentHandlerPort`／三層擴充點的完整解釋。這份文件是它的延伸：一次
請求實際怎麼流過六角形的每一層，以及一個從零開始寫 domain agent 的完整範例。

## 目錄結構（角色對照表）

```
server/
├── domain/                          【領域層】純 Python，零框架依賴
│   ├── models/
│   │   ├── agent_card.py            AgentDescriptor（discovery 用的最小描述：name/description/agent_card_url）
│   │   └── principal.py             Principal（值物件）+ read_principal/write_principal（純函式操作 dict，
│   │                                 不 import 任何 a2a.* 型別——這是 application 層可以放心依賴它、
│   │                                 不會不小心反向依賴 adapters 的原因）
│   └── services/
│       └── discovery_service.py     rank_agents()：純函式，依 query 字詞比對 agent name/description 評分排序
│
├── application/                     【應用層】DTO + use case + port（介面）
│   ├── dtos.py                       ExtendedRequestContext（AgentHandlerPort 的輸入）+
│   │                                 HandlerCompleted/HandlerFailed/HandlerInputRequired/
│   │                                 HandlerAuthRequired/HandlerCanceled/HandlerResult（輸出）+ CancelResult
│   ├── ports/
│   │   ├── inbound/                 別人「可以呼叫我」的介面
│   │   │   ├── agent_handler_port.py       AgentHandlerPort（domain agent 實作的合約）
│   │   │   ├── on_cancel_port.py           OnCancelPort（可選：外部取消時的自訂訊息）
│   │   │   └── discovery_use_case_port.py  DiscoveryUseCasePort
│   │   └── outbound/                我「需要別人提供」的介面
│   │       └── registry_port.py            AgentRegistryPort
│   └── use_cases/
│       ├── register_agent_card_use_case.py  RegisterAgentCardUseCase：一次 register/heartbeat
│       └── search_agent_use_case.py         SearchAgentUseCase：list_all() 查全部、search() 查符合的
│
├── adapters/                        【轉接器層】跟外部世界打交道，依賴 application 的 port，不會被 domain 依賴
│   ├── inbound/                     外部世界怎麼呼叫進來
│   │   ├── agent_executor.py               AgentExecutor：真正的原生 AgentExecutor 子類別，建構子注入
│   │   │                                    AgentHandlerPort（+ 可選 OnCancelPort），execute()/cancel()
│   │   │                                    完全沒有自己的決策邏輯，純粹翻譯/分派
│   │   ├── call_context_builder.py         A2AUtilityCallContextBuilder：從 request 建 Principal 寫進
│   │   │                                    call_context.state；get_principal() 是給拿著原生 RequestContext
│   │   │                                    的人用的 escape hatch
│   │   └── discovery_agent_executor.py     DISCOVERY 模式：A2A Task ↔ DiscoveryUseCasePort
│   └── outbound/                    我怎麼呼叫外部世界
│       ├── event_queue_adapter.py          ExtendedEventQueue：包住原生 TaskUpdater，emit()/complete()/
│       │                                    failed()/requires_input()/requires_auth()/cancel()
│       └── in_memory_registry_adapter.py   把記憶體 dict + TTL 包成 AgentRegistryPort
│
├── config.py                        A2ASettings（A2A_ 前綴），pydantic-settings
├── app.py                           build_agent_card / create_app(mode=) / serve / serve_as_a2a — composition root
└── main.py                          獨立可執行節點：依 A2A_SERVER_MODE 決定組出 AGENT 示範節點還是 DISCOVERY 節點
```

**依賴方向永遠朝內**：`adapters` 依賴 `application` 的 port，`application` 依賴 `domain`，`domain` 不依賴
任何人。`app.py`／`main.py` 是 composition root，唯一被允許把所有層「兜」在一起的地方。

## 一次請求實際怎麼流過這些層（AGENT 模式）

```
coordinator 發 SendStreamingMessage
        │
        ▼
adapters/inbound/agent_executor.py  AgentExecutor.execute(context, event_queue)
   ├─ ctx = ExtendedRequestContext(context)          ← application/dtos.py
   ├─ eq  = ExtendedEventQueue(context, event_queue)  ← adapters/outbound/event_queue_adapter.py
   ├─ await eq.start_work()                            送初始 WORKING 狀態（"Processing request..."）
   ├─ result = await self._handler(ctx, eq.emit)        ← 呼叫你寫的 handler（AgentHandlerPort）
   │       │                                             每次 await eq.emit(part) 都即時送一則
   │       │                                             WORKING 狀態（帶這個 part 的 protobuf 形狀）
   │       │                                             → SSE 即時推給 coordinator
   │       ▼
   │   你的 handler 回傳一個 HandlerResult 變體
   │
   └─ isinstance(result, ...) 分派：
        HandlerCompleted   → eq.complete(result.parts)   加 artifact，送 TASK_STATE_COMPLETED（不帶 message，
        HandlerFailed      → eq.failed(result.message)   避免蓋掉真正答案——見根目錄 README 的踩過的坑）
        HandlerInputRequired → eq.requires_input(...)     TASK_STATE_INPUT_REQUIRED，等下一輪 execute() 帶著
        HandlerAuthRequired  → eq.requires_auth(...)      TASK_STATE_AUTH_REQUIRED    使用者回答再進來
        HandlerCanceled       → eq.cancel(...)             TASK_STATE_CANCELED（agent 自己決定放棄）

（未接住的 Python exception：try/except Exception 轉成 eq.failed(f"Agent error: {e}") ——
 這是「沒預期到的 bug」安全網，跟 handler 自己回傳 HandlerFailed 是兩回事。
 asyncio.CancelledError 不是 Exception 的子類別，不會被這個 except 攔住，
 讓外部取消能正常往上傳播。）
```

**外部取消**（client 端送 cancel RPC）走另一條、完全獨立的路徑：框架把跑 `execute()` 的 asyncio task
取消（`CancelledError` 往上傳，handler 自己的 `try/finally` 在這裡自然執行），同時呼叫
`AgentExecutor.cancel(context, event_queue)`——這裡才會用到 `OnCancelPort`（如果有給的話），沒給就
`eq.cancel(None)`，單純標記 `CANCELED`。

## 完整範例：從零寫一個新 domain agent

假設要新增一個 `joke_agent`（講笑話），目錄結構：

```
joke_agent/
  agent.py     # 你的業務邏輯，完全不 import 任何 a2a.*/a2a_utility
  server.py    # 接上 a2a_utility.server 的幾行
```

`joke_agent/agent.py`：

```python
import asyncio

async def handle(text: str, emit_thought=None) -> str:
    if emit_thought:
        await emit_thought("正在想一個跟「{}」有關的笑話...".format(text))
    await asyncio.sleep(1)
    return f"關於「{text}」：為什麼工程師分不清萬聖節跟聖誕節？因為 Oct 31 == Dec 25。"
```

`joke_agent/server.py`（`a2a_utility` 是 `pip install -e ./a2a_utility[server]` 裝進環境的，不用
`sys.path` hack）：

```python
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from a2a_utility.server import ExtendedRequestContext, HandlerCompleted, HandlerResult, PartEmitter, serve_as_a2a
from a2a_utility.schema import ExtendedPart, as_thinking_emitter
from agent import handle


async def handle_task(context: ExtendedRequestContext, emit: PartEmitter) -> HandlerResult:
    answer = await handle(context.get_user_input(), as_thinking_emitter(emit))
    return HandlerCompleted(parts=[ExtendedPart.from_text(answer)])


if __name__ == "__main__":
    serve_as_a2a(
        handler=handle_task,
        name="joke_agent",
        description="Tells a short joke related to whatever you ask about.",
        skill_id="joke",
        skill_name="Joke",
        skill_description="Tell a joke related to the given topic.",
        examples=["跟工程師有關的笑話", "tell me a joke about cats"],
        port=9050,
        registry_url="http://127.0.0.1:8090",   # 給了這個，啟動時就會自動登記 + 每 5 秒 heartbeat
    )
```

啟動：`python joke_agent/server.py &`——完成。不用碰任何 `a2a.*`／registry 的程式碼，`AgentExecutor`
（見上面的請求流程）幫你把 `handle_task` 包成合法的 A2A 端點，coordinator 下一次 `list_agents()` 就會
看到它。

## Discovery（「agent discover agent」）怎麼運作

### 啟動一個 DISCOVERY 節點

```bash
A2A_SERVER_MODE=DISCOVERY A2A_PORT=8090 python -m a2a_utility.server.main
```

這台節點**不會建立任何 handler／LLM 相關的東西**（`create_app(mode=ServerMode.DISCOVERY, ...)` 的
`_create_discovery_app()` 完全沒有走 `AgentExecutor`/`AgentHandlerPort` 那條路），只掛：

- `POST /register`：`{"name", "description", "agent_card_url"}` → `RegisterAgentCardUseCase.register()`
  → `InMemoryRegistryAdapter` 記下 `(descriptor, now)`。同一個 `name` 再 `register` 一次就是刷新
  heartbeat 時間戳。
- `GET /agents`：`SearchAgentUseCase.list_all()` → 回傳 TTL（預設 15 秒）內有心跳的全部 agent。
- `POST /` 與 `POST /a2a/v1/discovery`（同一個 JSON-RPC dispatcher 掛在兩個路徑上）：**真的 A2A
  `SendMessage`/`SendStreamingMessage`**，訊息文字當作查詢字串，經 `DefaultRequestHandler` →
  `adapters/inbound/discovery_agent_executor.py` 的 `DiscoveryAgentExecutor` →
  `SearchAgentUseCase.search()` → `domain/services/discovery_service.py` 的 `rank_agents()` 依 query
  字詞對 name/description 評分排序，回傳最相關的在前面（純空白字串的查詢 = 回傳全部，走
  `list_all()`）。回傳的 `artifact` 是一段 JSON 字串（`{"agents": [...]}`），跟 `GET /agents` 的資料
  形狀一致，只是包在 A2A Task 裡。

### Domain agent 這端：自動登記，不用手動呼叫

不用自己呼叫 `/register`——`serve_as_a2a(..., registry_url="http://127.0.0.1:8090")` 就會自動做：

```python
# app.py 內部（簡化）
async def _heartbeat_loop(registry_url, payload):
    async with httpx.AsyncClient() as client:
        while True:
            await client.post(f"{registry_url}/register", json=payload)  # payload = {name, description, agent_card_url}
            await asyncio.sleep(REGISTRY_HEARTBEAT_SECONDS)  # 5 秒
```

這個 loop 是用 Starlette `lifespan` 開的背景 task，跟著 AGENT server 的生命週期一起活著。

### Coordinator 這端：怎麼查、怎麼叫

用 [`a2a_utility.client.DiscoveryClient`](../client/discovery_client.py) 查目錄：

```python
from a2a_utility.client import DiscoveryClient

directory = DiscoveryClient("http://127.0.0.1:8090")
agents = await directory.list_agents()   # [{"name": ..., "description": ...}, ...]
entry  = await directory.resolve(name)   # {"description": ..., "agent_card_url": ...} | None
```

真正要「呼叫」某個 agent，coordinator 是拿 `entry["agent_card_url"]` 算出 base_url 去起 A2A client——
google-adk 用 `RemoteA2aAgent`（框架內建），deepagents 用
[`a2a_utility.client.call_agent_result`](../client/agent_client.py) 直接呼叫。**呼叫本身不透過
discovery 節點**，discovery 節點只負責回答「有誰、在哪」。

### 直接測 discovery 節點

```bash
# 手動登記一個假的
curl -s -X POST http://127.0.0.1:8090/register \
  -H "Content-Type: application/json" \
  -d '{"name":"demo_agent","description":"a demo","agent_card_url":"http://127.0.0.1:9999/.well-known/agent-card.json"}'

# 查全部
curl -s http://127.0.0.1:8090/agents | python3 -m json.tool

# 依查詢字詞排序找誰能做某件事（真的 A2A SendMessage，不是 plain REST）
curl -s -X POST http://127.0.0.1:8090/a2a/v1/discovery \
  -H "Content-Type: application/json" -H "A2A-Version: 1.0" \
  -d '{"jsonrpc":"2.0","id":"1","method":"SendMessage",
       "params":{"message":{"messageId":"m1","role":"ROLE_USER","parts":[{"text":"weather"}]}}}'
```

## 已知限制／待改善

見根目錄 [`../../README.md`](../../README.md#已知限制待改善)——避免同一份清單維護兩份、內容漂移。
