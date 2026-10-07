#!/usr/bin/env python3
"""Reddit official OAuth API read-only CLI. Standard library only."""
from __future__ import annotations

import argparse
import base64
import fcntl
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent
ENV_PATH = Path.home() / '.secrets' / 'reddit.env'
TOKEN_URL = 'https://www.reddit.com/api/v1/access_token'
API_ROOT = 'https://oauth.reddit.com'
RATE_PATH = ROOT / '.rate_limit.json'
LOCK_PATH = ROOT / '.rate_limit.lock'
MAX_PER_MINUTE = 60  # stricter than task's <=100/min; archived Reddit API wiki says 60.
TIMEOUT = 15


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


OPENER = urllib.request.build_opener(NoRedirect)


class RedditError(Exception):
    pass


def load_credentials(path: Path = ENV_PATH) -> dict[str, str]:
    if not path.exists():
        raise RedditError('CREDENTIALS_MISSING: ~/.secrets/reddit.env 不存在')
    if path.is_symlink() or not path.is_file() or path.stat().st_uid != os.getuid():
        raise RedditError('CREDENTIALS_PERMISSIONS: reddit.env 必須是本使用者的普通檔案')
    if path.stat().st_mode & 0o077:
        raise RedditError('CREDENTIALS_PERMISSIONS: reddit.env 必須是 600')
    data: dict[str, str] = {}
    for raw in path.read_text(encoding='utf-8').splitlines():
        if not raw or raw.startswith('#'):
            continue
        if '=' not in raw:
            raise RedditError('CREDENTIALS_FORMAT: 必須 KEY=VALUE')
        key, value = raw.split('=', 1)
        if not re.fullmatch(r'REDDIT_[A-Z_]+', key) or key in data:
            raise RedditError('CREDENTIALS_FORMAT: key 無效或重複')
        data[key] = value
    required = ('REDDIT_CLIENT_ID', 'REDDIT_CLIENT_SECRET', 'REDDIT_REFRESH_TOKEN',
                'REDDIT_USER_AGENT', 'REDDIT_SCOPE')
    if any(not data.get(key) for key in required):
        raise RedditError('CREDENTIALS_MISSING: 缺必要 key')
    if data['REDDIT_SCOPE'] != 'read':
        raise RedditError('SCOPE_INVALID: 只允許 read')
    if not re.fullmatch(r'[^\r\n]+:[^\r\n]+:v[0-9][^\r\n]* \(by /u/[A-Za-z0-9_-]+\)',
                        data['REDDIT_USER_AGENT']):
        raise RedditError('USER_AGENT_INVALID: 格式不符')
    return data


