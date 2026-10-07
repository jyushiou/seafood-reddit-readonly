#!/usr/bin/env python3
"""Exchange an OAuth code without logging secrets or shell argv exposure."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

REDIRECT = 'http://127.0.0.1:8765/reddit/callback'
ENV_PATH = Path.home() / '.secrets' / 'reddit.env'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def fail(reason: str) -> None:
    raise SystemExit(f'STOP: {reason}')


def main() -> None:
    values = [sys.stdin.readline().rstrip('\n') for _ in range(5)]
    client_id, secret, user, state, callback = values
    if not re.fullmatch(r'[A-Za-z0-9_-]{5,128}', client_id):
        fail('client ID 格式不符')
    if not secret or any(x in secret for x in '\r\n'):
        fail('client secret 格式不符')
    if not re.fullmatch(r'[A-Za-z0-9_-]{3,32}', user):
        fail('Reddit 使用者名稱格式不符')
    if not state or any(x in state for x in '\r\n'):
        fail('state 格式不符')
    parsed = urllib.parse.urlparse(callback)
    fixed = urllib.parse.urlparse(REDIRECT)
    if (parsed.scheme, parsed.netloc, parsed.path) != (fixed.scheme, fixed.netloc, fixed.path):
        fail('redirect URI 不符')
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    if query.get('state') != [state] or len(query.get('code', [])) != 1 or not query['code'][0]:
        fail('state/code 不符或缺失')
    if 'error' in query:
        fail('Reddit 未授權')
    ua = f'macos:seafood-reddit-readonly:v1.0 (by /u/{user})'
    if '--dry-run' in sys.argv[1:]:
        print('DRY_RUN_OK: 隱藏輸入、URL/state/code 驗證通過；未連線、未寫憑證')
        return
    body = urllib.parse.urlencode({
        'grant_type': 'authorization_code', 'code': query['code'][0],
        'redirect_uri': REDIRECT,
    }).encode()
    basic = base64.b64encode(f'{client_id}:{secret}'.encode()).decode()
    request = urllib.request.Request(
        'https://www.reddit.com/api/v1/access_token', body,
        headers={'Authorization': f'Basic {basic}', 'User-Agent': ua,
                 'Content-Type': 'application/x-www-form-urlencoded'},
        method='POST',
    )
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=15) as response:
            token = json.load(response)
    except (urllib.error.URLError, ValueError):
        fail('Reddit token 交換失敗；未寫入憑證。請核 app 類型、redirect、code 是否過期')
    refresh = token.get('refresh_token')
    if not isinstance(refresh, str) or not refresh or set(token.get('scope', '').split()) != {'read'}:
        fail('未取得永久 refresh token 或 scope 非單獨 read；未寫入憑證')
    entries = {
        'REDDIT_CLIENT_ID': client_id,
        'REDDIT_CLIENT_SECRET': secret,
        'REDDIT_REFRESH_TOKEN': refresh,
        'REDDIT_USER_AGENT': ua,
        'REDDIT_REDIRECT_URI': REDIRECT,
        'REDDIT_SCOPE': 'read',
    }
    if any('\n' in value or '\r' in value for value in entries.values()):
        fail('憑證含換行；未寫入')
    directory = ENV_PATH.parent
    directory.mkdir(mode=0o700, exist_ok=True)
    if directory.stat().st_mode & 0o077:
        fail('~/.secrets 權限過寬；未寫入')
    if ENV_PATH.exists():
        fail('reddit.env 已存在；不覆蓋')
    temporary = directory / f'.reddit.env.{os.getpid()}.tmp'
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            for key, value in entries.items():
                stream.write(f'{key}={value}\n')
        os.link(temporary, ENV_PATH)
    finally:
        temporary.unlink(missing_ok=True)
    print(f'已寫入 {ENV_PATH}；mode=600；keys=' + ','.join(entries))


if __name__ == '__main__':
    main()
