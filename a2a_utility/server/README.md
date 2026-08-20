# a2a_utility.server — 檔案地圖

使用方式看 [`../../README.md`](../../README.md)，設計理由看
[`../../docs/DESIGN.md`](../../docs/DESIGN.md)。這份只回答一個問題：**東西放在哪、為什麼在那裡。**

## 分層

```
adapters/  →  application/  →  domain/          依賴方向永遠朝內
```

```
server/
├── domain/                       純 Python，零框架依賴
│   ├── models/
│   │   ├── agent_card.py         AgentDescriptor：discovery 用的最小描述
│   │   └── task_events.py        Progress / PublishArtifact / InputRequired /
│   │                             AuthRequired / Rejected / MessageReply（TaskEvent 聯集）——
│   │                             DomainAgentExecutorPort.execute() 的 yield 詞彙
│   └── services/
│       └── discovery_service.py  rank_agents()：純函式排序
│
├── application/                  DTO + port（介面）+ 純邏輯服務
│   ├── dtos.py                   ExtendedRequestContext —— execute() 的參數。
│   │                             .message/.current_task 在這裡轉成 schema 型別（轉換邊界）
│   ├── ports/
│   │   ├── inbound/              「別人可以呼叫我」
│   │   │   ├── domain_agent_executor_port.py  DomainAgentExecutorPort（domain agent 繼承的合約，
│   │   │   │                     execute()+cancel()）
│   │   │   └── discovery_use_case_port.py
│   │   └── outbound/             「我需要別人提供」
│   │       └── registry_port.py          AgentRegistryPort
│   └── use_cases/                register_agent_card / search_agent
│
├── adapters/
│   ├── inbound/                  外面進來
│   │   ├── agent_executor.py     原生 AgentExecutor 的真子類別。把 DomainAgentExecutorPort
│   │   │                         yield 出的 TaskEvent 轉成原生 TaskUpdater/EventQueue 呼叫，
│   │   │                         例外變 FAILED（帶錯誤訊息）
│   │   ├── _native_task.py       private：initial_task()/coerce_message()，AGENT／DISCOVERY
│   │   │                         兩個 executor 共用的 Task-lifecycle 邏輯，不對外匯出
│   │   └── discovery_agent_executor.py  同樣直接用原生 TaskUpdater/EventQueue
│   └── outbound/                 往外出去
│       └── in_memory_registry_adapter.py
│
├── card.py       ExtendedAgentCard / ExtendedAgentSkill
├── app.py        create_app(mode=) / serve / serve_as_a2a —— composition root
├── config.py     A2ASettings（A2A_ 前綴）
└── main.py       獨立可執行節點（env 驅動）。也是別人會照抄的範本，所以它自己不 import 任何 a2a.*
```

## 一次請求怎麼流過去

```
HTTP POST /
  → create_jsonrpc_routes 的 dispatcher
  → DefaultServerCallContextBuilder（原生，沒有包一層）  組裝 ServerCallContext
  → DefaultRequestHandler (= V2)      建 ActiveTask
  → AgentExecutor.execute()
      └ DomainAgentExecutorPort.execute(ctx)   ← domain agent 的程式碼從這裡開始，
            yield Progress(...)                   一個 async generator，不碰 TaskUpdater/EventQueue
            yield PublishArtifact(...)
            └ AgentExecutor 把每個 yield 出的 TaskEvent 轉成原生呼叫：
                 原生 TaskUpdater.update_status()/.add_artifact()（Task 先行由 AgentExecutor
                 自己惰性補上，第一個需要它的事件才送）
                      └ 原生 EventQueue → EventConsumer → TaskManager → SSE 回 client
```

domain generator 正常跑完（沒中途 `return`）→ `complete()`；拋例外由 `AgentExecutor` 的安全網接住
→ FAILED（帶錯誤訊息）；`yield MessageReply(...)` 則完全跳過 Task，直接送一則獨立 `Message`。
