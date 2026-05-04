@echo off
REM Windows NSIS build (wrapper). Run from frontend\:
REM   npm run electron:build:win
REM   scripts\electron\build-electron-win.cmd

setlocal
cd /d "%~dp0\..\.."
node scripts\electron\build-electron.mjs win
exit /b %ERRORLEVEL%
