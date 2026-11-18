"""Durable calendar workflow. Stdlib only; no Telegram calls inside transactions."""
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

MSK = timezone(timedelta(hours=3))
MODERATORS = (361153663, 997999396)
GROUP = -1002736798924
MASSHTAB = -1002982534919
PUBLIC_URL = 'https://www.frgu.ru/calendar.html'
SUBSCRIBE = 'https://t.me/tribute/app?startapp=sCed'
STEPS = ('source', 'title', 'date', 'format', 'location', 'fee', 'amount', 'description', 'url', 'poster', 'review')

class EventError(ValueError):
    pass

def parse_date(text, today=None):
    """Dates/times are explicitly Moscow local, never inferred from server TZ."""
    parts = text.strip().split()
    if not 1 <= len(parts) <= 2:
        raise EventError('Напиши дату ДД.ММ.ГГГГ, при желании — время: 24.11.2026 16:00 (МСК).')
    try:
        day = datetime.strptime(parts[0], '%d.%m.%Y').date()
        clock = datetime.strptime(parts[1], '%H:%M').strftime('%H:%M') if len(parts) == 2 else None
    except ValueError:
        raise EventError('Проверь дату и время. Пример: 24.11.2026 16:00. Время — московское.') from None
    today = today or datetime.now(MSK).date()
    if not today <= day <= today + timedelta(days=730):
        raise EventError('Нужна дата от сегодняшнего дня до двух лет вперёд.')
    return {'date': day.isoformat(), 'time': clock, 'end_date': day.isoformat()}

def safe_url(text):
    try:
        u = urlsplit(text)
        if u.scheme != 'https' or not u.hostname or u.username or u.password or any(c.isspace() for c in text):
            raise ValueError()
        if len(text) > 350:
            raise ValueError()
    except ValueError:
        raise EventError('Нужна ссылка с https:// без пробелов, до 350 символов.') from None
    return text

def validate_field(step, value):
    limits = {'title': (5,100), 'location': (5,160), 'description': (40,1800)}
    if step in limits:
        lo, hi = limits[step]
        if not lo <= len(value.strip().encode('utf-16-le')) // 2 <= hi:
            raise EventError(f'Нужно от {lo} до {hi} символов.')
        return {step: value.strip()}
    if step == 'source':
        if value not in ('community', 'partner'): raise EventError('Выбери организатора кнопкой.')
        return {'source': value}
    if step == 'date': return parse_date(value)
    if step == 'format':
        if value not in ('online', 'offline'): raise EventError('Выбери формат кнопкой.')
        return {'format': value, 'location': 'Онлайн' if value == 'online' else ''}
    if step == 'fee':
        if value not in ('free', 'paid'): raise EventError('Выбери условия кнопкой.')
        return {'fee': value, 'amount': None}
    if step == 'amount':
        try:
            amount = int(value.replace(' ', '').replace('₽', ''))
            if not 1 <= amount <= 10000000: raise ValueError()
        except ValueError: raise EventError('Укажи целую сумму в рублях, например 3000.') from None
        return {'amount': amount}
    if step == 'url': return {'url': None if value == '-' else safe_url(value)}
    raise EventError('Неизвестный шаг анкеты.')

def next_step(step, data):
    if step == 'format': return 'fee' if data['format'] == 'online' else 'location'
    if step == 'fee': return 'amount' if data['fee'] == 'paid' else 'description'
    return STEPS[STEPS.index(step) + 1]

