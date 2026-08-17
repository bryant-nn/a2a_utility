# a2a_utility

公司內部共用的 A2A 協定 library。**你不需要碰任何 `a2a.*` 的東西** —— 型別、驗證、task 狀態、串流全部從
這個 wrapper 拿；原生 a2a-sdk 是這個套件的相依，不是你程式碼的相依。

```
a2a_utility/
├── schema/     雙邊共用的型別契約（ExtendedPart／ExtendedMessage／ExtendedTaskState…）
├── server/     開 A2A 端點（AGENT 節點 / DISCOVERY 節點）+ Gate Keeper 驗證
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

## 驗證與權限（Gate Keeper）

驗證由 library 統一處理，**agent 作者只需要做兩件事**：宣告要什麼、在用到 tool 的地方檢查。

### 1. 開啟 gate

```python
from a2a_utility.server import BearerAuth, GateKeeper, ExtendedAgentCard, serve_as_a2a
from a2a_utility.server import CachedPermissionService, HttpPermissionService
from a2a_utility.server.adapters.outbound.jwt_token_verifier import JwtTokenVerifier

gate = GateKeeper(
    JwtTokenVerifier(
        jwks_url="https://internal-idp/.well-known/jwks.json",
        issuer="https://internal-idp",
        audience="joke-agent",        # 這個服務自己的識別；不設的話別的服務的 token 也能進來
    ),
    CachedPermissionService(HttpPermissionService("http://permission-service")),
)

serve_as_a2a(
    handler=handle,
    card=ExtendedAgentCard(..., auth=BearerAuth()),   # ← 必填，見下方說明
    gate_keeper=gate,
    required_permission="agent:joke",                 # agent 層級權限
)
```

或全部走環境變數（`A2A_JWKS_URL` / `A2A_JWT_ISSUER` / `A2A_JWT_AUDIENCE` /
`A2A_PERMISSION_SERVICE_URL`）：

```python
settings = A2ASettings()
serve_as_a2a(..., gate_keeper=settings.build_gate_keeper(), require_auth=settings.require_auth)
```

> **card 上的 `auth=BearerAuth()` 不是裝飾**：原生 A2A client 是讀 agent card 上宣告的 security scheme
> 才決定要掛哪個 header。card 沒宣告，守規矩的 client 就什麼都不會送，然後你的 server 全部回 401。

### 2. Tool 層級權限在 handler 裡檢查

agent 層級的權限 gate 會在 handler 執行前擋掉；tool 層級沒辦法事先知道會用到哪些 tool，所以在用到的
地方檢查：

```python
async def handle(context, event_queue):
    task_updater = ExtendedTaskUpdater(context, event_queue)
    await task_updater.start_work()

    context.user.require("tool:send_email")     # 沒權限 → 丟 PermissionDenied
    ...
```

`PermissionDenied` 不用自己接 —— `AgentExecutor` 會把它變成 **REJECTED** task state（跟一般例外變成
FAILED 分開）。

### 失敗長什麼樣

| 情況 | Task state | client 端 |
|---|---|---|
| 沒帶 token / token 無效過期 | `AUTH_REQUIRED` | 正常回傳，理由在 `result.status_text` |
| 權限不足（agent 或 tool 層級） | `REJECTED` | 丟 `A2ACallError`，`.status` 是 REJECTED |
| handler 拋例外 | `FAILED` | 丟 `A2ACallError`，`.status` 是 FAILED |

AUTH_REQUIRED 不丟例外，因為那是「任務暫停等你補憑證」，不是「任務結束了」—— 重試有意義；REJECTED 是
「我知道你是誰，不行」，重試沒有意義。

### 3. Client 端帶憑證

```python
answer = await call_agent(url, question, credentials=token)
```

multi-agent 最常見的情境是 root agent 把使用者的 token 往下游傳：

```python
async def handle(context, event_queue):
    answer = await call_agent(downstream_url, question, credentials=context.user.token)
```

注意這代表**下游 agent 驗的是終端使用者，不是 root agent**——通常正是你要的，但要有意識。

### 沒設 gate 會怎樣

`create_app`/`serve_as_a2a` 會裝一個 `AllowAllGateKeeper` 並印 WARNING：所有請求放行，但
`context.user` 是未驗證狀態，所以 `context.user.require(...)` 仍然會拒絕。**部署環境請設
`A2A_REQUIRE_AUTH=true`** —— 沒給 gate 就直接啟動失敗，避免不小心把沒驗證的 server 送上線。

---

## 上線相關

```python
create_app(
    agent_card=card,
    handler=handle,
    gate_keeper=gate,
    task_store=DatabaseTaskStore(...),   # 預設 InMemoryTaskStore：重啟就掉、不能多副本共享
    push_config_store=...,               # push notification（兩個都要給才會生效）
    push_sender=...,
    middleware=[Middleware(GateMiddleware)],  # 可選：沒帶 token 的請求在建 task 前就擋掉
    extra_routes=[Route("/health", health)],
)
```

掛進既有 FastAPI 服務：

```python
from a2a_utility.server import add_to_fastapi

app = FastAPI()
add_to_fastapi(app, agent_card=card, handler=handle, gate_keeper=gate)
```

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
| `A2A_REQUIRE_AUTH` | `false` | **部署環境設 true** |
| `A2A_JWKS_URL` / `A2A_JWT_ISSUER` / `A2A_JWT_AUDIENCE` | 無 | JWT 驗證 |
| `A2A_PERMISSION_SERVICE_URL` / `A2A_PERMISSION_CACHE_TTL_SECONDS` | 無 / `30` | 權限查詢；TTL 同時是撤權延遲 |
| `A2A_REQUIRED_PERMISSION` | 無 | agent 層級權限 |

只有 `python -m a2a_utility.server.main` 這個獨立節點會自動讀這些；`serve_as_a2a()` 的參數是直接傳的。

---

## 測試

```bash
pip install -e .[server,client,test]
pytest
```

- `tests/e2e/` —— 完整堆疊（真 Starlette app + 真 client，走 ASGI），task 生命週期、message mode、
  gate 拒絕、憑證轉發、關機排空。
- `tests/server/`、`tests/schema/` —— 各 adapter 的單元測試。
- `tests/test_sdk_contract.py` —— **釘住我們依賴的 a2a-sdk 表面**。升版 SDK 時這支先炸，而不是 production
  先炸。

---

## 已知限制

- **Registry 是單一行程、純記憶體**：沒有持久化、沒有多副本。要給多團隊共用的話這是第一個要處理的。
- **DISCOVERY 的 `/register` 是手刻解析**，錯誤訊息只有一個手寫的 400。
- **`DiscoveryClient.search()` 沒有實際呼叫端**：`rank_agents()` 已經可用，但兩個 coordinator demo
  還是 `list_agents()` 列全部讓 LLM 自己選。
- **`CachedPermissionService` 的 TTL 同時是撤權延遲**：權限被收回後最多還會生效 TTL 這麼久。
- **例外安全網不是 100% 乾淨**：handler 自己送過終態後才拋例外的話，安全網那個 throwaway instance
  不知道，會多送一則狀態（無害但多餘）。

---

## 這是 git submodule

這個目錄是獨立的 git repository，在消費端 repo（`multi_agent_a2a`）裡以 submodule 接入。改動要在
**這個 repo** 裡 commit，消費端再 `git submodule update --remote` 推進 gitlink。
