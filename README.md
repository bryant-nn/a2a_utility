# a2a_utility

公司內部共用的 A2A 協定 library。獨立成自己的 git repo（以 git submodule 的形式被消費端 repo——目前是
`multi_agent_a2a`——引用），目的是讓任何「有自己的 root agent + domain agents」的專案都能共用同一份
A2A server/client 實作，不用每個專案各自複製貼上一份樣板碼。

三個子套件，各自獨立、依賴方向單向：

```
a2a_utility/
├── schema/     雙邊共用的型別契約（ExtendedPart／HandlerResult／A2ATaskResult…）— 見下方「schema」
├── server/     開 A2A 端點（AGENT 節點 / DISCOVERY 節點），六角形分層 — 見 §架構
└── client/     呼叫 A2A 端點（call_agent / DiscoveryClient）
```

`server`／`client` 互不依賴，各自只依賴 `schema`——這樣一個只當 domain agent 作者的人 `pip install
-e .[server]` 不會拖進 client 端用不到的東西，反之亦然。

## 安裝

還沒發到任何套件索引（內部或公開）——目前是本機 editable install，或作為 git submodule 接進消費端 repo
之後在該環境裡裝：

```bash
# 只要當 server（domain agent 作者）
pip install -e /path/to/a2a_utility[server]

# 只要當 client（coordinator 作者）
pip install -e /path/to/a2a_utility[client]

# 兩者都要（例如同一個環境裡同時跑 domain agent 又跑 coordinator，本 demo 專案就是這樣）
pip install -e /path/to/a2a_utility[server,client]
```

裝完之後，任何地方都能直接 `import a2a_utility.server` / `import a2a_utility.client` /
`import a2a_utility.schema`，不需要 `sys.path.insert(...)` 這種 hack。

## 快速上手

### 寫一個 domain agent（server 角色）

一個 domain agent 只要寫**一個 async function（或一個帶 `__call__` 的物件）**，完全不 import 任何
`a2a.*`，也不用繼承任何 `a2a_utility` 提供的類別：

```python
from a2a_utility.server import ExtendedRequestContext, HandlerCompleted, HandlerResult, PartEmitter, serve_as_a2a
from a2a_utility.schema import ExtendedPart, as_thinking_emitter

async def handle_business_logic(text: str, emit_thought) -> str:
    if emit_thought:
        await emit_thought("思考中...")
    return f"你問的是：{text}"

async def handle(context: ExtendedRequestContext, emit: PartEmitter) -> HandlerResult:
    answer = await handle_business_logic(context.get_user_input(), as_thinking_emitter(emit))
    return HandlerCompleted(parts=[ExtendedPart.from_text(answer)])

serve_as_a2a(
    name="joke_agent", description="...", skill_id="joke",
    skill_name="Joke", skill_description="...", examples=["..."],
    handler=handle, port=9050,
    registry_url="http://127.0.0.1:8090",  # 給了就自動註冊 + heartbeat
)
```

想保有「業務邏輯完全不碰 A2A/pydantic」的分層，可以照這個 repo 的四個 domain agent 的慣例：業務邏輯
單獨寫在 `agent.py`（純 `async def handle(text, emit_thought=None) -> str`），`server.py` 只負責上面
那層薄薄的轉接（`handle_business_logic` ↔ `handle`）。完整範例、`AgentHandlerPort`／`HandlerResult`
細節，見 [`a2a_utility/server/README.md`](a2a_utility/server/README.md)。

### 啟動一個 DISCOVERY 節點（服務發現，兩種方式都行）

```bash
# CLI（適合獨立行程／container）
A2A_SERVER_MODE=DISCOVERY A2A_PORT=8090 python -m a2a_utility.server.main
```

```python
# 函式呼叫（適合內嵌在別的行程/測試/腳本裡，不用另開 subprocess）
from a2a_utility.server import A2ASettings, ServerMode, run_discovery_server

run_discovery_server(A2ASettings(server_mode=ServerMode.DISCOVERY, port=8090))
```

