# a2a_utility 設計說明

給**維護這個 library 的人**看的。使用方式看 [README](../README.md)。

這份文件記錄「為什麼是現在這樣」，特別是那些對照原生 a2a-sdk 原始碼追出來、但從程式碼表面看不出來的
決定。

> **對照版本：a2a-sdk 1.1.2。** 下面每一段對原生行為的描述都是從那個版本的原始碼追出來的，不是憑印象。
> 升版時請連同 `tests/test_sdk_contract.py` 一起重新確認 —— 那支測試就是為了讓這份文件不會默默過期。

---

## 一、核心目標：domain agent 不 import `a2a.*`

這是所有設計的第一順位。判斷任何改動的標準是：**寫 agent 的人會不會因此需要 import a2a？**

實務上這件事失守的方式不是有人故意去 import，而是 wrapper 的某個方法回傳或接受一個原生型別，於是
使用者「不得不」跟著 import。曾經失守的地方（現已修正）：

| 洩漏點 | 現在 |
|---|---|
| `ExtendedRequestContext.message` 回傳原生 `Message` | 回傳 `ExtendedMessage` |
| `.current_task` 回傳原生 `Task` | 回傳 `ExtendedTask` |
| `.native` 公開原生 `RequestContext` | 改成 `_native`，僅供 library 內部 |
| `update_status(state)` 吃原生 `TaskState`（整數 enum） | 吃 `ExtendedTaskState`（str enum） |
| 八個狀態捷徑的 `message=` 只吃原生 `Message` | 吃 str / part / parts / `ExtendedMessage` |
| `new_agent_message()` 回傳原生 `Message` | 回傳 `ExtendedMessage` |
| `create_app(agent_card=)` 吃原生 `AgentCard` | 吃 `ExtendedAgentCard` |
| `server/__init__` re-export `RequestContext`/`EventQueue` | 移除（等於在告訴使用者這些可以用） |
| `main.py` 自己 `from a2a.types import AgentSkill` | 移除 —— 這支是別人會照抄的範本 |

`server/__init__.py` 現在只 re-export 兩個原生型別：`IDGenerator` 和 `ServerCallContextBuilder`。理由是
使用者要「指名」它們才能傳 `message_id_generator=` 或子類化 `context_builder=`。其餘一律不 re-export。

驗證方式（`README` 的測試段也有）：

```bash
grep -rn "^from a2a\.\|^import a2a\b" ../weather_agent ../calculate_agent ...   # 預期零筆
```

---

## 二、`ExtendedTaskUpdater` 為什麼每個方法都 override

一度只 override `add_artifact` / `new_agent_message` / `update_status` 三個，其餘八個狀態捷徑
（`complete`/`failed`/…）完全繼承原生。那個版本很漂亮：「八個方法是原生自己的實作，一行沒改，靠 Python
動態 dispatch 讓 `update_status` 的 hook 對它們也生效」。

**但它讓型別洩漏從後門走回來**：原生 `complete(message: Message)` 吃的是 protobuf。繼承等於對使用者說
「想在完成時附一句話？自己去 import `a2a.types.Message` 建一個」。

所以現在八個全部顯式 override。犧牲了那個漂亮的性質，換到的是 `message=` 可以吃 `str`。**目標優先於
形式**。

`update_status` 的 override 另外承擔了 `_ensure_task()`：協定要求 `Task` 事件必須先於任何
`TaskStatusUpdateEvent`／`TaskArtifactUpdateEvent`，否則 `EventConsumer._handle_task_modification_event`
會丟 `InvalidAgentResponseError`。因為八個捷徑內部都走 `self.update_status(...)`，動態 dispatch 會進到
我們的 override，所以 lazy enqueue 對它們全部生效。

`_task_enqueued = True` 是在 `await` **之前**設的 —— 兩個並行的狀態更新否則會都看到 `False`、都送一次
`Task`，而原生會 log error 並丟掉第二個。

---

## 三、Gate Keeper 為什麼在 executor 裡，不是 Starlette middleware

需求是「失敗 → task state: rejected / auth-required」。

