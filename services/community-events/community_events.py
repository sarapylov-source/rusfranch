"""Community calendar in the existing aiogram dispatcher; no extra poller/webhook."""
import asyncio
import io
import json
import logging
import os
import tempfile
from datetime import datetime
from html import escape
from pathlib import Path

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter, TelegramNetworkError
from aiogram.types import InlineKeyboardButton as Button, InlineKeyboardMarkup as Markup
from events_store import Store, EventError, MODERATORS, GROUP, MASSHTAB, PUBLIC_URL, SUBSCRIBE, MSK, validate_field

log=logging.getLogger(__name__)
STATUS={'pending':'На модерации','approved':'Одобрено','rejected':'Отклонено','cancelled':'Отменено'}

def kb(*rows):
    return Markup(inline_keyboard=[[Button(text=t,callback_data=v) for t,v in row] for row in rows])

def links(*pairs):
    return Markup(inline_keyboard=[[Button(text=t,url=u)] for t,u in pairs])

def member(m):
    status=getattr(m.status,'value',m.status)
    return status in ('member','administrator','creator') or (status=='restricted' and bool(getattr(m,'is_member',False)))

def atomic(path, data):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.'+path.name,dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(data); f.flush(); os.fsync(f.fileno())
        os.chmod(name,0o644); os.replace(name,path)
    finally:
        if os.path.exists(name): os.unlink(name)

def clip(text, units):
    return str(text).encode('utf-16-le')[:units*2].decode('utf-16-le',errors='ignore')

def details(e, short=False):
    d=e['data']; day=datetime.fromisoformat(d['date']).strftime('%d.%m.%Y')
    price='Бесплатно' if d['fee']=='free' else (f'{d["amount"]:,} ₽'.replace(',',' ') if d.get('amount') else 'Стоимость уточняется')
    body=d['description']
    if short and len(body.encode('utf-16-le'))//2>300: body=clip(body,297).rsplit(' ',1)[0]+'…'
    return ('<b>'+escape(d['title'])+'</b>\n'+day+(' · '+d['time']+' МСК' if d.get('time') else '')+'\n'+('Онлайн' if d['format']=='online' else 'Офлайн · '+escape(d['location']))+' · '+price+'\n\n'+escape(body))

