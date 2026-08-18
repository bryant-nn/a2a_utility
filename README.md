# a2a_utility

公司內部共用的 A2A 協定 library。**你不需要碰任何 `a2a.*` 的東西** —— 型別、驗證、task 狀態、串流全部從
這個 wrapper 拿；原生 a2a-sdk 是這個套件的相依，不是你程式碼的相依。

```
a2a_utility/
├── schema/     雙邊共用的型別契約（ExtendedPart／ExtendedMessage／ExtendedTaskState…）
├── server/     開 A2A 端點（AGENT 節點 / DISCOVERY 節點）
└── client/     呼叫 A2A 端點（ExtendedAgentClient / call_agent / DiscoveryClient）
```

`server` 與 `client` 互不依賴，各自只依賴 `schema` —— 只當 domain agent 作者的人不會被拖進 client 的相依，
反之亦然。

設計理由、對原生 a2a-sdk 的追蹤紀錄，見 [`docs/DESIGN.md`](docs/DESIGN.md)。

---

## 安裝

```bash
pip install -e /path/to/a2a_utility[server]        # 寫 domain agent
pip install -e /path/to/a2a_utility[client]        # 寫 coordinator
pip install -e /path/to/a2a_utility[server,client] # 兩者都要

pip install -e /path/to/a2a_utility[server,auth]   # 要用內建 JWT 驗證（PyJWT）
pip install -e /path/to/a2a_utility[server,fastapi] # 要掛進既有 FastAPI 服務
```

---

## 快速上手

### 寫一個 domain agent

一個 async function 就是一個 agent：

```python
from a2a_utility.server import (
    ExtendedAgentCard, ExtendedAgentSkill, ExtendedEventQueue,
    ExtendedRequestContext, ExtendedTaskUpdater, serve_as_a2a,
)
from a2a_utility.schema import ExtendedPart, as_thinking_emitter


async def handle(context: ExtendedRequestContext, event_queue: ExtendedEventQueue) -> None:
    task_updater = ExtendedTaskUpdater(context, event_queue)
    await task_updater.start_work()

    emit = as_thinking_emitter(task_updater.as_part_emitter())   # 串流思考過程
    answer = await my_business_logic(context.get_user_input(), emit)

    await task_updater.add_artifact([ExtendedPart.from_text(answer)])
    await task_updater.complete()


serve_as_a2a(
    handler=handle,
    card=ExtendedAgentCard(
        name="joke_agent",
        description="說笑話",
        port=9050,                                   # host 預設 127.0.0.1
        skills=[ExtendedAgentSkill(id="joke", name="Joke", description="說一個笑話")],
    ),
    registry_url="http://127.0.0.1:8090",            # 給了就自動註冊 + heartbeat
)
```

`handle()` 的形狀是原生 `AgentExecutor.execute(context, event_queue) -> None` 的逐參數鏡射。沒有回傳值 ——
**task 的結局由你呼叫哪個方法決定**，跟原生一樣，連 `start_work()` 都要自己送。

想讓業務邏輯完全不碰 A2A，就照這個 repo 的慣例：`agent.py` 放純 `async def handle(text, emit) -> str`，
`server.py` 只做上面那層薄轉接。

### 寫一個 coordinator

```python
from a2a_utility.client import ExtendedAgentClient, DiscoveryClient

directory = DiscoveryClient("http://127.0.0.1:8090")
entry = await directory.resolve("joke_agent")
base_url = directory.agent_base_url(entry["agent_card_url"])

# 呼叫同一個 agent 多次 → 用 ExtendedAgentClient（重用連線與 agent card）
async with ExtendedAgentClient(base_url) as agent:
    answer = await agent.send("講個工程師的笑話", emit=my_emit)
    result = await agent.send_result("再一個")        # 要完整 typed 結果
    task = await agent.get_task(result.task_id)      # 事後查詢
```

只呼叫一次的腳本用 `call_agent(base_url, text)` 就好 —— 但它每次都會重開連線並重抓一次 agent card，
迴圈裡不要用。

