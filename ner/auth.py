"""Single-account authentication using FastAPI-Login and pwdlib."""

from collections import OrderedDict
from contextlib import closing, contextmanager
from datetime import timedelta
import getpass
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time

from fastapi_login import LoginManager
from pwdlib import PasswordHash

from .io_utils import atomic_output


COOKIE_NAME = 'ner_session'
SESSION_SECONDS = 12 * 3600
PASSWORDS = PasswordHash.recommended()


class AuthStore:
    def __init__(self, root):
        self.root = Path(root)
        self.manager = None
        self.attempts = OrderedDict()
        self.username = self.password_hash = None
        path = self.root / 'auth.json'
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            if (data['schema'] != 1 or not isinstance(data['username'], str)
                    or not data['username'] or not isinstance(data['secret'], str)
                    or len(data['secret']) < 32
                    or not isinstance(data['password_hash'], str)
                    or not data['password_hash'].startswith('$argon2id$')):
                raise ValueError('Invalid authentication configuration.')
            self.username, self.password_hash = data['username'], data['password_hash']
            self.manager = LoginManager(data['secret'], '/api/auth/login', use_cookie=True,
                                        cookie_name=COOKIE_NAME,
                                        default_expiry=timedelta(seconds=SESSION_SECONDS))
            self.manager.user_loader()(self.load_session)
            with self.database() as db:
                db.execute('CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, expires REAL NOT NULL)')
            (self.root / 'sessions.sqlite3').chmod(0o600)
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
            raise RuntimeError('Authentication configuration cannot be loaded. Run python -m ner.auth setup.') from exc

    @property
    def configured(self):
        return self.manager is not None

    @contextmanager
    def database(self):
        with closing(sqlite3.connect(self.root / 'sessions.sqlite3', timeout=5)) as db:
            with db:
                yield db

    @classmethod
    def provision(cls, root, username, password):
        username = username.strip()
        if not username or len(username) > 128 or not password or len(password) > 1024:
            raise ValueError('Use a username of 1–128 characters and a password of 1–1024 characters.')
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        data = {'schema': 1, 'username': username, 'password_hash': PASSWORDS.hash(password),
                'secret': secrets.token_urlsafe(48)}
        with atomic_output(root / 'auth.json') as path:
            path.write_text(json.dumps(data), encoding='utf-8')
            path.chmod(0o600)

    def allow_attempt(self, address):
        now = time.monotonic()
        recent = [stamp for stamp in self.attempts.pop(address, []) if now - stamp < 60]
        allowed = len(recent) < 5
        if allowed:
            recent.append(now)
        self.attempts[address] = recent
        if len(self.attempts) > 1024:
            self.attempts.popitem(last=False)
        return allowed

    def login(self, username, password, previous=None):
        # Verify even an unknown username, without exposing which field is wrong.
        matches = PASSWORDS.verify(password, self.password_hash)
        if not matches or not secrets.compare_digest(username.encode(), self.username.encode()):
            return None
        session_id = secrets.token_urlsafe(32)
        with self.database() as db:
            db.execute('DELETE FROM sessions WHERE expires <= ?', (time.time(),))
            if previous:
                db.execute('DELETE FROM sessions WHERE id = ?', (previous['session_id'],))
            db.execute('INSERT INTO sessions VALUES (?, ?)', (session_id, time.time() + SESSION_SECONDS))
        return self.manager.create_access_token(data={'sub': session_id})

    def load_session(self, session_id):
        if not isinstance(session_id, str) or len(session_id) > 128:
            return None
        with self.database() as db:
            found = db.execute('SELECT 1 FROM sessions WHERE id = ? AND expires > ?',
                               (session_id, time.time())).fetchone()
        return {'username': self.username, 'session_id': session_id} if found else None

    def logout(self, session_id):
        with self.database() as db:
            db.execute('DELETE FROM sessions WHERE id = ?', (session_id,))


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Create or replace the NER login account; restart the server afterward.')
    parser.add_argument('command', choices=['setup'])
    parser.add_argument('--storage', type=Path, default=Path(os.environ.get('NER_STORAGE_PATH', 'pdfstore/')))
    args = parser.parse_args()
    username = input('Username [admin]: ').strip() or 'admin'
    password = getpass.getpass('Password: ')
    if password != getpass.getpass('Confirm password: '):
        parser.error('Passwords do not match.')
    try:
        AuthStore.provision(args.storage, username, password)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print('Login account saved. Start or restart the NER server.')


if __name__ == '__main__':
    main()
