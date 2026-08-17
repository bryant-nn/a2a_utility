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
│   │   ├── user_context.py       UserContext（誰在呼叫、能做什麼）+ PermissionDenied
│   │   │                         + read/write_user_context（只操作純 dict，不 import 任何 a2a.*）
│   │   └── auth_decision.py      Allow / AuthRequired / Reject
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
│   │   │   ├── gate_keeper_port.py       GateKeeperPort（驗證閘門）
│   │   │   └── discovery_use_case_port.py
│   │   └── outbound/             「我需要別人提供」
│   │       ├── token_verifier_port.py    TokenVerifierPort + InvalidToken
│   │       ├── permission_service_port.py PermissionServicePort
│   │       └── registry_port.py          AgentRegistryPort
│   ├── services/
│   │   └── gate_keeper.py        GateKeeper（四步驟閘門）+ AllowAllGateKeeper + ClaimNames
│   └── use_cases/                register_agent_card / search_agent
│
├── adapters/
│   ├── inbound/                  外面進來
│   │   ├── agent_executor.py     原生 AgentExecutor 的真子類別。跑 gate → 呼叫 handler →
│   │   │                         PermissionDenied 變 REJECTED、其他例外變 FAILED
│   │   ├── call_context_builder.py  收集 headers 供 gate 使用（刻意做得很少）
│   │   ├── gate_middleware.py    可選的 HTTP 層前置粗篩（回 401，不驗證）
│   │   └── discovery_agent_executor.py
│   └── outbound/                 往外出去
│       ├── event_queue_adapter.py       ExtendedEventQueue（只有 enqueue_event）
│       ├── task_updater_adapter.py      ExtendedTaskUpdater（原生 TaskUpdater 真子類別）
│       ├── jwt_token_verifier.py        JWT + JWKS（需要 [auth] extra）
│       ├── cached_permission_service.py TTL 快取，包住任何 PermissionServicePort
│       ├── http_permission_service.py   打內部 Permission Service
│       └── in_memory_registry_adapter.py
│
├── card.py       ExtendedAgentCard / ExtendedAgentSkill / BearerAuth / ApiKeyAuth
├── app.py        create_app(mode=) / serve / serve_as_a2a —— composition root
├── fastapi.py    add_to_fastapi() —— 掛進既有 FastAPI 服務
├── config.py     A2ASettings（A2A_ 前綴）+ build_gate_keeper()
└── main.py       獨立可執行節點（env 驅動）。也是別人會照抄的範本，所以它自己不 import 任何 a2a.*
```

## 一次請求怎麼流過去

```
HTTP POST /
  → (可選) GateMiddleware            沒 token 就 401，不浪費建 task
  → create_jsonrpc_routes 的 dispatcher
  → A2AUtilityCallContextBuilder      headers 收進 call_context.state
  → DefaultRequestHandler (= V2)      建 ActiveTask
  → AgentExecutor.execute()
      ├ GateKeeper.authorize()        取 token → 驗簽 → 查權限 → 檢查 agent 層級
      │   ├ AuthRequired → requires_auth() → 結束
      │   └ Reject       → reject()        → 結束
      ├ ctx.attach_user()
      └ handler(ctx, eq)              ← domain agent 的程式碼從這裡開始
            ExtendedTaskUpdater(...)  ← 自己建，跟原生一樣
            .start_work() / .add_artifact() / .complete()
                └ 每個都先 _ensure_task()（Task 事件必須先於 status 事件）
                     └ ExtendedEventQueue.enqueue_event()  ← 驗 task_id
                          └ 原生 EventQueue → EventConsumer → TaskManager → SSE 回 client
```

`handler` 拋出的例外：`PermissionDenied` → REJECTED，其他 → FAILED（帶錯誤訊息）。