**middleware 做不到這件事**。middleware 只能回 HTTP 狀態碼，產不出 task 事件。只有
`AgentExecutor.execute()` 裡面的程式碼能把「拒絕」變成一個 A2A client 看得懂的 task state。

所以：

```
GateMiddleware（可選、只看有沒有 token）  ← 便宜的前置粗篩，回 401
      ↓
AgentExecutor.execute()
      ↓ GateKeeper.authorize()  ← 真正的閘門，回 AuthDecision
      ├ Reject       → task_updater.reject()        → REJECTED
      ├ AuthRequired → task_updater.requires_auth() → AUTH_REQUIRED
      └ Allow        → ctx.attach_user() → handler
```

`GateMiddleware` 是**可選的效能優化**，不是安全邊界。它只檢查 token 的存在與格式，永遠不驗證 ——
簽章驗證只有 `TokenVerifierPort` 一個實作，不能有第二份會漂移的。

### 三個決策而不是兩個

`AuthRequired` 和 `Reject` 分開是刻意的：

- **AuthRequired**（沒 token / token 過期或無效）→ 「我不知道你是誰，去拿憑證再來」。**可重試。**
- **Reject**（有 token 但權限不足）→ 「我知道你是誰，不行」。**重試無意義。**

合併成一種會讓 token 過期的呼叫端放棄、讓沒權限的呼叫端一直重試。client 端也對應：REJECTED/FAILED 丟
`A2ACallError`（拿不到答案），AUTH_REQUIRED/INPUT_REQUIRED 正常回傳（任務暫停中，理由在
`status_message`）。

### 為什麼 tool 層級不在 gate 裡

進 handler 之前不可能知道這次請求會用到哪些 tool。所以 tool 層級走「用到的地方檢查」：
`context.user.require("tool:x")` 丟 `PermissionDenied`，`AgentExecutor` 把它跟一般例外分開接
（`reject()` 而非 `failed()`），最後落在同一個 REJECTED 狀態。

### 權限服務掛掉時不做任何猜測

`GateKeeper` **沒有**在權限查詢外面包 try/except。兩種默默的處理都不對：

- fail open → gate 形同虛設
- fail closed → 一次故障看起來像一大批權限 bug

例外往上拋到 `AgentExecutor`，變成帶原因的 FAILED task，讓故障看起來像故障。

### 預設放行，但很吵

沒給 `gate_keeper=` 時裝 `AllowAllGateKeeper` 並印 WARNING。本機開發不該需要一套 token issuer。但
寬鬆的預設只有在「忘記設」很大聲時才安全，所以：

- 啟動時 WARNING
- `A2A_REQUIRE_AUTH=true` 讓「沒給 gate」變成**啟動失敗**
- `AllowAllGateKeeper` 回的是**未驗證**的 `UserContext`，不是萬能的 —— 所以
  `context.user.require(...)` 在沒有 gate 的環境下仍然會拒絕，而不是放行

最後一點很重要：如果 AllowAll 回一個「什麼權限都有」的 user，那 handler 裡的 tool 檢查會在 gate 被關掉時
默默失效。方向要反過來。

### card 上的宣告是必要的，不是裝飾

原生 `AuthInterceptor.before()` 的邏輯是：讀 agent card 的 `security_requirements`，逐一比對
`security_schemes`，找到才掛 header。**card 沒宣告 → 空迴圈 → 什麼都不送。**

所以 `ExtendedAgentCard.auth=BearerAuth()` 一定要設，否則守規矩的 client 一個 header 都不會送，而 server
全部回 401，兩邊都覺得自己沒錯。

我們的 `_A2AAuthInterceptor` 對這件事多加了一層保險：呼叫端明確給了 `credentials=`，但目標 card 什麼都
沒宣告時，退回送一個 `Authorization: Bearer`。card 有宣告但原生決定不掛時，**不覆寫** —— 那是原生判斷
沒有匹配的 scheme，二次猜測會送出對方沒要求的憑證。

---

## 四、`ExtendedEventQueue` 的兩個方法

