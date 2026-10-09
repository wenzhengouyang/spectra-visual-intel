"""Same-origin local accounts and private data. No third-party login dependency."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import re
import sqlite3
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from contextlib import contextmanager
from urllib.parse import urlsplit, unquote

EMPTY = {'interests': [], 'feedback': {}, 'favorites': [], 'history': []}
ROOT = Path(__file__).resolve().parents[1]


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    return salt + ':' + hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 600000).hex()


def validate_data(value):
    if not isinstance(value, dict) or set(value) != set(EMPTY):
        raise ValueError('invalid data fields')
    for key, limit, length in [('interests', 30, 80), ('favorites', 500, 120), ('history', 500, 120)]:
        items = value[key]
        if not isinstance(items, list) or len(items) > limit or any(not isinstance(x, str) or not x or len(x) > length for x in items):
            raise ValueError('invalid ' + key)
        value[key] = list(dict.fromkeys(items))
    if not isinstance(value['feedback'], dict) or len(value['feedback']) > 2000:
        raise ValueError('invalid feedback')
    if any(not k or len(k) > 120 or v not in ['useful', 'not_relevant'] for k, v in value['feedback'].items()):
        raise ValueError('invalid feedback')
    return value


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, name TEXT NOT NULL, data TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id TEXT NOT NULL, csrf TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS credentials(username TEXT PRIMARY KEY, password_hash TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS attempts(bucket TEXT PRIMARY KEY, count INTEGER NOT NULL, expires REAL NOT NULL);
            ''')
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def login(self, identity, name):
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute('DELETE FROM sessions WHERE expires<?', (time.time(),))
            db.execute('INSERT INTO users(id,name,data) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name', (identity, name[:100], json.dumps(EMPTY)))
            db.execute('INSERT INTO sessions VALUES(?,?,?,?)', (digest(token), identity, csrf, time.time()+7*86400))
        return token

    def session(self, token):
        with self.connect() as db:
            row = db.execute('SELECT u.*,s.csrf FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.expires>?', (digest(token), time.time())).fetchone()
            return dict(row) if row else None

    def logout(self, token):
        with self.connect() as db:
            db.execute('DELETE FROM sessions WHERE token=?', (digest(token),))

    def save(self, identity, revision, data):
        data = validate_data(data)
        with self.connect() as db:
            cursor = db.execute('UPDATE users SET data=?,revision=revision+1 WHERE id=? AND revision=?', (json.dumps(data, ensure_ascii=False), identity, revision))
            return cursor.rowcount == 1

    def allow_attempt(self, ip):
        with self.connect() as db:
            db.execute('DELETE FROM attempts WHERE expires<?', (time.time(),))
            bucket = digest(ip)
            db.execute('INSERT INTO attempts VALUES(?,1,?) ON CONFLICT(bucket) DO UPDATE SET count=count+1', (bucket, time.time()+900))
            return db.execute('SELECT count FROM attempts WHERE bucket=?', (bucket,)).fetchone()['count'] <= 20

    def register(self, username, password):
        encoded = password_hash(password)
        with self.connect() as db:
            try:
                db.execute('INSERT INTO credentials VALUES(?,?)', (username, encoded))
                db.execute('INSERT INTO users(id,name,data) VALUES(?,?,?)', ('local:'+username, username, json.dumps(EMPTY)))
            except sqlite3.IntegrityError:
                return False
        return True

    def verify_password(self, username, password):
        with self.connect() as db:
            row = db.execute('SELECT password_hash FROM credentials WHERE username=?', (username,)).fetchone()
        stored = row['password_hash'] if row else '0'*32 + ':' + '0'*64
        return secrets.compare_digest(password_hash(password, stored.split(':')[0]), stored)

    def change_password(self, username, password):
        with self.connect() as db:
            db.execute('UPDATE credentials SET password_hash=? WHERE username=?', (password_hash(password), username))
            db.execute('DELETE FROM sessions WHERE user_id=?', ('local:'+username,))


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, *args):
        pass  # Passwords, cookie values and private payloads must not enter logs.

    def cookie(self, name):
        try:
            cookies = SimpleCookie(self.headers.get('Cookie', ''))
            return cookies[name].value if name in cookies else ''
        except Exception:
            return ''

    def set_cookie(self, name, value, age):
        return f'{name}={value}; Path=/; Max-Age={age}; HttpOnly; SameSite=Lax' + ('; Secure' if self.server.base.startswith('https://') else '')

    def send(self, code, data=None, headers=()):
        body = json.dumps(data, ensure_ascii=False).encode() if data is not None else b''
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        for k, v in headers:
            self.send_header(k, v)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == '/api/account':
            session = self.server.store.session(self.cookie('spectra_session'))
            return self.send(200, {'configured': True, 'registration': self.server.registration, 'user': {'name': session['name'], 'role': 'member'} if session else None,
                                   'csrf': session['csrf'] if session else None, 'revision': session['revision'] if session else 0,
                                   'data': json.loads(session['data']) if session else None})
        relative = unquote(path).lstrip('/') or 'index.html'
        parts = Path(relative).parts
        from spectra_agent.run import STATIC_ASSETS
        allowed = relative == 'index.html' or relative in {str(p) for p in STATIC_ASSETS} or (parts and parts[0] in ['assets', 'archive'] and Path(relative).suffix in ['.html', '.css', '.js', '.png', '.jpg', '.jpeg', '.svg', '.webp'])
        target = (self.server.site / relative).resolve()
        if not allowed or '..' in parts or not target.is_relative_to(self.server.site) or not target.is_file():
            return self.send(404, {'error': 'not found'})
        import mimetypes
        body = target.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', (mimetypes.guess_type(str(target))[0] or 'application/octet-stream')+'; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        path = urlsplit(self.path).path
        if self.headers.get('Origin') != self.server.base:
            return self.send(403, {'error': '请求来源校验失败。'})
        if path in ['/api/account/login', '/api/account/register']:
            if not self.server.store.allow_attempt(self.client_address[0]):
                return self.send(429, {'error': '尝试次数过多，请 15 分钟后再试。'})
            try:
                payload = self.body(4096)
                username, password = payload['username'].strip().lower(), payload['password']
                if not re.fullmatch(r'[a-z0-9][a-z0-9_.-]{2,31}', username) or not isinstance(password, str) or not 12 <= len(password) <= 128:
                    raise ValueError()
            except (ValueError, TypeError, KeyError, AttributeError):
                return self.send(400, {'error': '用户名需为 3–32 位字母、数字或 ._-，密码需为 12–128 位。'})
            if path.endswith('/register'):
                if not self.server.registration:
                    return self.send(403, {'error': '暂未开放注册，请联系管理员。'})
                if not self.server.store.register(username, password):
                    return self.send(409, {'error': '该用户名已被使用。'})
            elif not self.server.store.verify_password(username, password):
                return self.send(401, {'error': '用户名或密码不正确。'})
            self.server.store.logout(self.cookie('spectra_session'))
            token = self.server.store.login('local:'+username, username)
            return self.send(200, {'ok': True}, [('Set-Cookie', self.set_cookie('spectra_session', token, 7*86400))])
        session = self.server.store.session(self.cookie('spectra_session'))
        if not session:
            return self.send(401, {'error': '请先登录。'})
        if self.headers.get('Origin') != self.server.base or not secrets.compare_digest(self.headers.get('X-CSRF-Token', ''), session['csrf']):
            return self.send(403, {'error': '请求校验失败。'})
        if path == '/api/account/logout':
            self.server.store.logout(self.cookie('spectra_session'))
            return self.send(200, {'ok': True}, [('Set-Cookie', self.set_cookie('spectra_session', '', 0))])
        if path == '/api/account/password':
            if not self.server.store.allow_attempt(self.client_address[0]):
                return self.send(429, {'error': '尝试次数过多，请稍后再试。'})
            try:
                payload = self.body(4096)
                old, new = payload['old_password'], payload['new_password']
                if not isinstance(old, str) or not isinstance(new, str) or not 12 <= len(new) <= 128 or len(old) > 128:
                    raise ValueError()
            except (ValueError, TypeError, KeyError):
                return self.send(400, {'error': '新密码需为 12–128 位。'})
            username = session['id'].removeprefix('local:')
            if not self.server.store.verify_password(username, old):
                return self.send(403, {'error': '原密码不正确。'})
            self.server.store.change_password(username, new)
            token = self.server.store.login(session['id'], session['name'])
            return self.send(200, {'ok': True}, [('Set-Cookie', self.set_cookie('spectra_session', token, 7*86400))])
        if path != '/api/account/data':
            return self.send(404, {'error': 'not found'})
        try:
            payload = self.body(262144)
            if type(payload.get('revision')) is not int:
                raise ValueError('invalid revision')
            saved = self.server.store.save(session['id'], payload['revision'], payload['data'])
        except (ValueError, TypeError, KeyError):
            return self.send(400, {'error': '数据格式不正确或超过容量限制。'})
        if not saved:
            return self.send(409, {'error': '另一设备已更新数据，请刷新后重试。'})
        return self.send(200, {'revision': payload['revision']+1})

    def body(self, maximum):
        length = int(self.headers.get('Content-Length', '0'))
        if not 0 < length <= maximum or self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            raise ValueError('invalid request')
        value = json.loads(self.rfile.read(length))
        if not isinstance(value, dict):
            raise ValueError('invalid payload')
        return value


def make_server(site, db, base, port, registration=False):
    parsed = urlsplit(base)
    if parsed.path not in ['', '/'] or parsed.query or parsed.fragment or parsed.username or parsed.password or not parsed.netloc:
        raise ValueError('base URL must be an origin without credentials or path')
    if parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in ['localhost', '127.0.0.1']):
        raise ValueError('HTTPS required outside localhost')
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.site, server.store, server.base, server.registration = Path(site).resolve(), Store(db), base.rstrip('/'), registration
    return server


if __name__ == '__main__':
    from spectra_agent.llm_client import load_local_env
    load_local_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8011)
    parser.add_argument('--site-dir', type=Path, default=ROOT)
    parser.add_argument('--allow-registration', action='store_true')
    args = parser.parse_args()
    base = os.environ.get('SPECTRA_ACCOUNT_ORIGIN') or f'http://127.0.0.1:{args.port}'
    db = os.environ.get('SPECTRA_ACCOUNT_DB') or str(Path.home()/'Library/Application Support/SPECTRA/data/accounts.sqlite3')
    server = make_server(args.site_dir, db, base, args.port, args.allow_registration)
    print('Account workbench: '+base, flush=True)
    server.serve_forever()