class Calendar:
    def __init__(self,bot,dispatcher,base,public):
        self.bot,self.dp=bot,dispatcher
        self.base=Path(base); self.public=Path(public)
        self.store=Store(self.base/'events.db')
        self.locks={}
        self.last_export=0

    async def send(self,uid,text,**kwargs):
        return await self.bot.send_message(uid,text,parse_mode='HTML',disable_web_page_preview=True,**kwargs)

    async def eligible(self,uid):
        try: return member(await self.bot.get_chat_member(MASSHTAB,uid))
        except TelegramBadRequest as ex:
            if any(x in str(ex).lower() for x in ('user not found','member not found','participant_id_invalid','user_id_invalid')): return False
            raise

    async def require_member(self,uid):
        if await self.eligible(uid): return True
        await self.send(uid,'Добавлять события могут участники закрытого чата «Масштаб»: подписчики, резиденты и проверенные подрядчики. Оформи подписку, вступи в чат и вернись к /add_event.',reply_markup=links(('Подписаться на «Масштаб»',SUBSCRIBE)))
        return False

    async def busy(self,uid):
        context=self.dp.fsm.get_context(bot=self.bot,chat_id=uid,user_id=uid)
        if await context.get_state(): return True
        award=getattr(self,'award',None)
        if award and award.enabled and award.store.session(uid): return True
        club=getattr(self,'club',None)
        current=club.current(uid) if club and club.enabled else None
        return bool(current and current['active'])

    async def start(self,user):
        if user.is_bot or not await self.require_member(user.id): return
        if await self.busy(user.id):
            await self.send(user.id,'Сначала заверши другую анкету в боте или отмени её командой /cancel. Затем открой /add_event.'); return
        if not self.store.session(user.id): self.store.begin(user.id,user.full_name)
        await self.prompt(user.id)

    async def prompt(self,uid):
        s=self.store.session(uid)
        if not s: return
        step=s['step']; n=s['nonce']
        cancel='\n\nАнкета сохраняется. /event_cancel — отменить, /add_event — продолжить.'
        choices={
            'source':('Это событие сообщества или партнёра?', [('Сообщества','community'),('Партнёра','partner')]),
            'format':('Как пройдёт событие?', [('Онлайн','online'),('Офлайн','offline')]),
            'fee':('Участие платное или бесплатное?', [('Бесплатно','free'),('Платно','paid')]),
        }
        if step in choices:
            title,rows=choices[step]
            await self.send(uid,title+cancel,reply_markup=kb(*[[(t,f'ev:field:{n}:{step}:{v}')] for t,v in rows])); return
        questions={
            'title':'Как называется событие? От 5 до 100 символов.',
            'date':'Когда состоится событие? Дата ДД.ММ.ГГГГ, при желании со временем: 24.11.2026 16:00. Время указывай по Москве.',
            'location':'Город и место проведения: адрес или название площадки (от 5 до 160 символов).',
            'amount':'Сколько стоит участие? Напиши целую сумму в рублях, например 3000.',
            'description':'Расскажи о событии: программа, для кого оно, кто организует. От 40 до 1800 символов. Этот текст будет опубликован после одобрения.',
            'url':'Пришли https:// ссылку на подробности или регистрацию. Если ссылки нет, отправь «-».',
            'poster':'Пришли афишу как фотографию (до 10 МБ). Если её нет, нажми «Без афиши».',
        }
        if step=='review':
            e={'data':s['data']}
            text=details(e)+'\n\n'+('Ссылка: '+escape(s['data']['url'])+'\n' if s['data'].get('url') else '')+'После одобрения Семёном или Ярославой событие появится в календаре, а анонс — в чате «Франчайзинг по-русски».\nПубликуем от твоего имени: '+escape(s['name'])+'.'
            if s['data'].get('poster_file_id'): await self.bot.send_photo(uid,s['data']['poster_file_id'])
            await self.send(uid,text,reply_markup=kb([('Отправить на модерацию',f'ev:submit:{n}')],[('Заполнить заново',f'ev:restart:{n}'),('Отменить',f'ev:cancel:{n}')])); return
        markup=kb([('Без афиши',f'ev:field:{n}:poster:skip')]) if step=='poster' else None
        await self.send(uid,questions[step]+cancel,reply_markup=markup)

    async def my_events(self,uid):
        rows=self.store.events(uid=uid)
        if not rows:
            await self.send(uid,'У тебя пока нет событий. /add_event — добавить.'); return
        for e in rows:
            text=f'№{e["id"]} · {STATUS[e["status"]]}\n'+details(e)
            if e.get('reason'): text+='\n\nПричина: '+escape(e['reason'])
            rows_k=[]
            if e['status'] in ('pending','approved'): rows_k.append([('Отменить событие',f'ev:withdraw_ask:{e["id"]}')])
            await self.send(uid,text,reply_markup=kb(*rows_k) if rows_k else None)

    async def admin(self,uid):
        if uid not in MODERATORS: raise EventError('Раздел доступен только модераторам.')
        pending=self.store.events(pending=True)
        await self.send(uid,f'Календарь: {len(pending)} заявок на модерации.\n/add_event — добавить, /my_events — свои события.\n/event_cancel ID — отменить событие.\n/event_retry JOB — повторить отправку после проверки отсутствия сообщения.\n/event_sent JOB MESSAGE_ID — подтвердить, что сообщение уже отправлено.\n\nПри неопределённой отправке сначала проверь чат: Telegram мог принять сообщение, даже если не вернул ответ. Повтор может создать дубль.')
        for e in pending: await self.review(e,uid)
        for j in self.store.issues():
            await self.send(uid,f'Требует проверки: задача {j["id"]}, событие №{j["event_id"]}, {j["kind"]}, {j["state"]}.\nОшибка: {escape(j["error"] or "—")}')

    async def review(self,e,uid):
        text=f'<b>Событие №{e["id"]} · {STATUS[e["status"]]}</b>\nАвтор: '+escape(e['creator_name'])+'\n\n'+details(e)
        if e['data'].get('url'): text+='\n\nСсылка: '+escape(e['data']['url'])
        if e['status']=='pending': markup=kb([('Одобрить',f'ev:approve:{e["id"]}'),('Отклонить',f'ev:reject:{e["id"]}')])
        else: markup=None
        if e['data'].get('poster_file_id'): await self.bot.send_photo(uid,e['data']['poster_file_id'],caption=f'Афиша события №{e["id"]}')
        return await self.send(uid,text,reply_markup=markup)

    async def command(self,msg):
        text=(msg.text or '').strip(); bits=text.split(); cmd=(bits[0].split('@')[0].lower() if bits else '')
        uid=msg.from_user.id
        if (cmd=='/start' and len(bits)>1 and bits[1] in ('event_add','calendar')) or cmd in ('/add_event','/addevent') or text.lower()=='добавить событие':
            await self.start(msg.from_user); return True
        if cmd=='/my_events': await self.my_events(uid); return True
        if cmd=='/calendar_admin': await self.admin(uid); return True
        if cmd in ('/event_retry','/event_sent'):
            if uid not in MODERATORS: raise EventError('Нет доступа.')
            if len(bits)!=(2 if cmd=='/event_retry' else 3) or not all(v.isdigit() for v in bits[1:]): raise EventError('Пример: /event_retry 1 или /event_sent 1 12345')
            jid=int(bits[1])
            if cmd=='/event_retry':
                await self.send(uid,'Проверь, что сообщение ещё не отправлено. Точно повторить задачу №'+str(jid)+'?',reply_markup=kb([('Проверил, повторить',f'ev:retry:{jid}')])); return True
            # Verify that operator supplied an actual bot message at the recorded destination.
            with self.store.db() as c: j=c.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
            if not j: raise EventError('Задача не найдена.')
            try:
                await self.bot.edit_message_reply_markup(chat_id=j['target'],message_id=int(bits[2]),reply_markup=self.announcement_kb(j['event_id']) if j['kind']=='announce' else None)
            except TelegramBadRequest as ex:
                if 'message is not modified' not in str(ex).lower(): raise
            self.store.resolve(jid,uid,'sent',int(bits[2])); await self.send(uid,'Отправка подтверждена.'); return True
        if cmd=='/event_cancel':
            if len(bits)==2 and bits[1].isdigit():
                eid=int(bits[1]); e=self.store.event(eid)
                if uid not in MODERATORS and uid!=e['creator']: raise EventError('Нет доступа.')
                await self.send(uid,'Отменить событие №'+str(eid)+' «'+escape(e['data']['title'])+'»?',reply_markup=kb([('Да, отменить',f'ev:withdraw:{eid}')]))
            else:
                self.store.clear(uid); await self.send(uid,'Черновик события отменён. /add_event — начать заново.')
            return True
        if self.store.session(uid) and cmd:
            self.store.clear(uid)
            if cmd=='/cancel': await self.send(uid,'Анкета события отменена.'); return True
            # Leave other modules' slash commands intact; a draft must not steal their text.
        return False

    async def private(self,msg):
        uid=msg.from_user.id; s=self.store.session(uid)
        if not s: return False
        key=f'm:{uid}:{msg.message_id}'
        if self.store.seen(key): return True
        step=s['step']; text=(msg.text or '').strip()
        if step in ('review','source','format','fee'): await self.prompt(uid); return True
        if msg.media_group_id: raise EventError('Пришли одну афишу отдельной фотографией, не альбомом.')
        if step=='poster':
            if not msg.photo: raise EventError('Пришли фотографию или нажми «Без афиши».')
            if (msg.photo[-1].file_size or 0)>10*1024*1024: raise EventError('Афиша должна быть до 10 МБ.')
            patch={'poster_file_id':msg.photo[-1].file_id,'poster':None}
        else:
            if not text: raise EventError('На этом шаге нужен текст.')
            patch=validate_field(step,text)
        self.store.advance(uid,step,patch,key); await self.prompt(uid); return True

    async def callback(self,cb):
        parts=(cb.data or '').split(':'); uid=cb.from_user.id
        if not cb.message or cb.message.chat.type!='private': raise EventError('Открой бота в личных сообщениях.')
        action=parts[1]
        if action in ('field','submit','restart','cancel'):
            s=self.store.session(uid)
            if not s or s['nonce']!=parts[2]: raise EventError('Эта кнопка уже обработана. /add_event — продолжить, /my_events — свои события.')
            if action=='field':
                _,_,n,step,value=parts
                patch={'poster_file_id':None,'poster':None} if step=='poster' and value=='skip' else validate_field(step,value)
                self.store.advance(uid,step,patch,'c:'+cb.id,n)
                await cb.answer(); await self.prompt(uid)
            elif action=='submit':
                if not await self.require_member(uid): await cb.answer(); return
                eid=self.store.submit(uid,parts[2]); await cb.answer('Отправлено')
                await self.send(uid,f'Событие №{eid} отправлено Семёну и Ярославе на модерацию. Сообщу о решении. /my_events — статус.')
            elif action=='restart':
                self.store.clear(uid); self.store.begin(uid,cb.from_user.full_name); await cb.answer(); await self.prompt(uid)
            else: self.store.clear(uid); await cb.answer(); await self.send(uid,'Черновик отменён.')
            return
        eid=int(parts[2])
        if action in ('approve','reject'):
            if uid not in MODERATORS: raise EventError('Решение могут принять только Семён или Ярослава.')
            e=self.store.event(eid)
            if e['status']!='pending': raise EventError('Решение уже принято.')
            if action=='reject':
                await cb.answer(); await self.send(uid,'Укажи причину отказа (один из вариантов):',reply_markup=kb([('Не подходит сообществу',f'ev:decline:{eid}:topic')],[('Недостаточно информации',f'ev:decline:{eid}:info')],[('Повтор события',f'ev:decline:{eid}:duplicate')])); return
            # Subscription may expire while the application is under review. Fail closed on lookup errors.
            if not await self.eligible(e['creator']): raise EventError('Автор больше не состоит в «Масштабе». Публикация не разрешена.')
            self.store.decide(eid,uid,True); await cb.answer('Одобрено'); await self.send(uid,'Событие одобрено. Публикация в календаре и анонс поставлены в очередь.')
        elif action=='decline':
            reasons={'topic':'Событие не соответствует тематике сообщества.','info':'Недостаточно информации о событии.','duplicate':'Это событие уже есть в календаре.'}
            self.store.decide(eid,uid,False,reasons[parts[3]]); await cb.answer('Отклонено')
        elif action=='withdraw_ask':
            e=self.store.event(eid)
            if uid not in MODERATORS and uid!=e['creator']: raise EventError('Нет доступа.')
            await cb.answer(); await self.send(uid,'Отменить событие «'+escape(e['data']['title'])+'»?',reply_markup=kb([('Да, отменить',f'ev:withdraw:{eid}')]))
        elif action=='withdraw':
            self.store.withdraw(eid,uid); await cb.answer('Отменено'); await self.send(uid,'Событие отменено и будет удалено из календаря.')
        elif action=='retry': self.store.resolve(eid,uid,'retry'); await cb.answer('Поставлено в очередь')
        else: raise EventError('Неизвестная кнопка.')

    def announcement_kb(self,eid):
        return links(('Событие в календаре',PUBLIC_URL+'#event-'+str(eid)),('Добавить своё событие','https://t.me/rusfranch_bot?start=event_add'))

    async def ensure_poster(self,e):
        d=e['data']; fid=d.get('poster_file_id')
        if not fid or d.get('poster'): return
        path=self.public/'posters'/f'event-{e["id"]}.jpg'
        info=await self.bot.get_file(fid)
        if (info.file_size or 0)>10*1024*1024: raise EventError('Афиша слишком большая.')
        buf=io.BytesIO(); await self.bot.download_file(info.file_path,destination=buf)
        def normalize():
            from PIL import Image, ImageOps
            Image.MAX_IMAGE_PIXELS=20000000
            with Image.open(buf) as im:
                im=ImageOps.exif_transpose(im); im.thumbnail((1800,1800)); im=im.convert('RGB')
                out=io.BytesIO(); im.save(out,'JPEG',quality=88,optimize=True); return out.getvalue()
        data=await asyncio.to_thread(normalize); atomic(path,data)
        d['poster']='https://api.frgu.ru/events/posters/'+path.name
        with self.store.db() as c:
            c.execute('UPDATE events SET data=? WHERE id=?',(json.dumps(d,ensure_ascii=False),e['id']))

    async def export(self):
        with self.store.db() as c:
            r=c.execute("SELECT value FROM settings WHERE key='export_dirty'").fetchone()
        if r and r['value']=='1' or not (self.public/'events.json').exists():
            with self.store.db() as c: revision=c.execute('SELECT COALESCE(max(id),0) FROM audit').fetchone()[0]
            # Export text first, so a broken/missing image never hides approved event information.
            payload=self.store.public(); atomic(self.public/'events.json',json.dumps(payload,ensure_ascii=False).encode())
            for e in self.store.events():
                if e['status']=='approved' and e['data'].get('poster_file_id') and not e['data'].get('poster'):
                    try: await self.ensure_poster(e)
                    except Exception as ex:
                        log.warning('Calendar poster export event=%s error=%s',e['id'],type(ex).__name__)
                        return # keep export dirty and retry; don't block announcement jobs
            atomic(self.public/'events.json',json.dumps(self.store.public(),ensure_ascii=False).encode())
            with self.store.db() as c:
                # Never clear a newer approval/cancellation that arrived during a download.
                if c.execute('SELECT COALESCE(max(id),0) FROM audit').fetchone()[0]==revision:
                    c.execute("INSERT OR REPLACE INTO settings VALUES('export_dirty','0')")

    async def execute(self,j):
        e=self.store.event(j['event_id']); kind=j['kind']; uid=j['target']
        if kind=='review':
            if e['status']!='pending': return None
            return await self.review(e,uid)
        if kind.startswith('review_update'):
            with self.store.db() as c: old=c.execute("SELECT message_id FROM jobs WHERE event_id=? AND kind='review' AND target=? AND state='done'",(e['id'],uid)).fetchone()
            if old and old['message_id']:
                try:
                    await self.bot.edit_message_reply_markup(chat_id=uid,message_id=old['message_id'],reply_markup=None)
                except TelegramBadRequest as ex:
                    if 'message is not modified' not in str(ex).lower(): raise
            return await self.send(uid,f'Событие №{e["id"]} «{escape(e["data"]["title"])}»: {STATUS[e["status"]]}.')
        if kind=='decision':
            return await self.send(uid,f'Событие №{e["id"]} «{escape(e["data"]["title"])}»: {STATUS[e["status"]]}.\n'+('Скоро появится в календаре: '+PUBLIC_URL+'#event-'+str(e['id']) if e['status']=='approved' else escape(e.get('reason') or '')))
        if kind=='announce':
            if e['status']!='approved': return None
            text='<b>Новое событие</b>\n\n'+details(e,short=bool(e['data'].get('poster_file_id')))+'\n\nОрганизатор: '+escape(clip(e['creator_name'],100))
            if e['data'].get('url'): text+='\n<a href="'+escape(e['data']['url'],quote=True)+'">Подробнее и регистрация</a>'
            if e['data'].get('poster_file_id'):
                return await self.bot.send_photo(uid,e['data']['poster_file_id'],caption=text,parse_mode='HTML',reply_markup=self.announcement_kb(e['id']))
            return await self.send(uid,text,reply_markup=self.announcement_kb(e['id']))
        if kind=='cancel_notice':
            return await self.send(uid,'Событие «'+escape(e['data']['title'])+'» отменено организатором. Актуальные события — в календаре.',reply_to_message_id=e['group_message'],reply_markup=links(('Календарь',PUBLIC_URL)))
        raise EventError('Неизвестная задача.')

    async def run_once(self):
        # One worker only. Independent of all other bot services and queues.
        now=asyncio.get_running_loop().time()
        if now-self.last_export>15:
            self.last_export=now
            try: await self.export()
            except Exception as ex: log.error('Calendar export failed: %s',type(ex).__name__)
        j=self.store.claim()
        if not j: return
        try:
            result=await self.execute(j)
            self.store.finish(j,message_id=result.message_id if result else None)
        except TelegramRetryAfter as ex:
            self.store.finish(j,'pending',error='rate_limit',delay=int(ex.retry_after)+1)
        except (TelegramForbiddenError,TelegramBadRequest) as ex:
            if isinstance(ex,TelegramBadRequest) and 'message is not modified' in str(ex).lower(): self.store.finish(j)
            else: self.store.finish(j,'failed',error=type(ex).__name__)
        except Exception as ex:
            # Network errors/timeouts may occur AFTER Telegram accepted the message.
            self.store.finish(j,'uncertain',error=type(ex).__name__)
            log.warning('Calendar delivery uncertain job=%s error=%s',j['id'],type(ex).__name__)
        # Issues stay durable and accessible even if notifying an administrator fails.
        if any(x['id']==j['id'] for x in self.store.issues()):
            for mod in MODERATORS:
                try: await self.send(mod,f'Календарь: задача №{j["id"]} требует проверки отправки. /calendar_admin')
                except Exception: log.warning('Calendar incident notification failed recipient=%s',mod)

    async def worker(self):
        while True:
            try: await self.run_once()
            except Exception as ex: log.error('Calendar worker recovered from %s',type(ex).__name__)
            await asyncio.sleep(2)