```python
# 或直接用 create_app(mode=...) 自己組（AGENT／DISCOVERY 現在是同一個函式，同一個 mode 參數決定）
from a2a_utility.server import ServerMode, create_app, serve

app = create_app(mode=ServerMode.DISCOVERY, agent_card=my_card)
serve(app, host="127.0.0.1", port=8090)
```

### 寫一個 coordinator（client 角色）

```python
from a2a_utility.client import DiscoveryClient, call_agent, call_agent_result

directory = DiscoveryClient("http://127.0.0.1:8090")
agents = await directory.list_agents()          # [{"name", "description"}, ...]
entry = await directory.resolve("joke_agent")     # {"description", "agent_card_url"}
base_url = directory.agent_base_url(entry["agent_card_url"])

# 只要串接好的最終文字：
answer = await call_agent(base_url, "跟工程師有關的笑話", emit=my_emit)

# 要完整拿到 domain agent 回傳的 typed parts（thinking／source_reference／file，不只文字）：
result = await call_agent_result(base_url, "跟工程師有關的笑話", emit=my_emit)
answer = result.text()
for part in result.parts():
    if part.data and part.data.data_type == "source_reference_response":
        ...  # 處理來源參考
```

`my_emit` 是 `PartEmitter`（`Callable[[ExtendedPart], Awaitable[None]]`）——串流過來的每一個 part（不只
純文字，thinking／source_reference／file 都可能）都會即時呼叫它一次，跟 server 端 handler 收到的
callback 是同一個型別、同一套語彙。

## 架構

### `schema`：雙邊共用的型別契約

```
a2a_utility/schema/
  parts.py          ExtendedPart, CustomizedData, VercelThinkingResponse,
                     SourceReferenceResponse, PartEmitter, as_thinking_emitter()
  task_result.py     A2ATaskResult（client 專用的「完整結果」讀取型別）
```

- **`ExtendedPart`**：原生 a2a `Part` proto（`text`/`raw`/`url`/`data`/`metadata`/`filename`/`media_type`，
  其中 `text`/`raw`/`url`/`data` 是同一個 `oneof "content"`，同時只能設一個——`ExtendedPart` 有
  `model_validator` 強制檢查這件事）的 typed 鏡像，`.from_protobuf()`/`.to_protobuf()` 是唯一的轉換
  邊界。用 `.from_text(text)`／`.thinking(text)`／`.source_reference(refs)`／`.file(url=...)` 這幾個
  ergonomic constructor 建立，不用手刻 protobuf。
- **`CustomizedData`**：`ExtendedPart.data` 裡放的巢狀 envelope（`{data_type, data_content}`，仿 Vercel
  AI SDK 的 data-part 協定），目前有 `thinking_response`／`source_reference_response` 兩種
  `data_content`，用 pydantic discriminated union（`Literal` tag + `Field(discriminator=...)`）表達。
- **`PartEmitter`**：`Callable[[ExtendedPart], Awaitable[None]]`，server 端 handler 收到的即時串流
  callback、client 端 `call_agent*(..., emit=...)` 收到的即時回呼，是同一個型別——串流跟最終回傳用的
  是同一套語彙，不是兩套規則。
- **`A2ATaskResult`**：**只有 client 端在用**的「完整結果」讀取型別（`task_id`/`status`/`artifacts`/
  `history`）——刻意跟 server 端 handler 要回傳的型別（見下方 `HandlerResult`）分開，因為 task_id/
  status/history 對「正在產生答案的 agent」來說是填不出來、也沒人會讀的死欄位（真正的 task_id 已經
  綁在 server 端自己建的 `TaskUpdater` 上；status 由 handler 回傳哪個 `HandlerResult` 變體決定；
  history 是輸入端的東西，不是單次回覆會產生的）。

### `server`：六角形分層（Ports & Adapters）

