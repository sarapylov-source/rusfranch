#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Синхронизирует запасные значения цены в HTML с pricing.js.

Единственный источник правды — pricing.js. Этот скрипт переписывает текст
внутри элементов с data-price / data-price-regular / data-price-until /
data-price-badge, чтобы страница показывала верную цену даже если JS
не загрузился, и чтобы в HTML не оставалось устаревших цифр.

Запуск из корня репозитория:  python3 tools/sync-price.py
"""
import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, 'pricing.js')

KEYS = {
    'price': 'price',
    'price-regular': 'priceRegular',
    'price-until': 'until',
    'price-badge': 'badge',
}


def read_config():
    src = open(JS, encoding='utf-8').read()
    cfg = {}
    for attr, js_key in KEYS.items():
        m = re.search(js_key + r"\s*:\s*'([^']*)'", src)
        if not m:
            sys.exit('не нашёл %s в pricing.js' % js_key)
        cfg[attr] = m.group(1)
    m = re.search(r"payLink\s*:\s*'([^']*)'", src)
    cfg['payLink'] = m.group(1) if m else None
    return cfg


def sync_file(path, cfg):
    t = open(path, encoding='utf-8').read()
    before = t
    for attr, value in cfg.items():
        if attr == 'payLink':
            continue
        pat = re.compile(
            r'(<(?P<tag>\w+)(?P<attrs>[^>]*\sdata-' + attr + r'(?:=["\'][^"\']*["\'])?)(?P<rest>[^>]*)>)'
            r'(?P<inner>(?:(?!</?(?P=tag)\b).)*?)'
            r'(</(?P=tag)>)',
            re.S)
        t = pat.sub(lambda m: m.group(1) + value + m.group(6), t)

    # шаблон meta description
    m = re.search(r'<meta name="description" content="([^"]*)" data-price-tpl="([^"]*)"', t)
    if m:
        baked = (m.group(2)
                 .replace('{price}', cfg['price'])
                 .replace('{regular}', cfg['price-regular'])
                 .replace('{until}', cfg['price-until']))
        t = t.replace(m.group(0),
                      '<meta name="description" content="%s" data-price-tpl="%s"' % (baked, m.group(2)))

    if t != before:
        open(path, 'w', encoding='utf-8').write(t)
        return True
    return False


def main():
    cfg = read_config()
    print('pricing.js: %s (обычная %s), до %s, плашка %s'
          % (cfg['price'], cfg['price-regular'], cfg['price-until'], cfg['price-badge']))
    changed = []
    for path in sorted(glob.glob(os.path.join(ROOT, '*.html'))):
        if sync_file(path, cfg):
            changed.append(os.path.basename(path))
    print('обновлено файлов: %d%s' % (len(changed), (' — ' + ', '.join(changed)) if changed else ''))


if __name__ == '__main__':
    main()