---

## Task 狀態怎麼決定

`ExtendedTaskUpdater` 是原生 `TaskUpdater` 的真子類別，方法一一對應，但**參數全部是 a2a_utility 的型別**：

| 方法 | 結果 | 什麼時候用 |
|---|---|---|
| `start_work()` | WORKING | 開始做事 |
| `add_artifact([...])` | — | 產出答案，可呼叫多次（串流分段用 `append`/`last_chunk`） |
| `complete()` | COMPLETED（終態） | 成功 |
| `failed()` | FAILED（終態） | 壞掉了 |
| `reject()` | REJECTED（終態） | 拒絕服務（權限不足） |
| `cancel()` | CANCELED（終態） | 放棄 |
| `requires_input()` | INPUT_REQUIRED（暫停） | 需要使用者補資料 |
| `requires_auth()` | AUTH_REQUIRED（暫停） | 需要憑證 |

所有 `message=` 參數都吃四種形狀，**不用自己建 message 物件**：

```python
await task_updater.complete("done")                              # str
await task_updater.complete(ExtendedPart.from_text("done"))      # 一個 part
await task_updater.complete([ExtendedPart.thinking("...")])      # 一串 parts
await task_updater.complete(task_updater.new_agent_message([...]))  # ExtendedMessage
```

暫停之後框架會用**一次全新的 `execute()`**（同一個 task_id，不是接續舊 coroutine）把後續回答帶進來。
用 `context.is_resuming` 判斷，用 `context.current_task` 讀先前的狀態與歷史。

**但這件事需要 caller 配合**：續傳靠的是 caller 把下一次請求的 `task_id` 設成跟上次一樣，server 才會
去 `task_store` 撈出舊 task、填進 `context.current_task`。`send`/`send_result`/`send_parts`/
`call_agent*` 都吃 `task_id=`，沒給就一定是全新 task：

```python
result = await agent.send_result("book a flight")
if result.status == ExtendedTaskState.INPUT_REQUIRED:
    result = await agent.send_result("thursday", task_id=result.task_id)  # 續傳同一個 task
```

---

## 資料型別

| 型別 | 用途 |
|---|---|
| `ExtendedPart` | 一段內容。`from_text()` / `thinking()` / `source_reference()` / `file()` |
| `ExtendedMessage` | 一則訊息（雙向轉換，可當輸入也可當輸出） |
| `ExtendedTask` | 一個 task 的唯讀快照（`context.current_task`） |
| `ExtendedTaskState` | task 狀態的 str enum（`"completed"`，不是原生的整數） |
| `A2ATaskResult` | client 端拿回來的完整結果 |

`ExtendedPart` 的 `text`/`raw`/`url`/`data` 對應原生 `Part` proto 的同一個 `oneof`，同時只能設一個 ——
原生 protobuf 設兩個不會報錯、只默默留最後一個，`ExtendedPart` 直接擋下來。

自訂結構化資料放 `data`（`CustomizedData` envelope，仿 Vercel AI SDK 的 data-part 協定），目前有
`thinking_response` / `source_reference_response` 兩種。

---

## 上線相關

`create_app()` / `serve_as_a2a()` **刻意不收任何 production 注入參數**，只有 `mode` / `agent_card` /
`handler` / `on_cancel` / `registry_url` / `registration_payload` / `rpc_url`。需要更多的時候：

**掛 middleware、加 route** —— `create_app()` 回傳的就是一個普通 Starlette app，用 Starlette 自己的 API：

```python
app = create_app(agent_card=card, handler=handle)
app.add_middleware(MyMiddleware)
app.router.routes.append(Route("/health", health))
serve(app, host=card.host, port=card.port)
```

（`serve_as_a2a()` 是「建 app + 跑起來」一步到位，中間沒有插手的機會 —— 要動 app 就拆成
`create_app()` + `serve()` 兩步。）