```
a2a_utility/server/
  domain/
    models/
      agent_card.py      AgentDescriptor（discovery 用的最小描述）
      principal.py        Principal（純值物件）+ read_principal/write_principal（純函式操作 dict）
    services/
      discovery_service.py  rank_agents()：純函式，query 字詞比對 name/description 評分排序
  application/
    dtos.py                ExtendedRequestContext（handler 的輸入）+ HandlerResult 系列（handler 的輸出）
    ports/
      inbound/
        agent_handler_port.py    AgentHandlerPort（domain agent 實作的合約）
        on_cancel_port.py        OnCancelPort（可選，見下方「取消」）
        discovery_use_case_port.py
      outbound/
        registry_port.py         AgentRegistryPort
    use_cases/
      register_agent_card_use_case.py, search_agent_use_case.py
  adapters/
    inbound/
      agent_executor.py           AgentExecutor：橋接原生 a2a execute()/cancel() 到 AgentHandlerPort/OnCancelPort
      call_context_builder.py     A2AUtilityCallContextBuilder：把 Principal 寫進每個 request 的 call_context.state
      discovery_agent_executor.py DISCOVERY 模式：A2A Task ↔ DiscoveryUseCasePort
    outbound/
      event_queue_adapter.py      ExtendedEventQueue：包住原生 TaskUpdater 的 emit()/complete()/failed()/...
      in_memory_registry_adapter.py  記憶體 + TTL 實作 AgentRegistryPort
  app.py       build_agent_card / create_app(mode=) / serve / serve_as_a2a — composition root
  config.py    A2ASettings（pydantic-settings，A2A_ 前綴）
  main.py      獨立可執行節點：依 A2A_SERVER_MODE 決定 AGENT 示範節點還是 DISCOVERY 節點
```

**依賴方向永遠朝內**：`adapters` 依賴 `application` 的 port，`application` 依賴 `domain`，`domain`
不依賴任何人（`principal.py` 甚至刻意不 import 任何 `a2a.*` 型別，只操作純 `dict`）。`app.py`／
`main.py` 是 composition root，唯一被允許把所有層兜在一起的地方。

完整分層說明、每個檔案的角色、一次請求怎麼流過這些層，見
[`a2a_utility/server/README.md`](a2a_utility/server/README.md)。

### Domain agent 怎麼被接進來：注入，不是繼承

`AgentHandlerPort`（`application/ports/inbound/agent_handler_port.py`）是一個**單純的 `Callable`
型別**，不是一個 class-based Protocol：

```python
AgentHandlerPort = Callable[[ExtendedRequestContext, PartEmitter], Awaitable[HandlerResult]]
```

一個普通 async function（無狀態 agent）或一個帶 `__call__` 的物件（想保留自己狀態的 agent，例如包住
一個持久連線/agent loop 實例）都滿足這個型別，兩種完全等價，`a2a_utility` 不在乎背後是哪一種。

`AgentExecutor`（`adapters/inbound/agent_executor.py`）**是真正、原封不動的原生
`a2a.server.agent_execution.AgentExecutor` 子類別**（不是重新發明的東西），用建構子注入你的 handler：

```python
class AgentExecutor(_NativeAgentExecutor):
    def __init__(self, handler: AgentHandlerPort, on_cancel: Optional[OnCancelPort] = None) -> None:
        self._handler = handler
        ...
```

`serve_as_a2a(handler=..., ...)`／`create_app(handler=..., ...)` 在內部組出這個 `AgentExecutor`——
domain agent 的程式碼從頭到尾不用 import `AgentExecutor`，也不用碰任何 `a2a.*` 型別，跟 discovery
模式的 `DiscoveryAgentExecutor` 用建構子注入 `DiscoveryUseCasePort`（不是每個 discovery 節點各自寫一個
子類別）是同一種模式。

### Task 狀態由 domain agent 自己決定：`HandlerResult`

`handle()` 的回傳型別不是一個裸的 `list[ExtendedPart]`，而是一個 pydantic discriminated union（
跟 `CustomizedData` 同一種手法）：