原生 `EventQueue` 在 1.1.0 變成 ABC，docstring 寫得很直白：

> Producer-side interface passed to `AgentExecutor.execute`/`cancel`. Exposes only `enqueue_event`.
> The consumer is framework-managed and not part of the public surface.

舊版 wrapper 為了「完整 1:1 對等」把 `dequeue_event`/`tap`/`close`/`task_done`/`is_closed` 全部轉出去。
那些方法在具體實作（`EventQueueSource`/`EventQueueSink`）上有，但不在框架交給 executor 的**介面**上 ——
而且交給 domain agent 是有害的：handler 呼叫 `close()` 會直接拆掉框架還在消費的 stream，
`dequeue_event()` 會把事件從框架自己的 consumer 手上偷走。

**跟介面對等才是對等，跟實作細節對等是坑。**

### task_id 驗證只在 event 真的帶了 id 時做

`TaskManager.save_task_event()` 會拒絕 task_id 對不上的事件，而且是在框架自己的事件處理路徑上丟，
不在 handler 的 coroutine 裡 —— 所以 `AgentExecutor` 接不到，會變成很難看的崩潰。`enqueue_event` 提前
在 handler 自己的 coroutine 裡檢查，丟一個接得到的 `ValueError`。

但**不能對沒帶 task_id 的事件檢查**：native message-mode 的獨立 `Message` 回覆本來就不帶 task_id
（proto3 預設 `""`），框架的 `_handle_message_event` 照收。舊版對它一視同仁地驗證，等於擋掉了它 docstring
自稱支援的那個 workflow。

### `enqueue_message` 為什麼要獨立出來

`enqueue_event(event: Event)` 的 `Event` 是原生的 `Union[Message, Task, TaskStatusUpdateEvent,
TaskArtifactUpdateEvent]`。這是本次改造一度漏掉的型別洩漏：唯一會直接呼叫 `enqueue_event` 的情境是
message-mode（`ExtendedTaskUpdater` 結構上做不到），但要送出去就得先把 `ExtendedMessage` 呼叫
`.to_protobuf()` 轉成原生型別再傳進去 —— handler 沒 import `a2a`，但手上握著一個原生型別的物件、傳給一個
宣告吃原生型別的方法，跟「domain agent never touches a2a.*」的目標矛盾。

修法是新增 `enqueue_message(message: ExtendedMessage) -> None`，內部做 `.to_protobuf()` 再呼叫
`enqueue_event`（沿用同一套 task_id 驗證邏輯，不重複一份）。`enqueue_event` 保留下來，但重新定位成
**進階 escape hatch**：真的手上已經有一個原生 `Task`/`TaskStatusUpdateEvent`/`TaskArtifactUpdateEvent`
物件時才用（`ExtendedTaskUpdater` 內部自己就是這樣用它的），一般 handler 應該只碰得到
`enqueue_message`。

---

## 五、例外安全網：理由已經跟舊版不同

**舊版 docstring 是錯的**（對 1.1.2 而言）。它說：

> 原生 `_run_event_stream()` 呼叫 `agent_executor.execute()` 完全沒有 try/except，例外只會在 server
> 自己的 consume loop 裡重新 raise，不會變成優雅的狀態送回 caller。

那段追的是 `LegacyRequestHandler`。1.1.2 的 `a2a.server.request_handlers.DefaultRequestHandler`
**已經是 `DefaultRequestHandlerV2`**（ActiveTask 架構），而且：

- `ActiveTask._run_producer()` 有 try/except，會把 task 標成 FAILED 並把例外轉給 consumer
- `EventConsumer.run()` 也有自己的 except，同樣會標 FAILED

所以「task 會卡住」這個問題原生已經解決了。

**安全網現在的理由只有一個**：原生送的是**沒有 message 的空 FAILED status**，不會告訴 caller 出了什麼事。
安全網補的是錯誤訊息（`"Agent error: ..."`），是診斷體驗的加值，**不是在補正確性缺口**。

這個區別很重要 —— 如果誤以為它在補正確性缺口，未來有人可能會加上更多重複框架已做的事。

