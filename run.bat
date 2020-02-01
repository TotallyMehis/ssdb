@ECHO OFF

: E_Start
:: Python has to be in PATH variable
python ssdb.py
IF NOT ERRORLEVEL 1 GOTO E_Start

PAUSE
