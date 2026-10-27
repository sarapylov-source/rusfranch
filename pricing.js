/* Единая точка правки цены подписки на чат «Масштаб».
   Меняете значения здесь — обновляются все страницы сайта.
   Текст внутри элементов в HTML — это запасной вариант на случай,
   если JS не загрузился; синхронизировать его можно командой
   python3 tools/sync-price.py */
(function () {
  var P = {
    price: '1 000 ₽',          // цена первого месяца по акции
    priceRegular: '5 000 ₽',   // обычная цена месяца
    until: '3 ноября',         // до какого числа действует акция
    badge: '−80%',             // плашка скидки
    payLink: 'https://t.me/tribute/app?startapp=sCed'
  };

  window.FRGU_PRICING = P;

  function apply() {
    var map = {
      'price': P.price,
      'price-regular': P.priceRegular,
      'price-until': P.until,
      'price-badge': P.badge
    };
    Object.keys(map).forEach(function (key) {
      var nodes = document.querySelectorAll('[data-' + key + ']');
      for (var i = 0; i < nodes.length; i++) nodes[i].textContent = map[key];
    });

    var links = document.querySelectorAll('[data-price-link]');
    for (var j = 0; j < links.length; j++) links[j].setAttribute('href', P.payLink);

    var meta = document.querySelector('meta[name="description"][data-price-tpl]');
    if (meta) {
      meta.setAttribute('content', meta.getAttribute('data-price-tpl')
        .split('{price}').join(P.price)
        .split('{regular}').join(P.priceRegular)
        .split('{until}').join(P.until));
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', apply);
  } else {
    apply();
  }
})();