class RateLimiter:
    """Cross-process sliding-window limit under a locked shared state file."""

    def __init__(self, state_path: Path = RATE_PATH, lock_path: Path = LOCK_PATH):
        self.state_path = state_path
        self.lock_path = lock_path

    def wait(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            with os.fdopen(fd, 'r+') as lock:
                while True:
                    fcntl.flock(lock, fcntl.LOCK_EX)
                    now = time.time()
                    try:
                        stamps = json.loads(self.state_path.read_text())
                        if not isinstance(stamps, list):
                            stamps = []
                    except (OSError, ValueError):
                        stamps = []
                    recent = [float(x) for x in stamps
                              if isinstance(x, (int, float)) and 0 <= now-float(x) < 60]
                    if len(recent) < MAX_PER_MINUTE:
                        recent.append(now)
                        temp = self.state_path.with_name('.rate_limit.tmp.' + str(os.getpid()))
                        fd2 = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                        with os.fdopen(fd2, 'w') as output:
                            json.dump(recent, output)
                        os.replace(temp, self.state_path)
                        fcntl.flock(lock, fcntl.LOCK_UN)
                        return
                    delay = max(0.1, 60 - (now - min(recent)) + 0.1)
                    fcntl.flock(lock, fcntl.LOCK_UN)
                    time.sleep(delay)
        except Exception:
            # Never allow a limiter failure to silently remove the limit.
            raise


class RedditClient:
    def __init__(self, creds: dict[str, str], limiter: RateLimiter | None = None):
        self.creds = creds
        self.limiter = limiter or RateLimiter()
        self.access_token = ''
        self.expires_at = 0.0

    def _request_json(self, request: urllib.request.Request) -> Any:
        for attempt in range(4):
            self.limiter.wait()
            try:
                with OPENER.open(request, timeout=TIMEOUT) as response:
                    remaining = response.headers.get('X-Ratelimit-Remaining')
                    reset = response.headers.get('X-Ratelimit-Reset')
                    result = json.load(response)
                    if remaining is not None and reset is not None:
                        try:
                            if float(remaining) < 1:
                                time.sleep(min(max(float(reset), 0), 60))
                        except ValueError:
                            pass
                    return result
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt < 3:
                    try:
                        retry = float(exc.headers.get('Retry-After', ''))
                    except ValueError:
                        retry = 2 ** (attempt + 1)
                    time.sleep(min(max(retry, 1), 60))
                    continue
                if exc.code in (301, 302, 303, 307, 308):
                    raise RedditError('REDIRECT_BLOCKED: Reddit 回應要求轉址') from None
                if exc.code in (401, 403):
                    raise RedditError(f'REDDIT_HTTP_{exc.code}: OAuth 權限或 API 准入被拒') from None
                raise RedditError(f'REDDIT_HTTP_{exc.code}: API 呼叫失敗') from None
            except (urllib.error.URLError, TimeoutError, ValueError):
                raise RedditError('NETWORK_OR_JSON: Reddit 回應不可用') from None
        raise RedditError('RATE_LIMITED: 重試上限已達')

    def refresh(self) -> None:
        client_id = self.creds['REDDIT_CLIENT_ID']
        secret = self.creds['REDDIT_CLIENT_SECRET']
        auth = base64.b64encode(f'{client_id}:{secret}'.encode()).decode()
        body = urllib.parse.urlencode({'grant_type': 'refresh_token',
                                       'refresh_token': self.creds['REDDIT_REFRESH_TOKEN']}).encode()
        request = urllib.request.Request(TOKEN_URL, body, method='POST', headers={
            'Authorization': f'Basic {auth}',
            'User-Agent': self.creds['REDDIT_USER_AGENT'],
            'Content-Type': 'application/x-www-form-urlencoded',
        })
        response = self._request_json(request)
        if not isinstance(response, dict) or not response.get('access_token'):
            raise RedditError('TOKEN_REFRESH_FAILED: Reddit 未回 access token')
        if set(str(response.get('scope', '')).split()) != {'read'}:
            raise RedditError('SCOPE_INVALID: token scope 非單獨 read')
        self.access_token = response['access_token']
        self.expires_at = time.time() + max(30, int(response.get('expires_in', 3600)) - 60)

    def get(self, path: str, params: dict[str, object] | None = None) -> Any:
        if time.time() >= self.expires_at or not self.access_token:
            self.refresh()
        query = dict(params or {})
        query['raw_json'] = 1
        url = API_ROOT + path + '?' + urllib.parse.urlencode(query)
        for attempt in range(2):
            request = urllib.request.Request(url, headers={
                'Authorization': f'bearer {self.access_token}',
                'User-Agent': self.creds['REDDIT_USER_AGENT'],
            })
            try:
                return self._request_json(request)
            except RedditError as exc:
                if 'REDDIT_HTTP_401' in str(exc) and attempt == 0:
                    self.refresh()
                    continue
                raise
        raise RedditError('TOKEN_REFRESH_FAILED: 重新整理後仍被拒')


def subreddit(value: str) -> str:
    value = value.removeprefix('r/')
    if not re.fullmatch(r'[A-Za-z0-9_]{2,21}', value):
        raise argparse.ArgumentTypeError('subreddit 名稱不合法')
    return value


def post_id(value: str) -> str:
    value = value.removeprefix('t3_')
    if not re.fullmatch(r'[a-z0-9]{4,12}', value):
        raise argparse.ArgumentTypeError('post ID 不合法；請用 base36 ID 或 t3_ID')
    return value


def compact_post(child: dict[str, Any]) -> dict[str, Any]:
    item = child.get('data', {})
    keys = ('id', 'subreddit', 'title', 'selftext', 'author', 'score', 'created_utc',
            'url', 'permalink', 'num_comments', 'over_18', 'spoiler')
    return {key: item.get(key) for key in keys}


def listing(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, dict) or data.get('kind') != 'Listing':
        raise RedditError('API_FORMAT: 預期 Listing')
    return [compact_post(x) for x in data.get('data', {}).get('children', [])
            if isinstance(x, dict) and x.get('kind') == 't3']


def post_with_comments(data: Any, limit: int) -> dict[str, Any]:
    if not isinstance(data, list) or len(data) < 2:
        raise RedditError('API_FORMAT: 預期貼文與留言兩個 Listing')
    posts = listing(data[0])
    if not posts:
        raise RedditError('API_FORMAT: 找不到貼文')
    if not isinstance(data[1], dict) or data[1].get('kind') != 'Listing':
        raise RedditError('API_FORMAT: 預期留言 Listing')
    comments = []
    for child in data[1].get('data', {}).get('children', []):
        if child.get('kind') != 't1':
            continue
        item = child['data']
        comments.append({key: item.get(key) for key in
                         ('id', 'parent_id', 'author', 'body', 'score', 'created_utc')})
        if len(comments) >= limit:
            break
    return {'post': posts[0], 'comments': comments}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description='Reddit official OAuth read-only JSON CLI')
    sub = p.add_subparsers(dest='command', required=True)
    for command in ('hot', 'new', 'top'):
        x = sub.add_parser(command)
        x.add_argument('subreddit', type=subreddit)
        x.add_argument('--limit', type=int, default=25)
        if command == 'top':
            x.add_argument('--time', choices=('hour', 'day', 'week', 'month', 'year', 'all'), default='day')
    x = sub.add_parser('search')
    x.add_argument('query')
    x.add_argument('--subreddit', type=subreddit)
    x.add_argument('--sort', choices=('relevance', 'hot', 'top', 'new', 'comments'), default='relevance')
    x.add_argument('--time', choices=('hour', 'day', 'week', 'month', 'year', 'all'), default='all')
    x.add_argument('--limit', type=int, default=25)
    x = sub.add_parser('post')
    x.add_argument('id', type=post_id)
    x.add_argument('--comments', type=int, default=10, help='first N top-level comments, max 100')
    return p


