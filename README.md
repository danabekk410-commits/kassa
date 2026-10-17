# Касса — POS для кофейни на телефоне

Веб-касса для небольшой кофейни: бариста пробивает заказ на своём телефоне,
выбирает оплату (Kaspi, наличные или смешанная), касса считает сдачу. Работает
как PWA и без интернета: продажи копятся на телефоне и уходят на сервер, когда
появляется сеть. Владелец видит выручку и расходы за любой день.

Используется в реальной кофейне в Алматы.

## Что умеет

- Меню из `public/menu.json`: категории, позиции, варианты объёма/порции.
- Скидка по времени (например, −15% на обеды и выпечку с 16:00 до 19:00) — настраивается в `menu.json`.
- Оплата Kaspi / наличные / смешанная, расчёт сдачи.
- Расходы из кассы (купили молоко за наличные — записали тут же).
- Отмена и правка чека: старый чек отменяется, новый ссылается на него;
  если один чек правят с двух телефонов, побеждает первая правка.
- Офлайн: service worker отдаёт оболочку и меню из кэша, продажи синхронизируются при сети.
- Отчёт за день по всем телефонам.

## Стек

- Фронтенд: один `index.html` на чистом JS, PWA (manifest + service worker), localStorage.
- Бэкенд: `server/kassa_api.py` — Python 3, только стандартная библиотека
  (`http.server`, `sqlite3`), без зависимостей.
- Доступ — общий ключ в заголовке `X-Kassa-Key`, сравнение через `hmac.compare_digest`.
- Записи неизменяемы: повторная отправка того же чека ничего не дублирует, отмену нельзя снять.
- Деплой: nginx (статика + прокси `/api/kassa/`) и systemd.

## API

| Метод | Путь | Что делает |
|---|---|---|
| GET | `/api/kassa/ping` | проверка, без ключа |
| GET | `/api/kassa/day?d=ГГГГ-ММ-ДД` | продажи и расходы за день |
| POST | `/api/kassa/sync` | пачка продаж и расходов с телефона |

## Как запустить локально

```bash
cp .env.example .env            # задать KASSA_KEY (16+ символов)
export $(grep -v '^#' .env | xargs)
python3 server/kassa_api.py     # API на 127.0.0.1:5054, база создастся в data/kassa.db
```

Фронтенд обращается к `/api/kassa` на том же домене, поэтому нужен nginx
(пример — `deploy/nginx.example.conf`) или любой другой прокси. Телефон
открывает `https://<домен>/kassa/#k=<KASSA_KEY>` один раз — ключ сохраняется на устройстве.

Для продакшна — `deploy/kassa.service.example` (systemd).

## Скриншоты

<!-- TODO: экран продажи, экран оплаты, отчёт за день -->

## Демо

Публичного демо нет: рабочая касса закрыта ключом.

---

## English summary

A mobile-first offline-capable POS (PWA) for a small coffee shop. Baristas ring up
orders on their phones (Kaspi / cash / mixed payment, change calculation, time-based
discounts, expenses, void and edit with conflict handling); sales queue locally and
sync to a tiny dependency-free Python + SQLite API when online. Stack: vanilla JS,
Service Worker, Python 3 stdlib (`http.server`, `sqlite3`), nginx, systemd.

## Лицензия

MIT — см. [LICENSE](LICENSE).
