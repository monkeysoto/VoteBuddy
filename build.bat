@echo off
rem Builds VoteBuddy.exe and release\VoteBuddy.zip (exe + browser extension).
rem Needs: pip install -r requirements.txt pyinstaller
cd /d "%~dp0"

python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name VoteBuddy --icon votebuddy.ico ^
  --add-data "votebuddy.ico;." ^
  --add-data "banner.gif;." ^
  --collect-data customtkinter ^
  votebuddy.pyw || goto :fail

if exist release rmdir /s /q release
mkdir release\VoteBuddy
copy /y dist\VoteBuddy.exe release\VoteBuddy\ >nul
xcopy /e /i /q extension release\VoteBuddy\extension >nul
copy /y README.md release\VoteBuddy\ >nul
powershell -NoProfile -Command "Compress-Archive -Force -Path 'release\VoteBuddy\*' -DestinationPath 'release\VoteBuddy.zip'" || goto :fail

echo.
echo Done: release\VoteBuddy.zip
exit /b 0

:fail
echo.
echo Build failed.
exit /b 1