**durable task store、push notification、REST binding** —— 用公開的 `AgentExecutor` 自己組。這是刻意的
取捨：`a2a_utility` 只保證最短路徑，複雜部署直接對原生：

```python
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a_utility.server import AgentExecutor, A2AUtilityCallContextBuilder

proto_card = card.to_agent_card()
request_handler = DefaultRequestHandler(
    agent_executor=AgentExecutor(handler=handle),   # ← handler 合約完全不變
    task_store=DatabaseTaskStore(...),
    agent_card=proto_card,
    push_config_store=..., push_sender=...,
)
routes = [*create_agent_card_routes(proto_card),
          *create_jsonrpc_routes(request_handler, "/",
                                 context_builder=A2AUtilityCallContextBuilder())]
app = Starlette(routes=routes)   # lifespan 記得接 request_handler.aclose()
```

domain agent 那支 `handle()` 一個字都不用改 —— 換掉的只有組 app 這層。

---

## DISCOVERY 節點

```bash
A2A_SERVER_MODE=DISCOVERY A2A_PORT=8090 python -m a2a_utility.server.main
```

或 `create_app(mode=ServerMode.DISCOVERY, agent_card=card)`。掛 `POST /register`、`GET /agents`
（plain REST），以及真正的 A2A JSON-RPC 搜尋端點。domain agent 端不用手動註冊 ——
`serve_as_a2a(registry_url=...)` 會自己 heartbeat，斷線的會在 TTL 後從目錄消失。

---

## 環境變數（前綴 `A2A_`）

| 變數 | 預設 | 說明 |
|---|---|---|
| `A2A_SERVER_MODE` | `AGENT` | `AGENT` 或 `DISCOVERY` |
| `A2A_AGENT_NAME` / `A2A_AGENT_DESCRIPTION` | — | 節點自己的身分 |
| `A2A_HOST` / `A2A_PORT` | `127.0.0.1` / `9000` | — |
| `A2A_REGISTRY_URL` | 無 | 設了就自我註冊 |
| `A2A_REGISTRY_HEARTBEAT_SECONDS` / `A2A_REGISTRY_TTL_SECONDS` | `5` / `15` | TTL 要明顯大於 heartbeat |

只有 `python -m a2a_utility.server.main` 這個獨立節點會自動讀這些；`serve_as_a2a()` 的參數是直接傳的。

---

## 測試

```bash
pip install -e .[server,client,test]
pytest
```

- `tests/e2e/` —— 完整堆疊（真 Starlette app + 真 client，走 ASGI），task 生命週期、message mode、
  client 端憑證附加、關機排空。
- `tests/server/`、`tests/schema/` —— 各 adapter 的單元測試。
- `tests/test_sdk_contract.py` —— **釘住我們依賴的 a2a-sdk 表面**。升版 SDK 時這支先炸，而不是 production
  先炸。

---

## 已知限制

- **`create_app()` 的 task store 寫死 `InMemoryTaskStore`**：重啟就掉、不能多副本共享。要 durable 的
  就照「上線相關」那段自己組 app。
- **Registry 是單一行程、純記憶體**：沒有持久化、沒有多副本。要給多團隊共用的話這是第一個要處理的。
- **DISCOVERY 的 `/register` 是手刻解析**，錯誤訊息只有一個手寫的 400。
- **`DiscoveryClient.search()` 沒有實際呼叫端**：`rank_agents()` 已經可用，但兩個 coordinator demo
  還是 `list_agents()` 列全部讓 LLM 自己選。
- **例外安全網不是 100% 乾淨**：handler 自己送過終態後才拋例外的話，安全網那個 throwaway instance
  不知道，會多送一則狀態（無害但多餘）。

---

## 這是 git submodule

這個目錄是獨立的 git repository，在消費端 repo（`multi_agent_a2a`）裡以 submodule 接入。改動要在
**這個 repo** 裡 commit，消費端再 `git submodule update --remote` 推進 gitlink。
