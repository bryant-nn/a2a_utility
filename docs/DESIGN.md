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
| `.native` 公開原生 `RequestContext` | 拿掉了（連 `_native` 都刪了——沒有任何呼叫者，見第二節） |
| `update_status(state)` 吃原生 `TaskState`（整數 enum） | 吃 `ExtendedTaskState`（str enum） |
| 八個狀態捷徑的 `message=` 只吃原生 `Message` | 吃 str / part / parts / `ExtendedMessage` |
| `new_agent_message()` 回傳原生 `Message` | 回傳 `ExtendedMessage` |
| `create_app(agent_card=)` 吃原生 `AgentCard` | 吃 `ExtendedAgentCard` |
| `server/__init__` re-export `RequestContext`/`EventQueue` | 移除（等於在告訴使用者這些可以用） |
| `main.py` 自己 `from a2a.types import AgentSkill` | 移除 —— 這支是別人會照抄的範本 |

`server/__init__.py` 現在只 re-export 一個原生型別：`IDGenerator`。理由是使用者要「指名」它才能傳
`message_id_generator=`。其餘一律不 re-export。

驗證方式（`README` 的測試段也有）：

```bash
grep -rn "^from a2a\.\|^import a2a\b" ../weather_agent ../calculate_agent ...   # 預期零筆
```

---

## 二、從 imperative 換成 declarative：`DomainAgentExecutorPort`

舊版：domain agent 寫一個 `async def handle(context, event_queue) -> None`，自己建
`ExtendedTaskUpdater(context, event_queue)`，呼叫哪個方法決定 task 怎麼結束——完全鏡射原生
`AgentExecutor.execute(context, event_queue) -> None` 的形狀，連 `start_work()` 都要自己送。

現在：domain agent 繼承 `DomainAgentExecutorPort`，`execute(self, context: ExtendedRequestContext)`
是一個 async generator——yield `domain/models/task_events.py` 的 `TaskEvent`（`Progress`／
`PublishArtifact`／`InputRequired`／`AuthRequired`／`Rejected`／`MessageReply`）驅動 task，`return`
就是 COMPLETED，`raise` 就是 FAILED。`cancel()` 是可選 override，回傳值是要附在 CANCELED 上的自訂
訊息（不是決定要不要取消——task 一律會被標成 CANCELED）。

這是**刻意違反**第一節以外的另一條既有原則：`AgentHandlerPort`/`OnCancelPort`（現已刪除）的 docstring
主張「plain Callable，不強迫繼承」。這次改成 class-based 是使用者明確要的用法，理由是宣告式的介面
需要一個地方掛 `cancel()` 的預設實作，而且比起「鏡射原生」，這次的優先順序是「domain agent 連 task
生命週期的概念都不用懂」——不只型別被封住，連「什麼時候該呼叫哪個方法」這個知識都不用學。

`adapters/inbound/agent_executor.py` 是唯一讀懂這些 yield 的地方，讀到的每個 `TaskEvent` 對應哪個
task 狀態、什麼時候該 lazy 建 Task，見第三節。

---

## 三、Base executor 內部直接用原生，不繼續用 `ExtendedTaskUpdater`/`ExtendedEventQueue`

`ExtendedTaskUpdater`（原生 `TaskUpdater` 真子類別）和 `ExtendedEventQueue`（原生 `EventQueue` 的薄
wrapper）曾經存在，被這次改動整個刪除。這裡記錄決策過程，因為中間繞了一圈。

### 一開始傾向繼續借用

`DomainAgentExecutorPort` 這個設計本身參考了同事 mentor 的一份 pseudo code 範例（`BaseA2AWrapperExecutor`
+ `DomainAgentExecutorPort`，domain agent yield `TextChunk`/`StatusMessage`/`ArtifactResult`/...）。
那份範例的 base executor 直接呼叫原生 `TaskUpdater`：

```python
updater = TaskUpdater(event_queue=event_queue, task_id=task_id, context_id=context_id)
await updater.submit()
await updater.start_work()
```

