# Reddit 官方 OAuth 唯讀 CLI

Personal read-only research tool. OAuth scope: `read` only. Local limit: **≤60 requests/minute**.

這個工具只呼叫 Reddit 官方 OAuth API。它不登入瀏覽器、不讀 Cookie、不提供發文或管理命令。

## 一次性設定

由 the operator 依 [SETUP.md](SETUP.md) 建 web app，執行 `zsh setup_oauth.zsh`。憑證寫到 `~/.secrets/reddit.env`（0600）。不要把憑證、授權後的 redirect URL 或輸出中的貼文內容貼到公開頻道。

## 命令

```sh
python3 -B reddit.py hot python --limit 10
python3 -B reddit.py new python --limit 10
python3 -B reddit.py top python --time week --limit 10
python3 -B reddit.py search 'EEG PAC' --subreddit neuroscience --limit 10
python3 -B reddit.py post 1abcde --comments 10
```

每個成功命令只在 stdout 輸出一份 JSON。失敗在 stderr 輸出 `{"ok":false,"error":"..."}`，exit code 非零。`post` 傳回貼文全文及前 N 則頂層留言。列表與搜尋上限每次 100 筆。`--help` 不需憑證。

CLI 只接受本機擁有、非 symlink、非群組／其他人可讀的憑證檔。它用 refresh token 取得短期 access token，僅在記憶體中保留。token POST 固定到 `https://www.reddit.com/api/v1/access_token`；內容 GET 固定到 `https://oauth.reddit.com`。任何 HTTP redirect 都拒絕。工具沒有寫入端點。跨行程滑動視窗最多 60 次／分鐘（嚴於指定的 100）；遇 429 讀 `Retry-After` 並有限重試。這個限制只涵蓋本工具的呼叫，其他 Reddit client 的額度仍須另算。

## 驗證

開發時的離線測試用假憑證與假 HTTP 回應，不連網，不碰 the operator 的 `.env`。負控制涵蓋無憑證、錯 scope、轉址及非唯讀命令。真實抓取須等 the operator 完成 OAuth；若 Reddit 回 401／403，先檢查 app 准入，不要改用 Cookie 或繞過封鎖。

來源：Reddit [OAuth2 wiki](https://github.com/reddit-archive/reddit/wiki/OAuth2) 與 [API wiki](https://github.com/reddit-archive/reddit/wiki/API)（封存文件；現行准入政策可能另有限制）。
