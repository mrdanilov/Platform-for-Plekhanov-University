@echo off
chcp 65001 >nul

rem Переключение кодировки стоит вторым действием, до первой русской
rem строки: пока кодировка не переключена, cmd читает файл в кодовой
rem странице консоли и режет кириллицу посреди слова. Поэтому до этой
rem строки в файле только ASCII.

rem ============================================================
rem   Расписание вуза - запуск платформы на Windows
rem
rem   Двойной клик по файлу: появляется список, выбирается цифра.
rem   Из консоли то же самое командой:
rem
rem       Platform.bat run       сайт на этом компьютере
rem       Platform.bat lan       показ коллегам в локальной сети
rem       Platform.bat test      тесты
rem       Platform.bat backup    бэкап базы
rem       Platform.bat check     проверка проекта
rem       Platform.bat admin     создать администратора
rem
rem   Скрипт сам находит папку проекта, поэтому его можно запускать
rem   откуда угодно. Если окружения .venv нет - создаёт и ставит
rem   зависимости, дальше миграции и запуск.
rem ============================================================

setlocal EnableExtensions
cd /d "%~dp0"

set "PY=%~dp0.venv\Scripts\python.exe"
set "PYTHONIOENCODING=utf-8"
set "DJANGO_SETTINGS_MODULE=config.settings"
set "URL=http://127.0.0.1:8000/"

if not "%~1"=="" goto dispatch
set "FROM_MENU=1"

:menu
cls
echo.
echo  ==================================================
echo     Расписание вуза - платформа
echo  ==================================================
echo      1  Открыть сайт на этом компьютере
echo      2  Показать коллегам в локальной сети
echo      3  Прогнать тесты
echo      4  Сделать бэкап базы
echo      5  Создать администратора
echo      6  Проверить проект
echo      0  Выход
echo  ==================================================
choice /c 1234560 /n /m "  Ваш выбор: "
if errorlevel 7 goto bye
if errorlevel 6 goto do_check
if errorlevel 5 goto do_admin
if errorlevel 4 goto do_backup
if errorlevel 3 goto do_test
if errorlevel 2 goto do_lan
if errorlevel 1 goto do_run
goto menu

:dispatch
if /i "%~1"=="run"     goto do_run
if /i "%~1"=="lan"     goto do_lan
if /i "%~1"=="test"    goto do_test
if /i "%~1"=="backup"  goto do_backup
if /i "%~1"=="check"   goto do_check
if /i "%~1"=="admin"   goto do_admin
echo.
echo  Неизвестная команда: %~1
echo  Доступно: run, lan, test, backup, check, admin
goto fail

rem ------------------------------------------------------------
rem   Сайт на этом компьютере: разработческий сервер, он же переза-
rem   гружается на каждое изменение файла. Порт проверяется заранее:
rem   если платформа уже работает, второй раз её поднимать не нужно -
rem   достаточно открыть браузер.
rem ------------------------------------------------------------
:do_run
call :ensure_env
if errorlevel 1 goto fail
call :migrate
if errorlevel 1 goto fail
call :server_up
if "%UP%"=="1" (
    echo.
    echo  Сайт уже запущен - открываю в браузере.
    start "" "%URL%"
    goto bye
)
echo.
echo  Открываю %URL% в браузере. Остановить - Ctrl+C в этом окне.
start "" cmd /c "ping -n 3 127.0.0.1 >nul & start %URL%"
"%PY%" manage.py runserver 127.0.0.1:8000
goto bye

rem   Показ коллегам: waitress вместо runserver - многопоточный и со
rem   статикой, сам собирает статику и проверяет миграции.
:do_lan
call :ensure_env
if errorlevel 1 goto fail
"%PY%" tools\show_lan.py
goto bye

:do_test
call :ensure_env
if errorlevel 1 goto fail
"%PY%" manage.py test schedule
goto bye

:do_backup
call :ensure_env
if errorlevel 1 goto fail
"%PY%" tools\backup_db.py
if errorlevel 1 goto fail
goto bye

:do_admin
call :ensure_env
if errorlevel 1 goto fail
call :migrate
if errorlevel 1 goto fail
"%PY%" manage.py createsuperuser
goto bye

:do_check
call :ensure_env
if errorlevel 1 goto fail
"%PY%" manage.py check
if errorlevel 1 goto fail
"%PY%" manage.py makemigrations --check --dry-run
if errorlevel 1 (
    echo.
    echo  Есть изменения в таблицах без миграции.
    echo  Выполните: python manage.py makemigrations
    goto fail
)
echo.
echo  Проект в порядке: ошибок нет, миграции все применены.
goto bye

rem ------------------------------------------------------------
rem   Служебные шаги
rem ------------------------------------------------------------

rem   Окружение и зависимости. Проверяется каждый раз: руками окружение
rem   можно удалить или перенести, и падение с "python не найдено" на
rem   ровном месте отнимает больше времени, чем эта проверка.
:ensure_env
if exist "%PY%" exit /b 0
echo.
echo  Окружение .venv не найдено - создаю и ставлю зависимости.
echo  Это один раз, дальше запуск занимает секунды.
where python >nul 2>&1
if errorlevel 1 (
    echo.
    echo  Python не найден. Установите Python 3.12 или новее:
    echo  https://www.python.org/downloads/
    echo  При установке отметьте "Add python.exe to PATH".
    exit /b 1
)
python -m venv .venv
if errorlevel 1 (
    echo  Не удалось создать окружение .venv
    exit /b 1
)
"%PY%" -m pip install --quiet --upgrade pip
"%PY%" -m pip install --quiet -r requirements.txt
if errorlevel 1 (
    echo.
    echo  Не удалось поставить зависимости из requirements.txt
    exit /b 1
)
exit /b 0

rem   Миграции перед стартом: забытая миграция всплывает не в момент
rem   запуска, а когда таблица уже открыта в браузере.
:migrate
"%PY%" manage.py migrate --noinput
if errorlevel 1 (
    echo.
    echo  Миграции не применились - ошибка выше.
    exit /b 1
)
exit /b 0

rem   Отвечает ли на порту уже работающая платформа. curl есть на
rem   Windows 10 и новее; если его нет, переменная останется пустой и
rem   сервер просто запустится - худшего, чем сообщение Django о занятом
rem   порте, от такого порядка не случится.
:server_up
set "UP=0"
for /f "usebackq delims=" %%A in (`curl -s -o nul -w "%%{http_code}" --max-time 2 %URL%`) do set "UP=%%A"
if not "%UP%"=="200" set "UP=0"
exit /b 0

:fail
echo.
if defined FROM_MENU pause
endlocal
exit /b 1

:bye
if defined FROM_MENU (
    echo.
    pause
)
endlocal
exit /b 0
