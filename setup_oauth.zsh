#!/bin/zsh
# Interactive one-time authorization. Never echo or put credentials in argv.
set -eu
umask 077

script_dir=${0:A:h}
redirect_uri='http://127.0.0.1:8765/reddit/callback'
if [[ ${1:-} == --dry-run ]]; then
  state='dry-run-state'
else
  state=$(/usr/bin/openssl rand -hex 16)
fi

read -rs 'client_id?Reddit client ID（隱藏輸入）: '
print
read -rs 'client_secret?Reddit client secret（隱藏輸入）: '
print
read -r 'reddit_user?Reddit 使用者名稱（不含 u/）: '

auth_url=$(/usr/bin/python3 - "$client_id" "$state" "$redirect_uri" <<'PY'
import sys
from urllib.parse import urlencode
print('https://www.reddit.com/api/v1/authorize?' + urlencode({
    'client_id': sys.argv[1], 'response_type': 'code', 'state': sys.argv[2],
    'redirect_uri': sys.argv[3], 'duration': 'permanent', 'scope': 'read',
}))
PY
)
print '在瀏覽器登入要授權的 Reddit 帳號，開啟以下網址；只核准 read。'
print -r -- "$auth_url"
print '授權後瀏覽器可能顯示「無法連線到 127.0.0.1」；從網址列複製完整 redirect URL。不要貼到聊天工具。'
read -rs 'callback?完整 redirect URL（隱藏輸入）: '
print

if [[ ${1:-} == --dry-run ]]; then
  printf '%s\n' "$client_id" "$client_secret" "$reddit_user" "$state" "$callback" \
    | /usr/bin/python3 -B "$script_dir/exchange_code.py" --dry-run
else
  printf '%s\n' "$client_id" "$client_secret" "$reddit_user" "$state" "$callback" \
    | /usr/bin/python3 -B "$script_dir/exchange_code.py"
fi

unset client_id client_secret callback state