def run(args: argparse.Namespace, client: RedditClient) -> dict[str, Any]:
    if args.command == 'post':
        if not 1 <= args.comments <= 100:
            raise RedditError('ARGUMENT: comments 必須 1..100')
        data = client.get(f'/comments/{args.id}',
                          {'limit': args.comments, 'depth': 1, 'sort': 'top'})
        return {'ok': True, 'command': 'post', 'id': args.id,
                **post_with_comments(data, args.comments)}
    if not 1 <= args.limit <= 100:
        raise RedditError('ARGUMENT: limit 必須 1..100')
    if args.command == 'search':
        if not args.query or len(args.query) > 500:
            raise RedditError('ARGUMENT: query 長度必須 1..500')
        path = f'/r/{args.subreddit}/search' if args.subreddit else '/search'
        params = {'q': args.query, 'sort': args.sort, 't': args.time,
                  'limit': args.limit, 'restrict_sr': int(bool(args.subreddit))}
    else:
        path = f'/r/{args.subreddit}/{args.command}'
        params = {'limit': args.limit}
        if args.command == 'top':
            params['t'] = args.time
    data = client.get(path, params)
    return {'ok': True, 'command': args.command, 'items': listing(data)}


def main() -> int:
    args = parser().parse_args()
    try:
        result = run(args, RedditClient(load_credentials()))
    except RedditError as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, separators=(',', ':')))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
