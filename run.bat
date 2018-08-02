@ECHO OFF

: E_Start
:: Python has to be in PATH variable
python serverlist_bot.py
IF NOT ERRORLEVEL 1 GOTO E_Start

PAUSE
