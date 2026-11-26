@echo off
rem devcloud launcher (Windows x64 and ARM64): fetches uv into .dc\<platform>\bin, then
rem runs dc.py on a uv-managed Python. Nothing is installed outside this folder.
rem `dc help` for the commands.
setlocal
rem a PowerShell 7 parent leaks its module path into Windows PowerShell 5.1
set "PSModulePath="
set "P=windows-x64"
set "T=x86_64-pc-windows-msvc"
set "S=297b3881fe1adfb922becc2c1613202abb41dc83ea5ff43d472aa087ca1f8683"
if /i "%PROCESSOR_ARCHITECTURE%"=="ARM64" set "A=1"
if /i "%PROCESSOR_ARCHITEW6432%"=="ARM64" set "A=1"
if defined A (
  set "P=windows-arm64"
  set "T=aarch64-pc-windows-msvc"
  set "S=f223bcd3fa010174ffeeb885cc1d5334bb81388640c65baa5b8b42b47017337e"
)
set "L=%~dp0.dc\%P%"
if not exist "%L%\bin\uv.exe" (
  echo dc: downloading uv 0.13.10 1>&2
  powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue';" ^
    "New-Item -ItemType Directory -Force '%L%\bin','%L%\cache' | Out-Null;" ^
    "$f='%L%\cache\uv-%T%.zip';" ^
    "Invoke-WebRequest -UseBasicParsing -OutFile $f 'https://github.com/astral-sh/uv/releases/download/0.13.10/uv-%T%.zip';" ^
    "if ((Get-FileHash -Algorithm SHA256 $f).Hash -ne '%S%') { Remove-Item $f; throw 'uv sha256 mismatch' };" ^
    "Expand-Archive -Force $f '%L%\bin'" || exit /b 1
)
set "UV_CACHE_DIR=%L%\cache\uv"
set "UV_PYTHON_INSTALL_DIR=%L%\python"
set "UV_PYTHON_PREFERENCE=only-managed"
set "UV_PYTHON_INSTALL_BIN=0"
"%L%\bin\uv.exe" run -q --no-project --python 3.14 "%~dp0dc.py" %*
exit /b %ERRORLEVEL%
