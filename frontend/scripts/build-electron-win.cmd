@echo off
REM Desktop build for Windows (NSIS installer in dist-electron\). Run from anywhere:
REM   scripts\build-electron-win.cmd

setlocal
cd /d "%~dp0\.."

call npm run build
if errorlevel 1 exit /b 1

call node scripts\electron-prepare.mjs
if errorlevel 1 exit /b 1

call npx electron-builder --win --config electron-builder.yml --publish never
exit /b %ERRORLEVEL%