class Store:
    def __init__(self, path, clock=time.time):
        self.path, self.clock = str(path), clock

    @contextmanager
    def db(self):
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA busy_timeout=10000')
        try:
            with c: yield c
        finally: c.close()

    def init(self):
        with self.db() as c:
            c.execute('PRAGMA journal_mode=WAL')
            c.executescript('''
                CREATE TABLE IF NOT EXISTS drafts (
                    user_id INTEGER PRIMARY KEY, name TEXT NOT NULL, step TEXT NOT NULL,
                    data TEXT NOT NULL, nonce TEXT NOT NULL, updated INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, seed_key TEXT UNIQUE,
                    creator INTEGER, creator_name TEXT NOT NULL, status TEXT NOT NULL,
                    data TEXT NOT NULL, created INTEGER NOT NULL,
                    decided_by INTEGER, decided_at INTEGER, reason TEXT,
                    group_message INTEGER);
                CREATE TABLE IF NOT EXISTS receipts (key TEXT PRIMARY KEY, ts INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, job_key TEXT UNIQUE NOT NULL,
                    event_id INTEGER NOT NULL, kind TEXT NOT NULL, target INTEGER NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                    next_try INTEGER NOT NULL DEFAULT 0, message_id INTEGER, error TEXT);
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, actor INTEGER,
                    event_id INTEGER, action TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS jobs_ready ON jobs(state,next_try);
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            ''')
            # A crash after a send but before recording its result must never resend blindly.
            c.execute("UPDATE jobs SET state='uncertain',error='process_restarted_during_send' WHERE state='sending'")

        os.chmod(self.path, 0o600)

    def audit(self, c, actor, eid, action):
        c.execute('INSERT INTO audit(ts,actor,event_id,action) VALUES(?,?,?,?)', (int(self.clock()), actor, eid, action))

    def enqueue(self, c, eid, kind, target):
        c.execute('INSERT OR IGNORE INTO jobs(job_key,event_id,kind,target) VALUES(?,?,?,?)', (f'{eid}:{kind}:{target}',eid,kind,target))

    def session(self, uid):
        with self.db() as c: r = c.execute('SELECT * FROM drafts WHERE user_id=?',(uid,)).fetchone()
        if not r: return None
        result = dict(r); result['data'] = json.loads(result['data']); return result

    def begin(self, uid, name):
        with self.db() as c:
            if c.execute("SELECT count(*) FROM events WHERE creator=? AND status='pending'",(uid,)).fetchone()[0] >= 3:
                raise EventError('У тебя уже три события на модерации. Дождись решения.')
            c.execute('INSERT OR REPLACE INTO drafts VALUES(?,?,?,?,?,?)', (uid,name,'source' if uid in MODERATORS else 'title',json.dumps({'source':'community' if uid in MODERATORS else 'partner'}),secrets.token_hex(5),int(self.clock())))
        return self.session(uid)

    def clear(self, uid):
        with self.db() as c: c.execute('DELETE FROM drafts WHERE user_id=?',(uid,))

    def advance(self, uid, step, patch, receipt, nonce=None):
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            r = c.execute('SELECT * FROM drafts WHERE user_id=?',(uid,)).fetchone()
            if not r or r['step'] != step or (nonce and nonce != r['nonce']): raise EventError('Эта кнопка устарела. Продолжи текущую анкету: /add_event')
            if c.execute('SELECT 1 FROM receipts WHERE key=?',(receipt,)).fetchone(): return
            d = json.loads(r['data']); d.update(patch)
            c.execute('UPDATE drafts SET step=?,data=?,nonce=?,updated=? WHERE user_id=?', (next_step(step,d),json.dumps(d,ensure_ascii=False),secrets.token_hex(5),int(self.clock()),uid))
            c.execute('INSERT INTO receipts VALUES(?,?)',(receipt,int(self.clock())))

    def seen(self, receipt):
        with self.db() as c: return bool(c.execute('SELECT 1 FROM receipts WHERE key=?',(receipt,)).fetchone())

    def submit(self, uid, nonce):
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            r = c.execute('SELECT * FROM drafts WHERE user_id=?',(uid,)).fetchone()
            if not r or r['nonce'] != nonce or r['step'] != 'review': raise EventError('Анкета уже отправлена или кнопка устарела. /my_events — твои события.')
            d = json.loads(r['data'])
            if date.fromisoformat(d['date']) < datetime.now(MSK).date(): raise EventError('Дата события уже прошла. Заполни анкету заново: /event_cancel, затем /add_event.')
            # Content fingerprint prevents accidental repeat submission after a restart.
            old = c.execute("SELECT id FROM events WHERE creator=? AND data=? AND status IN ('pending','approved')", (uid,r['data'])).fetchone()
            if old: raise EventError(f'Такое событие уже есть: №{old["id"]}.')
            eid = c.execute("INSERT INTO events(creator,creator_name,status,data,created) VALUES(?,?,'pending',?,?)", (uid,r['name'],r['data'],int(self.clock()))).lastrowid
            for mod in MODERATORS: self.enqueue(c,eid,'review',mod)
            c.execute('DELETE FROM drafts WHERE user_id=?',(uid,))
            self.audit(c,uid,eid,'submitted')
            return eid

    def event(self, eid):
        with self.db() as c: r = c.execute('SELECT * FROM events WHERE id=?',(eid,)).fetchone()
        if not r: raise EventError('Событие не найдено.')
        r = dict(r); r['data'] = json.loads(r['data']); return r

    def decide(self, eid, actor, approve, reason=None):
        if actor not in MODERATORS: raise EventError('Решение могут принять только Семён или Ярослава.')
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            r = c.execute('SELECT * FROM events WHERE id=?',(eid,)).fetchone()
            if not r: raise EventError('Событие не найдено.')
            if r['status'] != 'pending': raise EventError('Решение по этому событию уже принято.')
            d = json.loads(r['data'])
            if approve and date.fromisoformat(d['date']) < datetime.now(MSK).date(): raise EventError('Дата события прошла. Отклони заявку и попроси автора уточнить дату.')
            c.execute('UPDATE events SET status=?,decided_by=?,decided_at=?,reason=? WHERE id=?', ('approved' if approve else 'rejected',actor,int(self.clock()),reason,eid))
            if approve:
                self.enqueue(c,eid,'announce',GROUP)
                c.execute("INSERT OR REPLACE INTO settings VALUES('export_dirty','1')")
            self.enqueue(c,eid,'decision',r['creator'])
            for mod in MODERATORS: self.enqueue(c,eid,'review_update',mod)
            self.audit(c,actor,eid,'approved' if approve else 'rejected')

    def withdraw(self, eid, actor):
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            r=c.execute('SELECT * FROM events WHERE id=?',(eid,)).fetchone()
            if not r or (actor not in MODERATORS and actor != r['creator']): raise EventError('Нет доступа к этому событию.')
            if r['status'] not in ('pending','approved'): raise EventError('Это событие уже закрыто.')
            # Do not race a potentially in-flight publication.
            if c.execute("SELECT 1 FROM jobs WHERE event_id=? AND kind='announce' AND state IN ('sending','uncertain')",(eid,)).fetchone(): raise EventError('Сначала проверь незавершённую публикацию: /calendar_admin')
            c.execute("UPDATE events SET status='cancelled',decided_by=?,decided_at=? WHERE id=?",(actor,int(self.clock()),eid))
            c.execute("UPDATE jobs SET state='cancelled' WHERE event_id=? AND state='pending' AND kind IN ('announce','review')",(eid,))
            c.execute("INSERT OR REPLACE INTO settings VALUES('export_dirty','1')")
            for mod in MODERATORS: self.enqueue(c,eid,'review_update_cancelled',mod)
            if r['group_message']: self.enqueue(c,eid,'cancel_notice',GROUP)
            self.audit(c,actor,eid,'cancelled')

    def events(self, uid=None, pending=False):
        with self.db() as c:
            if pending: rows=c.execute("SELECT * FROM events WHERE status='pending' ORDER BY id").fetchall()
            elif uid: rows=c.execute('SELECT * FROM events WHERE creator=? ORDER BY id DESC LIMIT 30',(uid,)).fetchall()
            else: rows=c.execute('SELECT * FROM events ORDER BY id').fetchall()
        return [{**dict(r),'data':json.loads(r['data'])} for r in rows]

    def seed(self, rows):
        with self.db() as c:
            for d in rows:
                key = d['seed_key']; data = dict(d); data.pop('id',None)
                c.execute("INSERT OR IGNORE INTO events(seed_key,creator_name,status,data,created) VALUES(?,'Франчайзинг по-русски','approved',?,?)",(key,json.dumps(data,ensure_ascii=False),int(self.clock())))
            c.execute("INSERT OR REPLACE INTO settings VALUES('export_dirty','1')")

    def public(self):
        result=[]
        for e in self.events():
            if e['status'] != 'approved': continue
            d=e['data']
            # Explicit allowlist: no Telegram IDs, file IDs or private moderation details.
            p={k:d.get(k) for k in ('title','date','end_date','time','format','location','fee','amount','description','url','source','poster')}
            p.update(id=e['id'],organizer=e['creator_name'])
            result.append(p)
        return {'version':1,'updated_at':datetime.now(timezone.utc).isoformat(),'timezone':'Europe/Moscow','events':sorted(result,key=lambda e:(e['date'],e['time'] or '',e['id']))}

    def claim(self):
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            r=c.execute("SELECT * FROM jobs WHERE state='pending' AND next_try<=? ORDER BY id LIMIT 1",(int(self.clock()),)).fetchone()
            if not r: return None
            c.execute("UPDATE jobs SET state='sending',attempts=attempts+1 WHERE id=?",(r['id'],))
            return dict(r)

    def finish(self, job, state='done', message_id=None, error=None, delay=0):
        with self.db() as c:
            c.execute('UPDATE jobs SET state=?,message_id=COALESCE(?,message_id),error=?,next_try=? WHERE id=?',(state,message_id,error,int(self.clock())+delay,job['id']))
            if state=='done' and job['kind']=='announce' and message_id:
                c.execute('UPDATE events SET group_message=? WHERE id=?',(message_id,job['event_id']))

    def issues(self):
        with self.db() as c: return [dict(r) for r in c.execute("SELECT * FROM jobs WHERE state IN ('uncertain','failed') ORDER BY id")]

    def resolve(self, jid, actor, action, message_id=None):
        if action not in ('retry','sent'): raise EventError('Неизвестное действие.')
        if actor not in MODERATORS: raise EventError('Нет доступа.')
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            j=c.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
            if not j or j['state'] not in ('uncertain','failed'): raise EventError('Эта задача уже обработана.')
            if action=='sent' and not message_id: raise EventError('Укажи ID реально отправленного сообщения.')
            c.execute('UPDATE jobs SET state=?,message_id=?,next_try=0,error=NULL WHERE id=?',('done' if action=='sent' else 'pending',message_id,jid))
            if action=='sent' and j['kind']=='announce': c.execute('UPDATE events SET group_message=? WHERE id=?',(message_id,j['event_id']))
            self.audit(c,actor,j['event_id'],f'job_{action}:{jid}')