---

## 六、`aclose()` 必須接進 lifespan

`DefaultRequestHandlerV2.aclose()` 的 docstring：

> Drains the `ActiveTaskRegistry` so a server shutdown leaves no pending `asyncio.Task`.
> Intended to be wired into an ASGI `lifespan` / `on_shutdown` hook.

`create_app` 的 `_make_lifespan` 兩種模式都接了（DISCOVERY 模式以前根本沒有 lifespan）。
`add_to_fastapi` 因為 FastAPI 有自己的 lifespan，改用 `app.add_event_handler("shutdown", ...)`。

---

## 七、Client 端：ownership 與那個會咬人的 close

`Client.close()` → `transport.close()` → **`httpx_client.aclose()`**（見 `JsonRpcTransport.close`）。

也就是說關掉原生 a2a Client 會連帶關掉底下的 httpx client —— 即使那個 client 是呼叫端傳進來、還要繼續用
的。第一次呼叫之後第二次就會 `RuntimeError: Cannot send a request, as the client has been closed`。

所以 `ExtendedAgentClient.close()` 和 `call_agent_result()` 都只在**自己擁有** httpx client 時才關。
`ExtendedAgentClient` 本身沒有其他資源要釋放。

### 一次性函式建在可重用 class 之上

`call_agent*` 是 `ExtendedAgentClient` 的薄包裝，不是平行實作。回應解析（`StreamResponse` 那四個 oneof
分支）只有一份。

`StreamResponse` 的 `message` 分支曾經完全沒處理 —— 加上 `ExtendedEventQueue` 擋掉沒有 task_id 的
Message，message-mode 是**端到端壞掉**的。兩邊都修了，`tests/e2e/test_agent_server.py` 有對應的測試。

---

## 八、測試分層

| 層 | 測什麼 | 為什麼需要 |
|---|---|---|
| `tests/test_sdk_contract.py` | a2a-sdk 的表面 | SDK 升版時**先炸這裡**。1.1.0 把 `DefaultRequestHandler` 換成 V2、把 `EventQueue` 變成 ABC —— 兩個都不會讓 import 失敗，但都改變了我們 adapter 的意義 |
| `tests/e2e/` | 完整堆疊（真 app + 真 client，走 ASGI） | 唯一能看到「框架對我們送的東西有什麼反應」的層 |
| `tests/server/`、`tests/schema/` | 各 adapter 單獨 | 快，但看不到框架的 consumer 端 |

`tests/conftest.py` 的 `FakeEventQueue` 只模擬 producer 端。**下列問題單元測試一個都抓不到**，都需要
e2e：Task 必須先於 status 事件、`aclose()` 沒接、message-mode 壞掉、gate 拒絕產出什麼 task state、
憑證有沒有真的送到 header 上。

---

## 九、分層規則

```
adapters/  →  application/  →  domain/
```

- `domain/` 不 import 任何框架。`user_context.py` 只操作純 `dict`（剛好就是原生
  `ServerCallContext.state` 的型別，但這個模組從不指名那個型別）。
- `application/dtos.py` 讀 `domain/models/user_context.py` 的 reader function，**不走 adapters 層的
  便利函式** —— 否則依賴方向就反了。
- `app.py`／`main.py` 是 composition root，唯一允許把所有層兜起來的地方。

`AgentHandlerPort` 的第二個參數型別是 `ExtendedEventQueue`（adapters 層）—— 這是唯一一個刻意的例外，
因為這個 port 的重點就是把那個具體物件交給 domain agent。

---

## 十、命名

一律 `Extended*` 前綴。曾經考慮全部拿掉前綴、讓套件名承擔區隔（`from a2a_utility import Part`），對
agent 作者最直覺，但會讓 library 內部在轉換邊界同時看到兩套同名型別。目前的選擇是保留前綴，好處是
`from a2a.types import Part` 和 `class ExtendedPart` 可以並存在同一支檔案而不打架。

`ExtendAgentCard`／`ExtendAgentSkill` 曾經漏掉 `ed`，已統一。