```python
class HandlerCompleted(BaseModel):
    status: Literal["completed"] = "completed"
    parts: list[ExtendedPart]

class HandlerFailed(BaseModel):
    status: Literal["failed"] = "failed"
    message: str

class HandlerInputRequired(BaseModel):
    status: Literal["input_required"] = "input_required"
    message: str

class HandlerAuthRequired(BaseModel):
    status: Literal["auth_required"] = "auth_required"
    message: str

class HandlerCanceled(BaseModel):
    status: Literal["canceled"] = "canceled"
    message: Optional[str] = None

HandlerResult = Annotated[Union[HandlerCompleted, HandlerFailed, HandlerInputRequired,
                                  HandlerAuthRequired, HandlerCanceled], Field(discriminator="status")]
```

`AgentExecutor.execute()` **沒有自己的決策邏輯**——純粹照 `handle()` 回傳的變體型別，`isinstance` 分派
給 `ExtendedEventQueue` 對應的方法（`complete`/`failed`/`requires_input`/`requires_auth`/`cancel`）。
這是刻意的設計選擇：曾經考慮過用 exception（`raise InputRequired(...)`）表達，但最終選了「回傳值就是
宣告」的做法——task 停在哪個狀態是 handler 的一個明確的、有型別檢查的回傳，不是隱含在 try/except
控制流裡。

真的沒預期到的 Python exception（bug、網路錯誤等）還是會被 `execute()` 的 `try/except Exception` 接住
轉成 `FAILED`——這跟 `HandlerFailed`（agent 自己判斷做不到、給使用者一個清楚訊息）是兩回事，不衝突。

`ExtendedRequestContext.is_resuming`（bool）：`HandlerInputRequired`/`HandlerAuthRequired` 之後，
框架會用**新的一次 `execute()` 呼叫**（同一個 task_id，不是恢復舊的 coroutine）把使用者的後續回答帶
進來——`is_resuming` 讓 handler 判斷「這次是不是接續之前被卡住的任務」，不用自己 import
`a2a.types.TaskState` 去比對 `context.current_task.status.state`。

### 即時串流：`emit` 帶的是完整的 `ExtendedPart`，不只是文字

```python
async def handle(context, emit: PartEmitter) -> HandlerResult:
    await emit(ExtendedPart.thinking("查詢中..."))
    await emit(ExtendedPart.source_reference([...]))   # 執行到一半也能先丟出來，不用等到最後
    ...
    return HandlerCompleted(parts=[...])
```

`emit` 是 `ExtendedEventQueue.emit`（`adapters/outbound/event_queue_adapter.py`）綁定方法，內部把
`part.to_protobuf()` 包進一個 `TASK_STATE_WORKING` 的狀態訊息送出去——跟 `HandlerCompleted.parts`
用的是完全同一套 `ExtendedPart` 語彙，差別只在「現在就丟出去」還是「跑完一起回傳」。業務邏輯如果只想
串純文字進度（大多數情況），用 `as_thinking_emitter(emit)` 轉成 `Callable[[str], Awaitable[None]]`
即可，一行接上 nanobot 的 `on_progress=`／deepagents 的 `emit_thought` 閉包這類既有的字串型 callback。

### 取消：agent 自己決定 vs. 外部要求

兩種「取消」，方向完全不同：

- **agent 自己決定放棄**：`handle()` 回傳 `HandlerCanceled(message=...)`，跟 `HandlerFailed` 走一樣的
  回傳值路徑。
- **外部主動要求取消**（client 端送 cancel RPC）：框架呼叫原生 `AgentExecutor.cancel(context,
  event_queue)`——這是完全不同方向的控制流（框架 → agent），也是為什麼它是一個獨立、可選的第二個注入
  `on_cancel: OnCancelPort`，沒有併進 `AgentHandlerPort`：

  ```python
  OnCancelPort = Callable[[ExtendedRequestContext], Awaitable[CancelResult]]
  ```

  不給 `on_cancel` 的話，`AgentExecutor.cancel()` 有合理的預設行為（標記 `CANCELED`，不帶自訂訊息）。
  真正的資源清理（關連線等）不需要透過這個 port——框架取消 `execute()` 背後的 asyncio task 時，
  handler 自己的 `try/finally` 就會自然執行，這個 port 純粹是給「想在取消時留一句自訂訊息」的情況用。

