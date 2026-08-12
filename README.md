# a2a_utility

公司內部共用的 A2A 協定 library。獨立成自己的 repo（打算以 git submodule 的形式，讓任何 repo——不只
`multi_agent_a2a`，未來每個「有自己的 root agent + domain agents」的專案——都能引用同一份實作，而不是
每個專案各自複製貼上一份 A2A server/client 樣板碼。

兩個角色，各自獨立成子套件，互不強迫依賴：

```
a2a_utility/
├── server/     開 A2A 端點（AGENT 節點 / DISCOVERY 節點）— 見 a2a_utility/server/README.md
└── client/     呼叫 A2A 端點（call_agent / DiscoveryClient）— 見 a2a_utility/client/ 內的 docstring
```

## 安裝

還沒發到任何套件索引（內部或公開）——目前是本機 editable install，或未來作為 git submodule 後在消費端
repo 的環境裡裝：

```bash
# 只要當 server（domain agent 作者）
pip install -e /path/to/a2a_utility[server]

# 只要當 client（coordinator 作者）
pip install -e /path/to/a2a_utility[client]

# 兩者都要（例如同一個環境裡同時跑 domain agent 又跑 coordinator，本 demo 專案就是這樣）
pip install -e /path/to/a2a_utility[server,client]
```

裝完之後，任何地方都能直接 `import a2a_utility.server` / `import a2a_utility.client`，不需要
`sys.path.insert(...)` 這種 hack。

## 快速上手

**寫一個 domain agent（server 角色）：**

```python
from a2a_utility.server import serve_as_a2a

async def handle(text: str, emit_thought=None) -> str:
    if emit_thought:
        await emit_thought("思考中...")
    return f"你問的是：{text}"

serve_as_a2a(
    name="joke_agent", description="...", skill_id="joke",
    skill_name="Joke", skill_description="...", examples=["..."],
    handler=handle, port=9050,
    registry_url="http://127.0.0.1:8090",  # 給了就自動註冊 + heartbeat
)
```

**啟動一個 DISCOVERY 節點（服務發現，兩種方式都行）：**

```bash
# CLI（適合獨立行程／container）
A2A_SERVER_MODE=DISCOVERY A2A_PORT=8090 python -m a2a_utility.server.main
```

```python
# 函式呼叫（適合內嵌在別的行程/測試/腳本裡，不用另開 subprocess）
from a2a_utility.server import A2ASettings, ServerMode, run_discovery_server

run_discovery_server(A2ASettings(server_mode=ServerMode.DISCOVERY, port=8090))
```

**寫一個 coordinator（client 角色）：**

```python
from a2a_utility.client import DiscoveryClient, call_agent

directory = DiscoveryClient("http://127.0.0.1:8090")
agents = await directory.list_agents()          # [{"name", "description"}, ...]
entry = await directory.resolve("joke_agent")     # {"description", "agent_card_url"}
base_url = directory.agent_base_url(entry["agent_card_url"])
answer = await call_agent(base_url, "跟工程師有關的笑話", emit_thought=my_callback)
```

完整架構、六角形分層說明、request flow trace，見 [`a2a_utility/server/README.md`](a2a_utility/server/README.md)。

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