**這樣寫在真正的 a2a-sdk 1.1.2 下會炸**：讀過原生 `TaskUpdater.update_status()` 原始碼確認，它不會
自動先送一個 `Task` 事件；而 `EventConsumer._handle_task_modification_event` 要求任何
`TaskStatusUpdateEvent`/`TaskArtifactUpdateEvent` 之前**必須**先有 `Task` 事件，否則丟
`InvalidAgentResponseError`。這正是舊版 `ExtendedTaskUpdater._ensure_task()` 存在的理由（`tests/e2e/
test_agent_server.py::test_task_is_enqueued_before_the_first_event` 釘住這件事）。

一開始因此主張：既然這段邏輯已經寫對、有測試守著，base executor 應該繼續借用
`ExtendedTaskUpdater`/`ExtendedEventQueue`，不要冒重新踩坑的風險。

### 但這是 mentor 沒寫對，不是原生辦不到

追問之後發現：上面列的每一個顧慮（Task 先行、cancel 在 Task 建立前就進來的邊界案例、`MessageLike`
轉換）都只是「自己寫一份會正確運作的邏輯」，沒有一個是原生 `TaskUpdater`/`EventQueue` 結構上做不到的。
而且既然這次是**完全取代**（不是新舊並存），`ExtendedTaskUpdater`/`ExtendedEventQueue` 在新設計下會
變成**沒有呼叫者的孤兒程式碼**——唯一用過它們的 `agent_executor.py` 這次要整支改寫，DISCOVERY 模式的
`DiscoveryAgentExecutor` 也用到它們，一起要動。留著等於維護兩份「怎麼跟原生 Task 生命週期打交道」的
邏輯，一份在已經沒人呼叫的 `ExtendedTaskUpdater`，一份在新的 `agent_executor.py`。

**最終決定：整個刪除，Task-lifecycle 邏輯收進 `adapters/inbound/_native_task.py`**（private，不對外
匯出），AGENT 模式的 `agent_executor.py` 跟 DISCOVERY 模式的 `discovery_agent_executor.py` 共用：

- `initial_task(context, task_id, context_id) -> Task`：三分支——`current_task`（續傳）優先、
  否則從 `message` 建、都沒有就手動兜一個最小 `Task`（cancel 在 Task 送出前就進來的邊界案例，原生
  沒處理過會在深處炸 `AttributeError`）。
- `coerce_message(message, ...) -> Optional[Message]`：`MessageLike` → 原生 `Message`。實測發現原生
  `TaskUpdater.new_agent_message()` 已經處理掉訊息 id 生成，這段比原本以為的還簡單。

好處：全 codebase 只剩一份這個邏輯，`AgentExecutor`（AGENT 模式）跟 `DiscoveryAgentExecutor`
（DISCOVERY 模式）共用同一個 `_native_task.py`，不是各自維護一份。

### `MessageReply` 為什麼能讓 Task 完全不被建立

`agent_executor.py` 裡 Task 是**惰性建立**的——`ensure_task()` 只在真的需要送 status/artifact 事件時
才呼叫，不是在 `execute()` 一進來就無條件送。這是 message-mode 之所以能在新設計下繼續運作的關鍵：
mentor 範例的 `submit()+start_work()` 無條件搶在最前面，等於 Task 一定會被建立，message-mode（獨立
回一則 `Message`、完全不建 Task）在那個結構下根本做不到——這是這次設計特別要保留、mentor 版本做不到
的能力，`tests/e2e/test_agent_server.py::test_message_mode_reply_reaches_the_caller` 驗證的就是這個。

### 例外處理跟 cancel 的兩個刻意選擇

- **保留真實錯誤訊息，不採用「通用訊息」**：mentor 範例的 except 區塊把例外吞成固定字串
  `"Internal server error"`，理由可能是不想讓內部例外洩漏給呼叫端。這次沒有採用——第四節記錄的
  「安全網存在的唯一理由是保留診斷用的錯誤訊息」這個決策沒有變，`agent_executor.py` 一樣送
  `f"Agent error: {e}"`。
- **`cancel()` 回傳值只決定訊息，不決定要不要取消**：mentor 版本 `await self._domain_executor.cancel
  (domain_ctx)` 之後無條件 `await updater.cancel()`，domain 端的 `cancel()` 完全不能影響最終狀態，
  只能做旁路的清理。`DomainAgentExecutorPort.cancel()` 保留同樣的「一律 CANCELED」語意，但讓回傳值
  變成可以附加的自訂訊息——這是舊版 `OnCancelPort`（可以自己建 updater、自訂整個結果）留下來的能力
  子集，比 mentor 版本多一點、比舊版 `OnCancelPort` 少一點（domain 不再能決定不同的終態，只能加一句
  話），這是刻意的簡化：外部 cancel RPC 的結局本來就該是 CANCELED，不該由 domain agent 決定成別的
  狀態。

