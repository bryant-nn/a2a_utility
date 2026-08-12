# a2a_utility.server

一個六角形架構（Ports & Adapters）／DDD 分層的 A2A server 套件，是 [a2a_utility](../../README.md) 這個
獨立 library（以 git submodule 的形式被各個 repo 引用）裡負責「開 A2A 端點」的那一半——另一半是
[a2a_utility.client](../client/)，見那邊的 README。這個 repo（`multi_agent_a2a`）裡所有 domain agent
（`calculate_agent`／`weather_agent`／`web_search_agent`／`fake_agent`）跟服務發現節點，都是靠這個
package 對外開 A2A 協定的端點——domain agent 自己完全不用碰 A2A、HTTP、SSE 這些細節。

設計依據見 `plan.md`（原始規格文件，隨 `a2a_server` 一起搬進這個獨立 repo 前留在 `multi_agent_a2a` 專案根目錄）。

## 1. 這個套件解決什麼問題

一個 domain agent（不管內部是 nanobot、LangChain deepagents、google-adk 還是純 mock 寫的）只想暴露
一個能力：「給我一句話，我用一段時間想一想（可選，即時串流），最後回你一句答案」。`a2a_server` 把這件事
包成兩種可以獨立運行的角色：

- **AGENT 節點**：把某個 domain agent 的能力，用 A2A 協定（JSON-RPC + SSE）開放給外界呼叫，並在啟動時
  向 registry 登記自己。
- **DISCOVERY 節點**：一個不含任何 LLM 的輕量節點，只做「誰活著、誰能做什麼」的登記與查詢——這個 repo
  裡的 coordinator（`root_agent`／`root_agent_deep_agent`）就是靠它動態發現 domain agent，不用寫死清單。

## 2. 架構：六角形分層

```
a2a_utility/server/
├── domain/                          【領域層】純 Python，零框架依賴
│   ├── models/
│   │   ├── agent_card.py            AgentDescriptor（discovery 用的最小描述：name/description/agent_card_url）
│   │   └── chat_message.py          StreamChunk（THINKING/ANSWER 兩種 phase 的一段串流文字）
│   └── services/
│       └── discovery_service.py     rank_agents()：純函式，依 query 字詞比對 agent name/description 評分排序
│
├── application/                     【應用層】use case + port（介面）
│   ├── ports/
│   │   ├── inbound/                 別人「可以呼叫我」的介面
│   │   │   ├── chat_use_case_port.py        ChatUseCasePort
│   │   │   └── discovery_use_case_port.py   DiscoveryUseCasePort
│   │   └── outbound/                我「需要別人提供」的介面
│   │       ├── llm_port.py                  LLMServicePort
│   │       └── registry_port.py             AgentRegistryPort
│   └── use_cases/
│       ├── chat_use_case.py                 ChatUseCase：目前是薄轉發，是之後要加 guardrail／重試的地方
│       ├── register_agent_card_use_case.py  RegisterAgentCardUseCase：一次 register/heartbeat
│       └── search_agent_use_case.py         SearchAgentUseCase：list_all() 給「查全部」、search() 給「查符合的」
│
├── adapters/                        【轉接器層】跟外部世界打交道，依賴 application 的 port，不會被 domain 依賴
│   ├── inbound/                     外部世界怎麼呼叫進來
│   │   ├── chat_agent_executor.py         AGENT 模式：A2A Task ↔ ChatUseCasePort
│   │   └── discovery_agent_executor.py    DISCOVERY 模式：A2A Task ↔ DiscoveryUseCasePort（見 §7 已知限制）
│   └── outbound/                    我怎麼呼叫外部世界
│       ├── handler_llm_adapter.py         把 domain agent 自己的 handle() 包成 LLMServicePort（★ 本專案主力）
│       ├── openai_llm_adapter.py          把 OpenAI 相容端點包成 LLMServicePort（main.py 內建示範用）
│       └── in_memory_registry_adapter.py  把記憶體 dict + TTL 包成 AgentRegistryPort
│
├── config.py                        A2ASettings（A2A_ 前綴）／LLMSettings（LLM_ 前綴），pydantic-settings
├── server.py                        serve_as_a2a()：domain agent 真正呼叫的入口（AGENT-only，pluggable handler）
└── main.py                          獨立可執行節點：依 A2A_SERVER_MODE 決定組出 AGENT демо節點還是 DISCOVERY 節點
```

**依賴方向永遠朝內**：`adapters` 依賴 `application` 的 port，`application` 依賴 `domain`，`domain` 不依賴任何人。
`server.py`／`main.py` 是 composition root，唯一被允許把所有層「兜」在一起的地方。

## 3. 兩種運行模式