class CalendarMiddleware(BaseMiddleware):
    def __init__(self,calendar): self.calendar=calendar
    async def __call__(self,handler,event,data):
        a=self.calendar; cb=event.callback_query; msg=event.message
        if cb and (cb.data or '').startswith('ev:'):
            async with a.locks.setdefault(cb.from_user.id,asyncio.Lock()):
                try: await a.callback(cb)
                except EventError as ex: await cb.answer(str(ex)[:190],show_alert=True)
                except Exception as ex:
                    log.warning('Calendar callback failed: %s',type(ex).__name__)
                    try: await cb.answer('Не удалось проверить данные. Анкета сохранена, попробуй позже.',show_alert=True)
                    except Exception: pass
            return
        # Starting another module through its own buttons must not leave a calendar
        # draft intercepting that module's subsequent free-text answers.
        if cb and cb.message and cb.message.chat.type=='private' and (cb.data or '').startswith(('aw:','club:')):
            a.store.clear(cb.from_user.id)
        if msg and msg.chat.type=='private' and msg.from_user:
            async with a.locks.setdefault(msg.from_user.id,asyncio.Lock()):
                try:
                    if await a.command(msg): return
                    if await a.private(msg): return
                except EventError as ex: await a.send(msg.chat.id,escape(str(ex))); return
                except Exception as ex:
                    log.warning('Calendar form failed: %s',type(ex).__name__)
                    await a.send(msg.chat.id,'Не удалось обработать сообщение. Анкета сохранена. Попробуй позже.'); return
        # Always pass group messages, other callbacks, joins and poll answers through.
        return await handler(event,data)

def build(bot,dispatcher,base):
    service=Calendar(bot,dispatcher,base,'/var/www/frgu-events')
    service.store.init()
    dispatcher.update.outer_middleware(CalendarMiddleware(service))
    return service
