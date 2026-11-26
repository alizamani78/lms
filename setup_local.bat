@echo off
REM Razavi LMS - first-time local setup on Windows (development only).
REM Run in an EMPTY copy of the project folder: double-click or run `setup_local.bat` in cmd.
setlocal
cd /d "%~dp0"
echo [1/7] checking the files ...
py tools\check_install.py || (echo Stop: fix the problems above first. & exit /b 1)
echo [2/7] virtual environment ...
if not exist venv ( py -m venv venv )
call venv\Scripts\activate
echo [3/7] installing libraries ...
py -m pip install --upgrade pip
py -m pip install -r requirements.txt || (echo pip failed & exit /b 1)
set DJANGO_DEBUG=1
echo [4/7] database migrations (new, clean) ...
if exist db.sqlite3 del db.sqlite3
for /d %%d in (apps\*) do if exist "%%d\migrations" del /q "%%d\migrations\0*.py" 2>nul
py manage.py makemigrations || (echo makemigrations failed & exit /b 1)
py manage.py migrate || (echo migrate failed & exit /b 1)
echo [5/7] catalogue ...
py manage.py seed_catalog
py manage.py seed_provinces
py manage.py seed_counties
echo [6/7] create the first administrator ...
py manage.py createsuperuser
echo [7/7] starting - open http://127.0.0.1:8000/ and http://127.0.0.1:8000/system/
py manage.py runserver
