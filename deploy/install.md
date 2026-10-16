# Установка на сервере

Расписание ставится на сервер под Ubuntu 22.04 или 24.04. Инструкция
проверялась по частям; команды копируются как есть.

Порядок обязателен: сначала код, потом база, потом nginx, и только потом
сертификат. Иначе nginx не пройдёт проверку и не поднимется.

## Что понадобится

| | |
|---|---|
| Сервер | Ubuntu 22.04/24.04, 1 ГБ памяти хватит |
| Домен | нужен для сертификата: `example.com` и `www.example.com` |
| Доступ | ssh и sudo |

Если сервера и домена нет, расписание можно открыть наружу без них —
см. раздел «Без сервера» в конце.

## 1. Пользователь и папки

Приложение не должно работать от root.

```bash
sudo adduser --system --group --home /opt/timetable timetable
sudo mkdir -p /opt/timetable /var/www/timetable-static
sudo chown -R timetable:timetable /opt/timetable /var/www/timetable-static
sudo chmod 750 /opt/backups 2>/dev/null || sudo mkdir -p /var/backups/timetable
sudo chown -R timetable:timetable /var/backups/timetable
sudo chmod 700 /var/backups/timetable
```

## 2. Код

```bash
sudo -u timetable git clone <адрес репозитория> /opt/timetable
cd /opt/timetable
sudo -u timetable python3 -m venv .venv
sudo -u timetable .venv/bin/pip install --upgrade pip
sudo -u timetable .venv/bin/pip install -r requirements.txt
```

Клонировать прямо в `/opt/timetable` нельзя: папка уже создана и
принадлежит пользователю `timetable`. Тогда так:

```bash
sudo -u timetable git clone <адрес> /opt/timetable/app
sudo rsync -a --exclude .git /opt/timetable/app/ /opt/timetable/
sudo rm -rf /opt/timetable/app
```

## 3. Настройки

Файл с настройками лежит вне репозитория — в нём секретный ключ.

```bash
sudo cp /opt/timetable/.env.example /etc/timetable.env
sudo nano /etc/timetable.env
```

Обязательно заполнить:

```
DJANGO_SECRET_KEY=<длинная случайная строка>
DJANGO_ALLOWED_HOSTS=example.com,www.example.com
DJANGO_DEBUG=0
DJANGO_CSRF_TRUSTED_ORIGINS=https://example.com,https://www.example.com
DJANGO_TRUST_PROTO=1
DJANGO_TRUST_XFF=1
DJANGO_STATIC_ROOT=/var/www/timetable-static
```

Ключ генерируется один раз:

```bash
/opt/timetable/.venv/bin/python -c \
  "from django.core.management.utils import get_random_secret_key as k; print(k())"
```

**`DJANGO_TRUST_XFF=1` здесь обязателен.** За nginx приложение видит в
`REMOTE_ADDR` адрес `127.0.0.1`, а не адрес посетителя. Если эту строку
не задать, проверка входа в админку будет смотреть на nginx и решит, что
запрос пришёл изнутри, — админка откроется всему интернету. Второй замок
(правило `allow` в nginx) для этого случая и нужен.

Кому разрешён вход в админку — строки `DJANGO_ADMIN_ALLOWED_IPS`. Пусто
означает «только локальная сеть вуза». Если вуз за провайдером и адреса
не 10.x/192.168.x, укажи диапазон, например `DJANGO_ADMIN_ALLOWED_IPS=203.0.113.0/24`.

```bash
sudo chmod 600 /etc/timetable.env
```

## 4. База

```bash
cd /opt/timetable
sudo -u timetable .venv/bin/python manage.py migrate
sudo -u timetable .venv/bin/python manage.py collectstatic --noinput
```

Базой данных здесь SQLite: она лежит файлом `db.sqlite3` рядом с кодом.
Файл в `.gitignore`, поэтому в репозиторий он не попадает. Копию базы
положить сюда надо отдельно, из своей машины:

```bash
scp db.sqlite3 timetable@example.com:/opt/timetable/db.sqlite3
ssh timetable@example.com "sudo chown timetable:timetable /opt/timetable/db.sqlite3"
```

Дальше на сервере создать администратора:

```bash
sudo -u timetable .venv/bin/python manage.py createsuperuser
```

## 5. Служба

```bash
sudo cp /opt/timetable/deploy/timetable.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now timetable
sudo systemctl status timetable
```

Проверка, что приложение отвечает (минуя nginx):

```bash
curl -i http://127.0.0.1:8000/healthz/
```

## 6. nginx

```bash
sudo cp /opt/timetable/deploy/timetable-proxy.conf /etc/nginx/snippets/
sudo nano /etc/nginx/snippets/timetable-proxy.conf   # ничего менять не нужно
sudo nano /etc/nginx/sites-available/timetable       # шаблон: deploy/timetable.nginx.conf
sudo ln -s /etc/nginx/sites-available/timetable /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

В шаблоне конфигурации заменить `example.com` на свой домен в трёх
местах и вписать адреса, которым разрешён вход в `/admin/`.

## 7. Сертификат

Сначала временная версия конфигурации без ssl-блока, чтобы nginx
поднялся и отвечал по http, и только потом выпуск сертификата:

```bash
sudo certbot --nginx -d example.com -d www.example.com
sudo systemctl reload nginx
```

## 8. Бэкапы

Раз в сутки, в 3:17 — не в ровный час, чтобы не совпасть с другими задачами.

```bash
sudo crontab -e
```

```
17 3 * * * TIMETABLE_DB=/opt/timetable/db.sqlite3 /opt/timetable/tools/backup.sh >> /var/log/timetable-backup.log 2>&1
```

Стоит также настроить копию папки проекта и проверять, что бэкап вообще
создаётся: копия, которая перестала делаться, выглядит как работающая.

## 9. Проверка после установки

```bash
curl -I https://example.com/                      # 200
curl -I https://example.com/admin/                # 404 — снаружи админки нет
curl -I -H 'X-Forwarded-For: 10.0.0.5' \
     https://example.com/admin/                   # 404, подделать адрес нельзя
```

Второй и третий запросы должны вернуть 404 снаружи и 200 из сети вуза.

## Без сервера

Если сервера и домена нет, а открыть расписание наружу нужно, помогает
сервис Cloudflare: он даёт публичный адрес с HTTPS прямо с этой машины,
без аренды сервера и без домена.

```bash
winget install --id Cloudflare.cloudflared
cloudflared tunnel --url http://127.0.0.1:8000
```

Плюсы: бесплатно, HTTPS из коробки. Минусы: сайт работает, только пока
включён компьютер, и адрес меняется при каждом запуске. Для постоянного
адреса нужна бесплатная регистрация на cloudflare.com и именованный
туннель.

**Проверьте, открывается ли админка снаружи.** Туннель отдаёт наружу
127.0.0.1, то есть «локальный» адрес, и проверка входа решит, что вы
внутри. Поэтому при туннеле `DJANGO_ADMIN_ALLOWED_IPS` надо задать
явно и обязательно ненулевой:

```
DJANGO_ADMIN_ALLOWED_IPS=10.0.0.0/8,192.168.0.0/16,172.16.0.0/12
```

Тогда снаружи вход будет закрыт, а из сети вуза останется.