### 資料要放哪：三層擴充點

| 層 | 位置 | 上線可見？ | 用途 |
|---|---|---|---|
| 1 | `Part.data`（`oneof content`）——`CustomizedData` envelope | 是 | 結構化的**答案內容**（thinking、來源參考）——真正的業務資料 |
| 2 | `Part.metadata`（`Struct`，在 `oneof` 之外） | 是，但可忽略 | 不屬於主要內容、附加在某個 part 上的旗標（目前沒實際用到） |
| 3 | `ServerCallContext.state`（`A2AUtilityCallContextBuilder` → `Principal`） | **否**（純 server-side） | 認證/租戶/session 這類擴充資訊，絕對不能序列化上 A2A wire |

這是對照真正的 a2a-sdk 原始碼（`Part` proto 的 `oneof`/`Struct` 定義、`DefaultServerCallContextBuilder`
的擴充點）確認過的，不是憑印象猜的——`Part.data`/`Part.metadata` 對不認識這些自訂型別的第三方 client
來說完全是不透明、安全忽略的資料，`ServerCallContext.state` 則從設計上就不會碰到 wire。

### DISCOVERY 模式：`create_app(mode=)` 統一組裝

AGENT／DISCOVERY 現在是**同一個 `create_app()`／`serve_as_a2a()`**，用 `mode: ServerMode` 參數決定：

```python
create_app(mode=ServerMode.AGENT, agent_card=..., handler=...)      # domain agent 用
create_app(mode=ServerMode.DISCOVERY, agent_card=...)                # registry 節點用，handler 不需要
```

`mode=DISCOVERY` 時，`create_app` 內部建 `InMemoryRegistryAdapter` + `RegisterAgentCardUseCase`/
`SearchAgentUseCase` + `DiscoveryAgentExecutor`，掛：

- `POST /register`、`GET /agents`（plain REST——登記是結構化資料，本來就不適合套 A2A 的 Task/Message
  形狀）
- `POST /`（或自訂 `rpc_url`）與 `POST /a2a/v1/discovery`（真的 A2A JSON-RPC，走
  `DiscoveryAgentExecutor` → `SearchAgentUseCase.search()` → `rank_agents()`，query 字詞對 name/
  description 評分排序，空字串查詢等同 `list_all()`）

domain agent 端完全不用手動呼叫 `/register`——`serve_as_a2a(..., registry_url=...)` 用 Starlette
`lifespan` 開一個背景 task，啟動時登記、之後每 `REGISTRY_HEARTBEAT_SECONDS`（5 秒）重送一次；
`InMemoryRegistryAdapter` 只回傳 `ttl_seconds`（預設 15 秒）內有心跳的 agent，斷線的會自動從
`GET /agents` 消失。

## `client`

```
a2a_utility/client/
  agent_client.py       call_agent()/call_agent_parts()/call_agent_result() — 送訊息、串流回收
  discovery_client.py    DiscoveryClient — list_agents()/resolve()/search()，包住 /agents、/a2a/v1/discovery
```

- `call_agent_result(base_url, text, emit=...)` 回傳完整 `A2ATaskResult`；`call_agent_parts()` 只要
  `list[ExtendedPart]`；`call_agent()` 只要串接好的最終文字——三個都建立在同一支 SDK 原生 client
  （`create_client(base_url)` → `Client.send_message(...)` 收 `StreamResponse`）上，`emit` 在收到每個
  `TASK_STATE_WORKING` 狀態訊息時，把訊息裡**每一個** part（不再只挑文字的）轉成 `ExtendedPart` 即時
  呼叫。