### 同一輪清掉的另一個孤兒：`A2AUtilityCallContextBuilder`

`adapters/inbound/call_context_builder.py` 曾經是原生 `DefaultServerCallContextBuilder` 的一個空子
類別——`build()` 什麼都不做，只呼叫 `super().build(request)`。它存在的理由是「context_builder= 的
預設值，也是要覆寫時繼承的對象」。但上一輪拿掉 production hooks 時，`create_app()` 的 `context_builder=`
參數已經被移除，這個類別因此也變成沒有任何注入點在用的孤兒——跟 `ExtendedTaskUpdater`/
`ExtendedEventQueue` 同一個模式：先被上游的簡化拿掉了唯一的呼叫理由，隔了一輪才發現。已刪除，
`app.py`／自組 app 的 escape hatch 現在直接用原生 `DefaultServerCallContextBuilder`。

---

## 四、例外安全網：理由已經跟舊版不同

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

## 五、`aclose()` 必須接進 lifespan

`DefaultRequestHandlerV2.aclose()` 的 docstring：

> Drains the `ActiveTaskRegistry` so a server shutdown leaves no pending `asyncio.Task`.
> Intended to be wired into an ASGI `lifespan` / `on_shutdown` hook.

`create_app` 的 `_make_lifespan` 兩種模式都接了（DISCOVERY 模式以前根本沒有 lifespan）。

---

## 六、Client 端：ownership 與那個會咬人的 close

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

## 七、測試分層

| 層 | 測什麼 | 為什麼需要 |
|---|---|---|
| `tests/test_sdk_contract.py` | a2a-sdk 的表面 | SDK 升版時**先炸這裡**。1.1.0 把 `DefaultRequestHandler` 換成 V2、把 `EventQueue` 變成 ABC —— 兩個都不會讓 import 失敗，但都改變了我們 adapter 的意義 |
| `tests/e2e/` | 完整堆疊（真 app + 真 client，走 ASGI） | 唯一能看到「框架對我們送的東西有什麼反應」的層 |
| `tests/server/`、`tests/schema/` | 各 adapter 單獨 | 快，但看不到框架的 consumer 端 |

`tests/conftest.py` 的 `FakeEventQueue` 只模擬 producer 端。**下列問題單元測試一個都抓不到**，都需要
e2e：Task 必須先於 status 事件、`aclose()` 沒接、message-mode 壞掉、憑證有沒有真的送到 header 上。

---

## 八、分層規則

```
adapters/  →  application/  →  domain/
```

- `domain/` 不 import 任何框架 —— `domain/models/agent_card.py` 的 `AgentDescriptor`、
  `domain/models/task_events.py` 的 `TaskEvent` 系列都是純 dataclass，不碰 a2a-sdk 或 Starlette
  （`TaskEvent` 依賴 `schema` 的 `ExtendedPart`/`MessageLike`，但 `schema` 本身也零框架依賴，不算
  破例）。
- `app.py`／`main.py` 是 composition root，唯一允許把所有層兜起來的地方。

**舊版有一個刻意的例外，這次拿掉了**：`AgentHandlerPort`（已刪除）的第二個參數型別是
`ExtendedEventQueue`（adapters 層），domain agent 因此直接依賴 adapters 層的具體型別。新的
`DomainAgentExecutorPort.execute(self, context: ExtendedRequestContext)` 只有一個參數，型別是
`ExtendedRequestContext`（application 層），完全不觸碰 adapters 層——分層規則現在沒有例外。

---

## 九、命名

一律 `Extended*` 前綴。曾經考慮全部拿掉前綴、讓套件名承擔區隔（`from a2a_utility import Part`），對
agent 作者最直覺，但會讓 library 內部在轉換邊界同時看到兩套同名型別。目前的選擇是保留前綴，好處是
`from a2a.types import Part` 和 `class ExtendedPart` 可以並存在同一支檔案而不打架。

`ExtendAgentCard`／`ExtendAgentSkill` 曾經漏掉 `ed`，已統一。
