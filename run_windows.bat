@echo off
cd /d "%~dp0"
set PORT=8010
echo.
echo Metsakontroll kaivitub.
echo Kontori aadress selles arvutis: http://127.0.0.1:8010
echo Vedukamehe uldlink: http://127.0.0.1:8010/vedukamees
echo Lingid juhtidele: http://127.0.0.1:8010/admin/lingid
echo.
echo Kui telefon on samas WiFi/vorgus, kasuta arvuti IP aadressi, mitte 127.0.0.1.
echo Arvuti IP vaatamiseks ava teine aken ja kirjuta: ipconfig
echo.
echo Ara seda akent kinni pane.
echo.
python app.py
if errorlevel 1 (
  echo.
  echo Pythoniga kaivitamine ebaonnestus. Proovin python3...
  python3 app.py
)
echo.
echo Programm peatus. Vajuta suvalist klahvi, et aken sulgeda.
pause >nul
