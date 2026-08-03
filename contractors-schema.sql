-- ============================================
-- Схема для раздела "Проверенные подрядчики"
-- frgu.ru/contractors
-- Запустить в Supabase → SQL Editor
-- ============================================

-- 1. КАТЕГОРИИ
create table if not exists categories (
  id          text primary key,
  name        text not null,
  icon        text,
  sort_order  int default 100
);

-- 2. ПОДРЯДЧИКИ
create table if not exists contractors (
  id            uuid primary key default gen_random_uuid(),
  slug          text unique not null,
  name          text not null,
  company       text,
  category_id   text references categories(id),
  city          text,
  photo_url     text,
  short_desc    text,
  full_desc     text,
  tg_account    text,
  tg_channel    text,
  website       text,
  is_verified   boolean default false,
  is_published  boolean default true,
  sort_order    int default 100,
  created_at    timestamptz default now()
);

-- 3. ОТЗЫВЫ
create table if not exists reviews (
  id             uuid primary key default gen_random_uuid(),
  contractor_id  uuid references contractors(id) on delete cascade,
  reviewer_name  text not null,
  reviewer_role  text,
  reviewer_photo text,
  reviewer_tg    text,
  duration       text,          -- п.2 "Как давно сотрудничаете"
  results        text,          -- п.3 "Результаты в цифрах"
  discipline     text,          -- п.4 "Сроки и бюджеты"
  verdict        text,          -- п.5 "Итоговый вердикт"
  sort_order     int default 100,
  created_at     timestamptz default now()
);

-- ============================================
-- RLS: только публичное чтение, запись закрыта
-- ============================================
alter table categories  enable row level security;
alter table contractors enable row level security;
alter table reviews     enable row level security;

drop policy if exists "public read categories"  on categories;
drop policy if exists "public read contractors" on contractors;
drop policy if exists "public read reviews"     on reviews;

create policy "public read categories"
  on categories for select to anon using (true);

create policy "public read contractors"
  on contractors for select to anon using (is_published = true);

create policy "public read reviews"
  on reviews for select to anon using (true);

-- ============================================
-- ДАННЫЕ: категории
-- ============================================
insert into categories (id, name, icon, sort_order) values
  ('marketing',   'Маркетинг',            '📈', 10),
  ('sales',       'Продажи и упаковка',   '🎯', 20),
  ('legal',       'Юристы',               '⚖️', 30),
  ('it',          'IT и автоматизация',   '⚙️', 40),
  ('design',      'Дизайн и брендинг',    '🎨', 50),
  ('hr',          'HR и обучение',        '👥', 60),
  ('finance',     'Финансы и учёт',       '💰', 70),
  ('other',       'Другое',               '🔧', 90)
on conflict (id) do update set
  name = excluded.name, icon = excluded.icon, sort_order = excluded.sort_order;

-- ============================================
-- ДАННЫЕ: Ярослав Осинцев
-- ============================================
insert into contractors (
  slug, name, company, category_id, city, photo_url,
  short_desc, full_desc, tg_account, tg_channel, website,
  is_verified, sort_order
) values (
  'yaroslav-osintsev',
  'Ярослав Осинцев',
  'Маркетинговое агентство «Osintsev»',
  'marketing',
  'Новосибирск',
  'contractors/osintsev.jpg',
  'Реклама франшиз. Более 100 проектов с 2020 года.',
  'Реклама франшиз. Реализовали более 100 проектов с 2020 года. В портфолио Чемпионика, Синергия, 4hands, Не Школа, HOHORO, Юниор, 20х80 и другие.',
  'yaroslav_osintsev',
  'yaroslav_osintsev',
  'https://osintsev.su/',
  true,
  10
) on conflict (slug) do update set
  name = excluded.name, company = excluded.company, category_id = excluded.category_id,
  city = excluded.city, photo_url = excluded.photo_url, short_desc = excluded.short_desc,
  full_desc = excluded.full_desc, tg_account = excluded.tg_account,
  tg_channel = excluded.tg_channel, website = excluded.website,
  is_verified = excluded.is_verified;

-- Отзывы на Ярослава
delete from reviews where contractor_id = (select id from contractors where slug = 'yaroslav-osintsev');

insert into reviews (
  contractor_id, reviewer_name, reviewer_role, reviewer_photo, reviewer_tg,
  duration, results, discipline, verdict, sort_order
) values
(
  (select id from contractors where slug = 'yaroslav-osintsev'),
  'Денис Блохин',
  'Директор по франчайзингу, Junior Projects (Юниор, Русский балет, ЮниорКод)',
  'contractors/rev-blokhin.jpg',
  'Denis_BL',
  'Сотрудничаем по разным проектам с 2022 года',
  'Показатели качества лидов и конверсии не хуже, чем у собственного отдела b2b, но при этом стоимость услуг во много раз дешевле, чем ФОТ собственного отдела.',
  'Да, дисциплина в полном порядке.',
  'Инициативность команды, быстрый запуск и корректировка рекламных кампаний, честность, прозрачная отчётность. Смело рекомендую агентство Ярослава. Цена точно ниже, чем выдаваемое качество работ.',
  10
),
(
  (select id from contractors where slug = 'yaroslav-osintsev'),
  'Александр Мельников',
  'OnePrice, EpicPizza и др.',
  'contractors/rev-melnikov.jpg',
  null,
  'С 2019 года',
  'Стабильный приток лидов, баланс цена/конверсия.',
  'Да',
  'На мой взгляд адекватная и профессиональная команда, работа на результат.',
  20
);
