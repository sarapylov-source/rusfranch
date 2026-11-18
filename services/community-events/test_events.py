import asyncio
import json
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

from events_store import Store, EventError, MODERATORS, validate_field, parse_date, next_step

FUTURE=(date.today()+timedelta(days=30)).isoformat()
def data():
    return dict(title='Полезный вебинар для франчайзеров',date=FUTURE,end_date=FUTURE,time='16:00',format='online',location='Онлайн',fee='free',amount=None,description='Программа вебинара: продажи, управление командой и запуск новых точек франшизы.',url='https://example.org/event',source='partner',poster=None,poster_file_id=None)

class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.path=Path(self.tmp.name)/'events.db'; self.s=Store(self.path); self.s.init()
    def tearDown(self): self.tmp.cleanup()
    def submitted(self):
        self.s.begin(123,'Организатор')
        with self.s.db() as c:c.execute("UPDATE drafts SET step='review',data=? WHERE user_id=123",(json.dumps(data()),))
        return self.s.submit(123,self.s.session(123)['nonce'])
    def test_submit_only_moderator_notifications(self):
        eid=self.submitted()
        with self.s.db() as c:j=list(c.execute('SELECT * FROM jobs'))
        self.assertEqual({r['target'] for r in j},set(MODERATORS));self.assertTrue(all(r['kind']=='review' for r in j));self.assertEqual(self.s.public()['events'],[])
    def test_approval_once(self):
        eid=self.submitted();self.s.decide(eid,MODERATORS[0],True)
        with self.assertRaises(EventError):self.s.decide(eid,MODERATORS[1],True)
        with self.s.db() as c:self.assertEqual(c.execute("SELECT count(*) FROM jobs WHERE kind='announce'").fetchone()[0],1)
        self.assertEqual(len(self.s.public()['events']),1)
    def test_reject_never_public(self):
        eid=self.submitted();self.s.decide(eid,MODERATORS[1],False,'Мало информации')
        self.assertFalse(self.s.public()['events'])
        with self.s.db() as c:self.assertFalse(c.execute("SELECT 1 FROM jobs WHERE kind='announce'").fetchone())
    def test_unauthorized_moderation(self):
        eid=self.submitted()
        with self.assertRaises(EventError):self.s.decide(eid,222,True)
        self.assertEqual(self.s.event(eid)['status'],'pending')
    def test_sessions_persist_and_stale_buttons_fail(self):
        s=self.s.begin(123,'Имя');self.s.advance(123,'title',{'title':'Заголовок'},'m:1',s['nonce'])
        other=Store(self.path);self.assertEqual(other.session(123)['step'],'date')
        with self.assertRaises(EventError):other.advance(123,'title',{},'m:2',s['nonce'])
    def test_receipts(self):
        self.s.begin(123,'Имя');self.s.advance(123,'title',{'title':'Заголовок'},'unique')
        self.assertTrue(self.s.seen('unique'))
    def test_no_duplicate_seed_or_announcements(self):
        d=data();d['source']='community';d['seed_key']='one';self.s.seed([d]);self.s.seed([d])
        self.assertEqual(len(self.s.public()['events']),1);self.assertIsNone(self.s.claim())
    def test_no_private_data_in_public_export(self):
        eid=self.submitted();self.s.decide(eid,MODERATORS[0],True)
        encoded=json.dumps(self.s.public())
        for key in ('creator','poster_file_id','decided_by','reason','nonce'):self.assertNotIn('"'+key+'"',encoded)
    def test_crash_during_send_not_resent(self):
        self.submitted();j=self.s.claim();self.assertEqual(j['kind'],'review')
        s=Store(self.path);s.init();self.assertEqual(s.issues()[0]['id'],j['id'])
        self.assertNotEqual(s.claim()['id'],j['id'])
    def test_safe_retry_and_confirm(self):
        self.submitted();j=self.s.claim();self.s.finish(j,'uncertain',error='timeout')
        self.s.resolve(j['id'],MODERATORS[0],'retry');self.assertEqual(self.s.claim()['id'],j['id'])
        self.s.finish(j,'uncertain');self.s.resolve(j['id'],MODERATORS[1],'sent',456)
        self.assertFalse(self.s.issues())
    def test_cancellation_removes_event_and_pending_send(self):
        eid=self.submitted();self.s.decide(eid,MODERATORS[0],True);self.s.withdraw(eid,123)
        self.assertFalse(self.s.public()['events'])
        with self.s.db() as c:self.assertEqual(c.execute("SELECT state FROM jobs WHERE kind='announce'").fetchone()[0],'cancelled')
    def test_cannot_cancel_during_uncertain_announcement(self):
        eid=self.submitted();self.s.decide(eid,MODERATORS[0],True)
        with self.s.db() as c:c.execute("UPDATE jobs SET state='uncertain' WHERE kind='announce'")
        with self.assertRaises(EventError):self.s.withdraw(eid,123)
    def test_date_validation(self):
        with self.assertRaises(EventError):parse_date('31.02.2027')
        with self.assertRaises(EventError):parse_date('01.01.2020')
        day=(date.today()+timedelta(days=10)).strftime('%d.%m.%Y');self.assertEqual(parse_date(day+' 16:30')['time'],'16:30')
    def test_url_security(self):
        for val in ('javascript:alert(1)','http://example.com','https://user:pass@example.com','https://example.com/ bad'):
            with self.assertRaises(EventError):validate_field('url',val)
    def test_fee_positive_amount(self):
        for val in ('0','-5','1.5','10000001'):
            with self.assertRaises(EventError):validate_field('amount',val)
        self.assertEqual(validate_field('amount','3 000')['amount'],3000)
    def test_utf16_limits(self):
        with self.assertRaises(EventError):validate_field('title','🦄'*60)
    def test_skip_paths(self):
        self.assertEqual(next_step('format',{'format':'online'}),'fee');self.assertEqual(next_step('fee',{'fee':'free'}),'description')

class IntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from community_events import Calendar
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name)
        self.bot=NS(send_message=AsyncMock(return_value=NS(message_id=100)),send_photo=AsyncMock(return_value=NS(message_id=101)),get_chat_member=AsyncMock(return_value=NS(status='member')),edit_message_reply_markup=AsyncMock(return_value=True))
        self.dp=NS(fsm=NS(get_context=lambda **kw:NS(get_state=AsyncMock(return_value=None))))
        self.a=Calendar(self.bot,self.dp,self.base,self.base/'public');self.a.store.init()
        self.user=NS(id=123,full_name='Тестовый Автор',is_bot=False)
    async def asyncTearDown(self):self.tmp.cleanup()
    def msg(self,text='',photo=None,mid=1):return NS(from_user=self.user,chat=NS(id=123,type='private'),text=text,photo=photo,message_id=mid,media_group_id=None)
    def cb(self,data,user=None):return NS(data=data,from_user=user or self.user,message=NS(chat=NS(id=(user or self.user).id,type='private')),id='cb-'+data,answer=AsyncMock())
    async def fill(self):
        await self.a.start(self.user)
        async def text(value,mid):await self.a.private(self.msg(value,mid=mid))
        async def choice(value):
            s=self.a.store.session(123);await self.a.callback(self.cb(f'ev:field:{s["nonce"]}:{s["step"]}:{value}'))
        await text('Практический вебинар',1);await text((date.today()+timedelta(days=30)).strftime('%d.%m.%Y')+' 16:00',2)
        await choice('online');await choice('free');await text('Программа вебинара: продажи, управление командой и запуск новых точек франшизы.',3);await text('https://example.org/event',4);await choice('skip')
        return self.a.store.session(123)
    async def test_member_form_submit_moderate_export_announce(self):
        s=await self.fill();await self.a.callback(self.cb('ev:submit:'+s['nonce']))
        eid=self.a.store.events()[0]['id'];self.assertIsNone(self.a.store.session(123))
        await self.a.run_once();await self.a.run_once() # Both moderation cards delivered before approval.
        mod=NS(id=MODERATORS[0]);await self.a.callback(self.cb('ev:approve:'+str(eid),mod))
        await self.a.export();payload=json.loads((self.base/'public/events.json').read_text());self.assertEqual(len(payload['events']),1)
        for _ in range(10):await self.a.run_once()
        group=[c for c in self.bot.send_message.call_args_list if c.args[0]==-1002736798924]
        self.assertEqual(len(group),1);self.assertIn('Новое событие',group[0].args[1]);self.assertEqual(self.a.store.issues(),[])
    async def test_nonmember_cannot_start(self):
        self.bot.get_chat_member.return_value=NS(status='left');await self.a.start(self.user)
        self.assertIsNone(self.a.store.session(123));self.assertIn('Оформи подписку',self.bot.send_message.call_args.args[1])
    async def test_member_left_cannot_submit(self):
        s=await self.fill();self.bot.get_chat_member.return_value=NS(status='left');await self.a.callback(self.cb('ev:submit:'+s['nonce']))
        self.assertEqual(self.a.store.events(),[]);self.assertIsNotNone(self.a.store.session(123))
    async def test_member_left_cannot_be_approved(self):
        s=await self.fill();await self.a.callback(self.cb('ev:submit:'+s['nonce']));eid=self.a.store.events()[0]['id'];self.bot.get_chat_member.return_value=NS(status='left')
        with self.assertRaises(EventError):await self.a.callback(self.cb('ev:approve:'+str(eid),NS(id=MODERATORS[0])))
        self.assertEqual(self.a.store.event(eid)['status'],'pending')
    async def test_group_and_other_updates_pass_through(self):
        from community_events import CalendarMiddleware
        handler=AsyncMock(return_value='passed');event=NS(callback_query=None,message=NS(chat=NS(type='supergroup')))
        result=await CalendarMiddleware(self.a)(handler,event,{})
        self.assertEqual(result,'passed');handler.assert_awaited_once()
    async def test_network_uncertain_not_auto_retried(self):
        from aiogram.exceptions import TelegramNetworkError
        from aiogram.methods import SendMessage
        s=await self.fill();self.a.store.submit(123,s['nonce'])
        self.bot.send_message.side_effect=TelegramNetworkError(method=SendMessage(chat_id=123,text='test'),message='timeout')
        await self.a.run_once();issue=self.a.store.issues()[0];self.assertEqual(issue['state'],'uncertain')
        await self.a.run_once();self.assertEqual(self.a.store.issues()[0]['attempts'],1)
    async def test_safe_html_and_caption_size(self):
        from community_events import details
        from html import unescape
        import re
        d=data();d.update(title='<script>alert(1)</script>',description='x'*1800)
        self.assertIn('&lt;script&gt;',details({'data':d}))
        self.assertLess(len(unescape(re.sub('<[^>]+>','',details({'data':d},short=True))).encode('utf-16-le'))//2,800)


class PhotoAndRecoveryTest(IntegrationTest):
    async def test_photo_export_is_normalized_and_public_safe(self):
        import io
        from PIL import Image
        s=await self.fill();d=s['data'];d['poster_file_id']='PRIVATE_FILE_ID'
        with self.a.store.db() as c:c.execute('UPDATE drafts SET data=? WHERE user_id=123',(json.dumps(d),))
        eid=self.a.store.submit(123,s['nonce']);self.a.store.decide(eid,MODERATORS[0],True)
        buf=io.BytesIO();Image.new('RGB',(2400,1500),'red').save(buf,'PNG');raw=buf.getvalue()
        self.bot.get_file=AsyncMock(return_value=NS(file_path='private/path.png',file_size=len(raw)))
        async def download(path,destination):destination.write(raw)
        self.bot.download_file=download
        await self.a.export()
        image=self.base/'public/posters'/f'event-{eid}.jpg'
        with Image.open(image) as im:self.assertEqual(im.format,'JPEG');self.assertEqual(im.size,(1800,1125))
        payload=(self.base/'public/events.json').read_text();self.assertNotIn('PRIVATE_FILE_ID',payload);self.assertIn(f'event-{eid}.jpg',payload)
    async def test_approval_during_export_keeps_dirty_flag(self):
        s=await self.fill();eid=self.a.store.submit(123,s['nonce']);self.a.store.decide(eid,MODERATORS[0],True)
        d=self.a.store.event(eid)['data'];d['poster_file_id']='photo'
        with self.a.store.db() as c:c.execute('UPDATE events SET data=? WHERE id=?',(json.dumps(d),eid))
        async def concurrent_cancel(e):self.a.store.withdraw(eid,123)
        self.a.ensure_poster=concurrent_cancel
        await self.a.export()
        with self.a.store.db() as c:self.assertEqual(c.execute("SELECT value FROM settings WHERE key='export_dirty'").fetchone()[0],'1')
        self.assertEqual(json.loads((self.base/'public/events.json').read_text())['events'],[])
    async def test_moderator_can_add_community_event(self):
        self.a.store.begin(MODERATORS[0],'Модератор')
        s=self.a.store.session(MODERATORS[0]);self.assertEqual(s['step'],'source')
        self.a.store.advance(MODERATORS[0],'source',{'source':'community'},'m:mod:1',s['nonce'])
        self.assertEqual(self.a.store.session(MODERATORS[0])['step'],'title')
    async def test_restricted_nonmember_denied(self):
        self.bot.get_chat_member.return_value=NS(status='restricted',is_member=False)
        await self.a.start(self.user);self.assertIsNone(self.a.store.session(123))



    async def test_existing_module_sessions_are_respected(self):
        self.a.award=NS(enabled=True,store=NS(session=lambda uid: {'step':'company'}))
        await self.a.start(self.user);self.assertIsNone(self.a.store.session(123))
        self.assertIn('другую анкету',self.bot.send_message.call_args.args[1])
        self.a.award.enabled=False
        await self.a.start(self.user);self.assertIsNotNone(self.a.store.session(123))
    async def test_other_feature_callback_releases_own_draft(self):
        from community_events import CalendarMiddleware
        self.a.store.begin(123,'Имя');handler=AsyncMock(return_value='next')
        cb=self.cb('aw:nominate');event=NS(callback_query=cb,message=None)
        self.assertEqual(await CalendarMiddleware(self.a)(handler,event,{}),'next')
        self.assertIsNone(self.a.store.session(123))

if __name__=='__main__':unittest.main()