- `DiscoveryClient` 快取一份目錄，`list_agents()`/`resolve(name)` 查 `GET /agents`；`search(query)`
  改走真正的 A2A JSON-RPC（給 agent 多到需要排序時用，兩個 coordinator demo 目前都還是用
  `list_agents()` 列全部讓 LLM 自己選）。

## 環境變數（`server/config.py`）

`A2ASettings`（前綴 `A2A_`，只有 `python -m a2a_utility.server.main` 這個獨立節點會讀，
`serve_as_a2a()` 的參數是直接傳的，不吃這些）：

| 變數 | 預設 | 說明 |
|---|---|---|
| `A2A_SERVER_MODE` | `AGENT` | `AGENT` 或 `DISCOVERY` |
| `A2A_AGENT_NAME` | `generic_agent` | 這個節點自己的 agent name |
| `A2A_AGENT_DESCRIPTION` | `A generic A2A agent.` | — |
| `A2A_HOST` | `127.0.0.1` | — |
| `A2A_PORT` | `9000` | — |
| `A2A_REGISTRY_URL` | 無 | AGENT 模式下若設定，會自我登記到這個 registry |

## 已知限制／待改善

- **`search()`（`DiscoveryClient.search()`）目前沒有真正的呼叫端**：`rank_agents()` 已經寫好、也已經
  能透過 `DiscoveryClient.search(query)` 呼叫，但兩個 coordinator demo 都還是用 `list_agents()`
  （列全部）自己在 prompt 裡讓 LLM 選，沒有人在用這個排序能力。agent 一多的時候，值得把
  `list_agents` 工具改成先用 `search(query)` 縮小候選清單再列出來。
- **DISCOVERY 節點是手刻 Starlette route + 手動 `await request.json()` 解析**，`/register` 丟錯欄位
  只會得到一個手寫的 400，沒有自動生成的詳細錯誤訊息。
- **沒有任何自動化測試**：六角形架構最大的賣點就是「用假的 adapter 讓 use case／domain 可以脫離真實
  網路單獨測」，但目前一個測試檔都沒有——`rank_agents()`、`HandlerResult` 的 discriminator round-trip、
  `AgentExecutor.execute()` 對五種 `HandlerResult` 變體的分派邏輯都是可以輕鬆單元測試的候選（後者已經
  用一次性腳本手動驗證過，還沒收進正式測試檔）。
- **心跳／TTL 目前是寫死的模組常數**（`app.py` 的 `REGISTRY_HEARTBEAT_SECONDS = 5.0`／
  `REGISTRY_TTL_SECONDS = 15.0`），沒有併入 `A2ASettings` 變成可用環境變數調整。
- **Registry 是單一行程、純記憶體**：這對 demo 完全足夠，但沒有持久化、沒有多副本/高可用的故事——如果
  之後真的要給多個團隊共用，這是第一個要處理的擴充點。
- **`Part.metadata`（三層擴充點的第 2 層）目前沒有實際使用者**：機制留著，但還沒有任何一個 domain
  agent 或 coordinator 真的往裡面塞東西。

## 這是 git submodule，不是這個 repo 自己的程式碼

這個目錄本身是一個獨立的 git repository。在消費端 repo（例如 `multi_agent_a2a`）裡，它是用

```bash
git submodule add <this-repo-url> a2a_utility
```

接進去的——消費端只保存一個指向特定 commit 的 gitlink，不是整份程式碼的複本。改動 A2A 協定層的邏輯要
在**這個 repo**裡改、commit、push，然後消費端 repo 再 `git submodule update --remote` 或
`cd a2a_utility && git pull` 把 gitlink 指標推進到新 commit。

目前這個 repo 還沒有推到任何 remote（先以本機 repo 存在），之後要接遠端／內部 GitLab／GitHub 的話：

```bash
git remote add origin <your-internal-remote-url>
git push -u origin main
```

在每個消費端 repo 裡，把 submodule 的 URL 從本機路徑改指到那個 remote：

```bash
git submodule set-url a2a_utility <your-internal-remote-url>
```