| | AGENT（domain agent 用這個） | DISCOVERY（registry 用這個） |
|---|---|---|
| 啟動方式 | `serve_as_a2a(...)`（domain agent 的 `server.py` import 呼叫） | `A2A_SERVER_MODE=DISCOVERY python -m a2a_utility.server.main` |
| 掛載的路由 | `GET /.well-known/agent-card.json`、`POST /`（JSON-RPC，`SendMessage`/`SendStreamingMessage`） | `GET /.well-known/agent.json`、`POST /register`（plain REST）、`GET /agents`（plain REST）、`POST /` 與 `POST /a2a/v1/discovery`（真的 A2A JSON-RPC，走 `DiscoveryAgentExecutor`） |
| 用到的 outbound adapter | `HandlerLLMAdapter`（包住你的 `handle()`） | `InMemoryRegistryAdapter`（**不會建立任何 LLM adapter**——這是 plan.md 特別要求的：DISCOVERY 節點零 LLM 成本） |
| 這個 repo 的實例 | `calculate_agent:9020`／`weather_agent:9030`／`web_search_agent:9010`／`fake_agent:9040` | registry:8090（取代舊的 `registry/server.py`） |

`main.py` 本身也內建了一個「AGENT 模式示範」（`A2A_SERVER_MODE=AGENT python -m a2a_utility.server.main`），走
`OpenAILLMAdapter`——這是給人看六角形架構「換一顆 LLM adapter、use case 完全不用改」的示範，**這個 repo
真正的 4 個 domain agent 不是走這條路**，它們是各自的 `server.py` 呼叫 `serve_as_a2a()`。

## 4. 核心型別（跨兩種模式共用的語言）

```python
# domain/models/chat_message.py
class StreamPhase(str, Enum):
    THINKING = "thinking"   # 模型/agent 的中間推理，不是最終答案
    ANSWER   = "answer"     # 真正要給使用者看的內容

@dataclass(frozen=True)
class StreamChunk:
    phase: StreamPhase
    text: str

# domain/models/agent_card.py
@dataclass(frozen=True)
class AgentDescriptor:
    name: str
    description: str
    agent_card_url: str

# adapters/outbound/handler_llm_adapter.py（domain agent 作者唯一要認識的型別）
ThoughtEmitter = Callable[[str], Awaitable[None]]
Handler        = Callable[[str, ThoughtEmitter], Awaitable[str]]
```

## 5. Domain agent 怎麼使用它

### 5.1 你只需要寫一個 `handle()`

合約就是 `Handler` 型別：

```python
async def handle(text: str, emit_thought: ThoughtEmitter) -> str:
    ...
```

- `text`：使用者的問題，純字串，A2A/JSON-RPC 早就被拆掉了
- `emit_thought(step: str)`：想讓 trace UI 即時顯示思考過程就呼叫（可選，可以完全不呼叫）
- 回傳值：最終答案，純字串

你的 `handle()` 完全不 import 任何 `a2a.*`、不知道自己被 HTTP 包起來、也不知道有 registry 這件事存在。

### 5.2 完整範例：從零寫一個新 domain agent

假設要新增一個 `joke_agent`（講笑話），目錄結構：

```
joke_agent/
  agent.py     # 你的業務邏輯
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

`joke_agent/server.py`（`a2a_utility` 是 `pip install -e ./a2a_utility[server]` 裝進環境的，不用 sys.path hack）：

```python
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent.parent / ".env")

from a2a_utility.server import serve_as_a2a
from agent import handle

if __name__ == "__main__":
    serve_as_a2a(
        name="joke_agent",
        description="Tells a short joke related to whatever you ask about.",
        skill_id="joke",
        skill_name="Joke",
        skill_description="Tell a joke related to the given topic.",
        examples=["跟工程師有關的笑話", "tell me a joke about cats"],
        handler=handle,
        port=9050,
        registry_url="http://127.0.0.1:8090",   # 給了這個，啟動時就會自動登記 + 每 5 秒 heartbeat
    )
```

啟動：`python joke_agent/server.py &`——完成。不用碰任何 A2A/registry 的程式碼，coordinator 下一次
`list_agents()` 就會看到它。

### 5.3 一次請求實際怎麼流過這些層（AGENT 模式）

```
coordinator 發 SendStreamingMessage
        │
        ▼
