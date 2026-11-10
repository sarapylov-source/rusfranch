(() => {
  'use strict';
  const root = document.getElementById('awardNominees');
  const dialog = document.getElementById('nomineeDialog');
  if (!root || !dialog) return;
  const content = document.getElementById('nomineeDetails');
  const stamp = document.getElementById('nomineeUpdated');
  const categories = ['ff', 'fs', 'ef', 'es'];
  let data = null, activeId = null, trigger = null;
  const el = (tag, text, cls) => { const n = document.createElement(tag); if (text != null) n.textContent = text; if (cls) n.className = cls; return n; };
  function photo(n, cls) {
    const box = el('div', null, cls);
    const initials = n.name.split(/\s+/).slice(0, 2).map(x => x[0] || '').join('');
    box.append(el('span', initials, 'nominee-initials'));
    if (n.photo && n.photo.startsWith('https://api.frgu.ru/award/photos/')) {
      const image = el('img'); image.src = n.photo; image.alt = n.name; image.loading = 'lazy';
      image.addEventListener('error', () => image.remove()); box.append(image);
    }
    return box;
  }
  function botLink(label, url) {
    const a = el('a', label, 'btn btn-red'); a.href = url; a.target = '_blank'; a.rel = 'noopener'; return a;
  }
  function details(n) {
    content.replaceChildren();
    const head = el('div', null, 'nominee-dialog-head'); head.append(photo(n, 'nominee-dialog-photo'));
    const meta = el('div'); meta.append(el('p', data.categories[n.category], 'nominee-category'), el('h3', n.name));
    meta.lastChild.id = 'nomineeDialogTitle';
    meta.append(el('p', n.company, 'nominee-company'), el('p', n.position), el('p', n.respects + ' респектов', 'nominee-respects'));
    head.append(meta); content.append(head);
    content.append(el('h4', 'За что предлагают вручить премию'), el('p', n.reason, 'nominee-reason'));
    content.append(el('h4', 'Кто выдвинул'), el('p', n.nominator.name + (n.self_nomination ? ' · самовыдвижение' : '')));
    content.append(el('h4', 'Кто поддержал · ' + n.respects));
    if (n.supporters.length) { const list = el('ul', null, 'nominee-supporters'); n.supporters.forEach(p => list.append(el('li', p.name))); content.append(list); }
    else content.append(el('p', 'Пока нет респектов. Можно поддержать выдвижение в боте.'));
    content.append(botLink('Поддержать респектом в боте', n.support_url), el('p', 'Респекты за выдвижение собираем до 1 декабря. Финальное голосование — 1–10 декабря.', 'nominee-note'));
  }
  function open(n, button) { activeId = n.id; trigger = button; details(n); dialog.showModal(); }
  document.getElementById('closeNominee').addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', event => { if (event.target === dialog) { const r = dialog.getBoundingClientRect(); if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) dialog.close(); } });
  dialog.addEventListener('close', () => { activeId = null; if (trigger && trigger.isConnected) trigger.focus(); });
  function render(next) {
    if (!next || !Array.isArray(next.nominees) || !next.categories) throw new Error('Invalid nominees');
    next.nominees.forEach(n => {
      if (!Number.isInteger(n.id) || !categories.includes(n.category) || n.support_url !== 'https://t.me/rusfranch_bot?start=award_n_' + n.id || !Array.isArray(n.supporters) || n.respects !== n.supporters.length) throw new Error('Invalid nominee');
    });
    const unchanged = data && JSON.stringify(data.nominees) === JSON.stringify(next.nominees);
    data = next;
    if (!unchanged) {
      root.replaceChildren();
      categories.forEach(cat => {
        const group = el('section', null, 'nominee-cohort'); group.append(el('h3', data.categories[cat]));
        const rows = data.nominees.filter(n => n.category === cat);
        const grid = el('div', null, 'nominee-grid');
        rows.forEach(n => {
          const card = el('article', null, 'nominee-card'); card.append(photo(n, 'nominee-photo'));
          const text = el('div', null, 'nominee-card-text'); text.append(el('h4', n.name), el('p', n.company));
          const button = el('button', 'Подробнее', 'nom-btn'); button.type = 'button'; button.setAttribute('aria-label', 'Подробнее: ' + n.name); button.addEventListener('click', () => open(n, button)); text.append(button); card.append(text); grid.append(card);
        });
        if (rows.length) group.append(grid); else group.append(el('p', 'Здесь пока нет номинантов. Выдвинуть себя или коллегу можно в боте.', 'nominee-empty'));
        root.append(group);
      });
      if (dialog.open) { const n = data.nominees.find(n => n.id === activeId); if (n) details(n); else dialog.close(); }
    }
    const date = new Date(data.updated_at); stamp.textContent = 'Данные обновлены ' + date.toLocaleString('ru-RU', {timeZone:'Europe/Moscow', day:'numeric', month:'long', hour:'2-digit', minute:'2-digit'}) + ' МСК';
  }
  async function load(url) { const r = await fetch(url, {cache:'no-store'}); if (!r.ok) throw new Error('Unavailable'); render(await r.json()); }
  (async () => { try { await load('https://api.frgu.ru/award/nominees.json'); } catch (_) { try { await load('award-nominees.json'); } catch (_) { stamp.textContent = 'Список временно недоступен. Все номинанты и респекты — в боте.'; } } })();
  setInterval(() => { if (!document.hidden) load('https://api.frgu.ru/award/nominees.json').catch(() => {}); }, 60000);
})();
