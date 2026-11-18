/* Public, read-only calendar. All publication and membership checks happen in the bot. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const API = 'https://api.frgu.ru/events/events.json';
  const BOT = 'https://t.me/rusfranch_bot?start=event_add';
  const months = ['Январь','Февраль','Март','Апрель','Май','Июнь','Июль','Август','Сентябрь','Октябрь','Ноябрь','Декабрь'];
  // en-CA date ordering is not guaranteed in all browser versions.
  const parts = new Intl.DateTimeFormat('en', {timeZone:'Europe/Moscow',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date());
  const part = key => parts.find(p => p.type === key).value;
  const todayISO = `${part('year')}-${part('month')}-${part('day')}`;
  let year = +part('year'), month = +part('month')-1;
  let events = [], fallback = false, loading = true, view = matchMedia('(max-width:640px)').matches ? 'list' : 'grid';
  const filters = {source:new Set(),format:new Set(),fee:new Set()};
  const dateISO = d => `${d.getUTCFullYear()}-${String(d.getUTCMonth()+1).padStart(2,'0')}-${String(d.getUTCDate()).padStart(2,'0')}`;
  const validDate = s => typeof s === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(s) && !Number.isNaN(Date.parse(s)) && dateISO(new Date(s+'T12:00:00Z'))===s;
  const url = value => {
    if (typeof value !== 'string') return null;
    try { const u=new URL(value); return u.protocol==='https:' && !u.username && !u.password ? u.href : null; } catch { return null; }
  };
  const node = (tag, cls, text) => {const el=document.createElement(tag); if(cls)el.className=cls; if(text!==undefined)el.textContent=text; return el;};
  function clean(payload) {
    if (!payload || payload.version!==1 || !Array.isArray(payload.events)) throw Error('Invalid data');
    const ids=new Set();
    return payload.events.filter(e=> {
      if (!Number.isSafeInteger(e.id)||ids.has(e.id)||!validDate(e.date)||!validDate(e.end_date||e.date)) return false;
      if ((e.end_date||e.date)<e.date || !['community','partner'].includes(e.source) || !['online','offline'].includes(e.format)) return false;
      if (typeof e.title!=='string'||!e.title.trim()||typeof e.description!=='string') return false;
      ids.add(e.id); return true;
    }).map(e=>({...e,end_date:e.end_date||e.date,url:url(e.url),poster:url(e.poster)})).sort((a,b)=>(a.date+' '+(a.time||'')).localeCompare(b.date+' '+(b.time||''))||a.id-b.id);
  }
  const matches = e => Object.entries(filters).every(([key,values])=> !values.size || values.has(key==='fee'&&e.fee==='subscription'?'paid':e[key]));
  const monthStart = () => dateISO(new Date(Date.UTC(year,month,1)));
  const monthEnd = () => dateISO(new Date(Date.UTC(year,month+1,0)));
  const inMonth = e => e.date<=monthEnd() && e.end_date>=monthStart();
  const dateLabel = iso => new Intl.DateTimeFormat('ru',{day:'numeric',month:'short',timeZone:'UTC'}).format(new Date(iso+'T12:00:00Z')).replace(' г.','');
  const period = e => e.end_date===e.date ? dateLabel(e.date) : dateLabel(e.date)+' — '+dateLabel(e.end_date);
  function price(e) {
    if(e.fee==='free')return 'Бесплатно';
    if(e.fee==='subscription')return 'По подписке «Масштаб»';
    if(e.fee==='paid'&&Number.isFinite(e.amount)&&e.amount>0)return 'От '+new Intl.NumberFormat('ru').format(e.amount)+' ₽';
    return 'Стоимость уточняется';
  }
  const format = e => e.format==='online'?'Онлайн':'Офлайн';
  function render() {
    $('month-title').textContent=months[month]+' '+year;
    $('grid-view').setAttribute('aria-pressed',String(view==='grid')); $('list-view').setAttribute('aria-pressed',String(view==='list'));
    $('calendar-grid').hidden=view!=='grid'; $('weekdays').hidden=view!=='grid'; $('event-list').hidden=view!=='list';
    $('reset-filters').hidden=!Object.values(filters).some(s=>s.size);
    const selected=events.filter(matches), visible=selected.filter(inMonth);
    $('data-status').classList.toggle('stale',fallback);
    $('data-status').textContent=loading?'Загружаем события…':`${visible.length ? 'Событий в этом месяце: '+visible.length : 'Нет событий в этом месяце'}.`+(fallback?' Показан сохранённый календарь сообщества; события партнёров временно недоступны.':'');
    const grid=$('calendar-grid'); grid.replaceChildren();
    const first=new Date(Date.UTC(year,month,1)); first.setUTCDate(1-((first.getUTCDay()+6)%7));
    const last=new Date(Date.UTC(year,month+1,0)); const cells=Math.ceil((((new Date(Date.UTC(year,month,1)).getUTCDay()+6)%7)+last.getUTCDate())/7)*7;
    for(let i=0;i<cells;i++) {
      const d=new Date(first); d.setUTCDate(first.getUTCDate()+i); const iso=dateISO(d);
      const cell=node('div','day'+(d.getUTCMonth()!==month?' outside':'')+(iso===todayISO?' today':'')+([0,6].includes(d.getUTCDay())?' weekend':''));
      const num=node('time','day-number',String(d.getUTCDate())); num.dateTime=iso; cell.append(num);
      selected.filter(e=>e.date<=iso&&e.end_date>=iso).forEach(e=> {
        const btn=node('button','event-pill'+(e.source==='partner'?' partner-event':''));btn.type='button';
        btn.setAttribute('aria-label',e.title+', '+period(e)+', '+format(e));
        btn.append(node('strong','',e.title),node('span','pill-meta',(e.time?e.time+' · ':'')+format(e)));
        btn.addEventListener('click',()=>open(e,true));cell.append(btn);
      });grid.append(cell);
    }
    const list=$('event-list');list.replaceChildren();
    for(const e of visible) {
      const btn=node('button','list-card');btn.type='button';const copy=node('div','list-copy');
      copy.append(node('h3','',e.title),node('p','',format(e)+' · '+price(e)+(e.source==='partner'?' · Событие партнёра':'')));
      btn.append(node('div','list-date',period(e)),copy,node('span','list-arrow','↗'));btn.addEventListener('click',()=>open(e,true));list.append(btn);
    }
    $('empty').hidden=visible.length>0||loading;
    $('find-next').disabled=!selected.some(e=>e.date>monthEnd());
  }
  function open(e,changeHash) {
    const body=$('dialog-body');body.replaceChildren();
    if(e.poster) {const img=node('img','dialog-poster');img.src=e.poster;img.alt='Афиша: '+e.title;img.addEventListener('error',()=>img.remove(),{once:true});body.append(img);}
    body.append(node('div','dialog-source',e.source==='community'?'СОБЫТИЕ СООБЩЕСТВА':'СОБЫТИЕ ПАРТНЁРА'));
    const title=node('h2','',e.title);title.id='dialog-title';body.append(title);
    const tags=node('div','dialog-meta');[period(e)+' '+e.date.slice(0,4),e.time?e.time+' МСК':null,format(e),price(e)].filter(Boolean).forEach(t=>tags.append(node('span','tag',t)));body.append(tags);
    if(e.format==='offline')body.append(node('p','dialog-location','Место: '+(e.location||'Уточняется')));
    body.append(node('p','dialog-desc',e.description),node('p','dialog-organizer','Организатор: '+(e.organizer||'Участник сообщества')));
    const actions=node('div','dialog-actions');
    if(e.url) {const a=node('a','button','Подробнее и участие ↗');a.href=e.url;a.target='_blank';a.rel='noopener';actions.append(a);}
    const a=node('a','button secondary','Добавить своё событие ↗');a.href=BOT;a.target='_blank';a.rel='noopener';actions.append(a);body.append(actions);
    if(!$('event-dialog').open)$('event-dialog').showModal();document.body.classList.add('modal-open');
    if(changeHash)history.pushState(null,'','#event-'+e.id);
  }
  function hashEvent() {
    const m=location.hash.match(/^#event-(\d+)$/);if(!m)return;
    const e=events.find(e=>e.id===Number(m[1]));if(e){year=+e.date.slice(0,4);month=+e.date.slice(5,7)-1;render();open(e,false);}
    else if(!loading){$('data-status').textContent='Событие пока недоступно или отменено. Выберите другое событие в календаре.';}
  }
  $('close-dialog').addEventListener('click',()=>$('event-dialog').close());
  $('event-dialog').addEventListener('close',()=>{document.body.classList.remove('modal-open');if(/^#event-/.test(location.hash))history.replaceState(null,'',location.pathname+location.search);});
  $('event-dialog').addEventListener('click',e=>{if(e.target===$('event-dialog')){const r=$('event-dialog').getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)$('event-dialog').close();}});
  window.addEventListener('hashchange',hashEvent);
  document.querySelectorAll('[data-filter]').forEach(btn=>btn.addEventListener('click',()=>{const s=filters[btn.dataset.filter];if(s.has(btn.dataset.value))s.delete(btn.dataset.value);else s.add(btn.dataset.value);btn.setAttribute('aria-pressed',String(s.has(btn.dataset.value)));render();}));
  $('reset-filters').addEventListener('click',()=>{Object.values(filters).forEach(s=>s.clear());document.querySelectorAll('[data-filter]').forEach(b=>b.setAttribute('aria-pressed','false'));render();});
  function move(delta){const d=new Date(Date.UTC(year,month+delta,1));year=d.getUTCFullYear();month=d.getUTCMonth();render();}
  $('prev-month').addEventListener('click',()=>move(-1));$('next-month').addEventListener('click',()=>move(1));
  $('today').addEventListener('click',()=>{year=+part('year');month=+part('month')-1;render();});
  $('grid-view').addEventListener('click',()=>{view='grid';render();});$('list-view').addEventListener('click',()=>{view='list';render();});
  $('find-next').addEventListener('click',()=>{const e=events.filter(matches).find(e=>e.date>monthEnd());if(e){year=+e.date.slice(0,4);month=+e.date.slice(5,7)-1;render();}});
  async function load(address) {const c=new AbortController(),timer=setTimeout(()=>c.abort(),7000);try{const response=await fetch(address,{signal:c.signal,cache:'no-cache'});if(!response.ok)throw Error('HTTP');return clean(await response.json());}finally{clearTimeout(timer);}}
  (async()=>{
    render();
    // First paint from repository snapshot; live feed is authoritative, including an empty feed.
    try{events=await load('calendar-events.json');fallback=true;loading=false;render();hashEvent();}catch{/* Live source can still load. */}
    try{events=await load(API);fallback=false;}catch{fallback=true;}
    loading=false;render();hashEvent();
    if(!events.length&&fallback)$('data-status').textContent='Не удалось загрузить календарь. Обновите страницу чуть позже.';
    setInterval(async()=>{try{events=await load(API);fallback=false;render();if(location.hash&&!$('event-dialog').open)hashEvent();}catch{/* Keep last known successful data visible. */}},60000);
  })();
})();