adapters/inbound/chat_agent_executor.py  ChatAgentExecutor.execute()
   ├─ 送初始 WORKING 狀態（"Processing request..."）
   ├─ 呼叫 ChatUseCasePort.handle(query)
   │       │
   │       ▼
   │  application/use_cases/chat_use_case.py  ChatUseCase
   │       │  （目前是薄轉發，之後要加 prompt 處理/重試就改這裡）
   │       ▼
   │  application/ports/outbound/llm_port.py  LLMServicePort（介面）
   │       │
   │       ▼
   │  adapters/outbound/handler_llm_adapter.py  HandlerLLMAdapter
   │       │  await handler(query, emit_thought)  ← 呼叫你寫的 handle()
   │       │  emit_thought(text) 每呼叫一次 → yield StreamChunk.thinking(text)
   │       │  handle() 回傳 → yield StreamChunk.answer(text)
   │       ▼
   ├─ 收到 THINKING chunk → 再送一次 WORKING 狀態（帶這段文字）→ SSE 即時推給 coordinator
   └─ 收到 ANSWER chunk → 累積，最後包成 artifact
   → 送 TASK_STATE_COMPLETED（刻意不帶 message，避免蓋掉真正答案——見 §7／根目錄 README 的踩過的坑）
```

## 6. Discovery（「agent discover agent」）怎麼運作

### 6.1 啟動一個 DISCOVERY 節點

```bash
A2A_SERVER_MODE=DISCOVERY A2A_PORT=8090 python -m a2a_utility.server.main
```

這台節點**不會建立 `OpenAILLMAdapter`**（`main.py:_run_discovery()` 完全沒有 import/建構它），只掛：

- `POST /register`：`{"name", "description", "agent_card_url"}` → `RegisterAgentCardUseCase.register()` →
  `InMemoryRegistryAdapter` 記下 `(descriptor, now)`。同一個 `name` 再 `register` 一次就是刷新 heartbeat 時間戳。
- `GET /agents`：`SearchAgentUseCase.list_all()` → 回傳 TTL（預設 15 秒）內有心跳的全部 agent。
- `POST /` 與 `POST /a2a/v1/discovery`（同一個 JSON-RPC dispatcher 掛在兩個路徑上）：**真的 A2A
  `SendMessage`/`SendStreamingMessage`**，訊息文字當作查詢字串，經 `DefaultRequestHandler` →
  `adapters/inbound/discovery_agent_executor.py` 的 `DiscoveryAgentExecutor` → `SearchAgentUseCase.search()`
  → `domain/services/discovery_service.py` 的 `rank_agents()` 依 query 字詞對 name/description 評分排序，
  回傳最相關的在前面（純空白字串的查詢 = 回傳全部，走 `list_all()`）。回傳的 `artifact` 是一段 JSON 字串
  （`{"agents": [...]}`），跟 `GET /agents` 的資料形狀一致，只是包在 A2A Task 裡。

### 6.2 Domain agent 這端：自動登記，不用手動呼叫

不用自己呼叫 `/register`——`serve_as_a2a(..., registry_url="http://127.0.0.1:8090")` 就會自動做：

```python
# server.py 內部（簡化）
async def _heartbeat_loop(registry_url, payload):
    async with httpx.AsyncClient() as client:
        while True:
            await client.post(f"{registry_url}/register", json=payload)  # payload = {name, description, agent_card_url}
            await asyncio.sleep(REGISTRY_HEARTBEAT_SECONDS)  # 5 秒
```

這個 loop 是用 Starlette `lifespan` 開的背景 task，跟著 AGENT server 的生命週期一起活著。

### 6.3 Coordinator 這端：怎麼查、怎麼叫

兩個 coordinator（`root_agent/dynamic_dispatch.py`、`root_agent_deep_agent/dynamic_dispatch.py`）現在都是
用 [`a2a_utility.client.DiscoveryClient`](../client/discovery_client.py) 查目錄，不再各自手刻一份 `httpx`：

```python
from a2a_utility.client import DiscoveryClient

directory = DiscoveryClient("http://127.0.0.1:8090")
agents = await directory.list_agents()   # [{"name": ..., "description": ...}, ...]
entry  = await directory.resolve(name)   # {"description": ..., "agent_card_url": ...} | None
```

真正要「呼叫」某個 agent，coordinator 是拿 `entry["agent_card_url"]` 算出 base_url 去起 A2A client——
google-adk 用 `RemoteA2aAgent`（框架內建，等同於 `a2a_utility.client.call_agent` 的 ADK 版本），deepagents
用 [`a2a_utility.client.call_agent`](../client/agent_client.py) 直接呼叫。**呼叫本身不透過 discovery 節點**，
discovery 節點只負責回答「有誰、在哪」。

### 6.4 直接測 discovery 節點

```bash
# 手動登記一個假的
curl -s -X POST http://127.0.0.1:8090/register \
  -H "Content-Type: application/json" \
  -d '{"name":"demo_agent","description":"a demo","agent_card_url":"http://127.0.0.1:9999/.well-known/agent-card.json"}'

# 查全部（plain REST，跟舊版 registry/server.py 一樣）
curl -s http://127.0.0.1:8090/agents | python3 -m json.tool

