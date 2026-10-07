# Reddit 官方 OAuth 唯讀工具：the operator 一次性設定

## 建 app

到 <https://www.reddit.com/prefs/apps> 建立 **web app**：

- 名稱：`SeafoodRedditReadOnly`
- 說明：`Personal read-only Reddit research CLI; no posting or moderation.`
- redirect URI：`http://127.0.0.1:8765/reddit/callback`（逐字相同，含 path）
- OAuth 授權：`response_type=code`、`duration=permanent`、**scope 僅 `read`**。

選 web app 是為了 client secret + authorization code + refresh token；不採 script 型帳號密碼流程。登入要授權的 Reddit 帳號後，在 the operator 自己的終端執行：

```zsh
zsh ~/.openclaw/shared/tools/reddit/setup_oauth.zsh
```

腳本用 `read -rs` 遮罩 client ID、client secret 與完整 redirect URL（內含一次性 code）。它會印授權 URL；瀏覽器回到 127.0.0.1 時沒有伺服器，頁面可能顯示連線失敗，但網址列仍可複製完整 URL。腳本驗 `state` 與 redirect URI，直接向 Reddit 交換 code，僅把 client ID、client secret、永久 refresh token、User-Agent 等寫入 `~/.secrets/reddit.env`（0600）；一次性 code 不落地。不要把憑證或 redirect URL 貼到聊天工具。已存在的 `.env` 不覆蓋。

User-Agent：`macos:seafood-reddit-readonly:v1.0 (by /u/<授權帳號>)`。官方 wiki 格式為 `<platform>:<app ID>:<version> (by /u/<reddit username>)`。

`zsh -c` 假值 dry-run 通過；錯誤 state dry-run 以 rc=1 擋下。**尚未用真實 Reddit app/憑證交換 token。**

官方文件（Reddit 封存的 OAuth/API wiki；內容可能受新政策補充）：

- <https://github.com/reddit-archive/reddit/wiki/OAuth2>：web app 可保管 secret；`duration=permanent` 才回 refresh token；code 用 Basic Auth + POST `/api/v1/access_token` 交換。
- <https://github.com/reddit-archive/reddit/wiki/API>：OAuth 與描述性 User-Agent；該舊版寫 60 requests/min，因此實作將以更嚴格的 60/min 上限，並讀回應的 rate-limit headers。

新 app 的可用性仍取決於 Reddit 當前開發者政策與帳號核准狀態；未真測前不宣稱 API 可用。
