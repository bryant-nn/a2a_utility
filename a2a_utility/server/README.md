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
│   │   └── agent_card.py         AgentDescriptor：discovery 用的最小描述
│   └── services/
│       └── discovery_service.py  rank_agents()：純函式排序
│
├── application/                  DTO + port（介面）+ 純邏輯服務
│   ├── dtos.py                   ExtendedRequestContext —— handler 的第一個參數。
│   │                             .message/.current_task 在這裡轉成 schema 型別（轉換邊界）
│   ├── ports/
│   │   ├── inbound/              「別人可以呼叫我」
│   │   │   ├── agent_handler_port.py     AgentHandlerPort（domain agent 實作的合約）
│   │   │   ├── on_cancel_port.py         OnCancelPort（可選）
│   │   │   └── discovery_use_case_port.py
│   │   └── outbound/             「我需要別人提供」
│   │       └── registry_port.py          AgentRegistryPort
│   └── use_cases/                register_agent_card / search_agent
│
├── adapters/
│   ├── inbound/                  外面進來
│   │   ├── agent_executor.py     原生 AgentExecutor 的真子類別。呼叫 handler，
│   │   │                         例外變 FAILED（帶錯誤訊息）
│   │   ├── call_context_builder.py  組裝每個請求的 ServerCallContext（擴充點，預設不多做事）
│   │   └── discovery_agent_executor.py
│   └── outbound/                 往外出去
│       ├── event_queue_adapter.py       ExtendedEventQueue（只有 enqueue_event）
│       ├── task_updater_adapter.py      ExtendedTaskUpdater（原生 TaskUpdater 真子類別）
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
  → A2AUtilityCallContextBuilder      組裝 ServerCallContext
  → DefaultRequestHandler (= V2)      建 ActiveTask
  → AgentExecutor.execute()
      └ handler(ctx, eq)              ← domain agent 的程式碼從這裡開始
            ExtendedTaskUpdater(...)  ← 自己建，跟原生一樣
            .start_work() / .add_artifact() / .complete()
                └ 每個都先 _ensure_task()（Task 事件必須先於 status 事件）
                     └ ExtendedEventQueue.enqueue_event()  ← 驗 task_id
                          └ 原生 EventQueue → EventConsumer → TaskManager → SSE 回 client
```

`handler` 拋出的例外由 `AgentExecutor` 的安全網接住 → FAILED（帶錯誤訊息）。