# 依查詢字詞排序找誰能做某件事（真的 A2A SendMessage，不是 plain REST）
curl -s -X POST http://127.0.0.1:8090/a2a/v1/discovery \
  -H "Content-Type: application/json" -H "A2A-Version: 1.0" \
  -d '{"jsonrpc":"2.0","id":"1","method":"SendMessage",
       "params":{"message":{"messageId":"m1","role":"ROLE_USER","parts":[{"text":"weather"}]}}}'
```

## 7. 環境變數（`config.py`）

`A2ASettings`（前綴 `A2A_`，只有 `main.py` 這個獨立節點會讀，`serve_as_a2a()` 的參數是直接傳的，不吃這些）：

| 變數 | 預設 | 說明 |
|---|---|---|
| `A2A_SERVER_MODE` | `AGENT` | `AGENT` 或 `DISCOVERY` |
| `A2A_AGENT_NAME` | `generic_agent` | 這個節點自己的 agent name |
| `A2A_AGENT_DESCRIPTION` | `A generic A2A agent.` | — |
| `A2A_HOST` | `127.0.0.1` | — |
| `A2A_PORT` | `9000` | — |
| `A2A_REGISTRY_URL` | 無 | AGENT 模式下若設定，會自我登記到這個 registry |

`LLMSettings`（前綴 `LLM_`，**只有 `main.py` 內建的 AGENT 示範模式**會讀，走 `HandlerLLMAdapter` 的 4 個
domain agent完全不受影響）：`LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`、`LLM_SYSTEM_PROMPT`。

## 8. 已知限制／待改善

- ~~沒有 `pyproject.toml`~~ **已修復**：整個套件現在是獨立 repo `a2a_utility`（[../../README.md](../../README.md)），
  有 `pyproject.toml`，`pip install -e .[server,client]` 就能裝，不用 sys.path hack；以 git submodule 的
  形式被 `multi_agent_a2a` 這類消費端 repo 引用。
- ~~`DiscoveryAgentExecutor` 是孤兒程式碼~~ **已修復**：`main.py:run_discovery_server()` 現在用
  `DefaultRequestHandler(agent_executor=DiscoveryAgentExecutor(search_use_case), ...)` +
  `create_jsonrpc_routes(...)` 掛在 `/` 跟 `/a2a/v1/discovery` 兩個路徑，查詢真的走 A2A JSON-RPC
  （`SendMessage`/`SendStreamingMessage`）。`/register`、`/agents` 保留原本的 plain REST（登記是結構化資料，
  本來就不適合套 A2A 的 Task/Message 形狀，見 `main.py` 開頭的 docstring）。
- ~~Discovery client 端沒有共用模組~~ **已修復**：新增了 [`a2a_utility.client`](../client/)，
  `DiscoveryClient`／`call_agent` 讓任何 coordinator 不用再各自用 `httpx` 重寫一次查目錄／呼叫的邏輯。
- **`search()`（A2A endpoint／`DiscoveryClient.search()`）目前沒有真正的呼叫端**：`rank_agents()` 已經寫好、
  也已經能透過 `DiscoveryClient.search(query)` 呼叫，但兩個 coordinator 都還是用 `list_agents()`（列全部）
  自己在 prompt 裡讓 LLM 選，沒有人在用這個排序能力。agent 一多的時候，值得把 `list_agents` 工具改成先用
  `search(query)` 縮小候選清單再列出來。
- **DISCOVERY 節點是手刻 Starlette route + 手動 `await request.json()` 解析**，沒有像舊版
  `registry/server.py`（FastAPI + pydantic `RegisterRequest`）那樣的自動請求驗證跟 `/docs`——`/register`
  丟錯欄位只會得到一個手寫的 400，不是 FastAPI 那種自動生成的詳細錯誤訊息。
- **沒有任何自動化測試**：六角形架構最大的賣點就是「用假的 adapter 讓 use case／domain 可以脫離真實網路
  單獨測」，但目前一個測試檔都沒有——`ChatUseCase`、`SearchAgentUseCase`、`rank_agents()` 都是可以輕鬆單元
  測試的候選，架構的潛力還沒兌現成實際覆蓋率。
- **心跳／TTL 目前是寫死的模組常數**（`server.py` 的 `REGISTRY_HEARTBEAT_SECONDS = 5.0`、`main.py` 的
  `REGISTRY_TTL_SECONDS = 15.0`），沒有併入 `A2ASettings` 變成可用環境變數調整。
- **Registry 是單一行程、純記憶體**：這對 demo 完全足夠，但沒有持久化、沒有多副本/高可用的故事——如果之後
  真的要給多個團隊共用，這是第一個要處理的擴充點。
