@echo off
setlocal
cd /d "%~dp0"
echo.
echo ADAPTIVE PAPER TRADER - CLOUD PUBLISHER
echo ---------------------------------------
echo First create an EMPTY PUBLIC GitHub repository (do not add README).
echo The repo and website will expose your FAKE trading history publicly.
echo Requires Git for Windows and a GitHub login.
echo.
set /p REPO=Paste your new repo's HTTPS URL (https://github.com/USER/REPO.git): 
if "%REPO%"=="" (echo Missing URL & pause & exit /b 1)
git --version >nul 2>&1
if errorlevel 1 (echo Git is not installed. Install Git for Windows first. & pause & exit /b 1)
if not exist .git git init -b main
if errorlevel 1 (echo Cannot initialize repo. & pause & exit /b 1)
git remote get-url origin >nul 2>&1
if errorlevel 1 (git remote add origin "%REPO%") else (git remote set-url origin "%REPO%")
git add .
git -c user.name="Paper Trader Owner" -c user.email="owner@users.noreply.github.com" commit -m "Add cloud paper trader dashboard" 
if errorlevel 1 (echo Commit may already exist. Trying push.)
git branch -M main
git push -u origin main
if errorlevel 1 (echo Push failed: check that the repo is EMPTY and you are logged into GitHub. & pause & exit /b 1)
echo.
echo UPLOADED. NEXT: GitHub repo Settings - Pages - Source: GitHub Actions.
echo Then repo Actions - Paper trader cloud updates and dashboard - Run workflow.
echo GitHub Settings - Actions - General - Workflow permissions: Read and write permissions.
echo Your real site URL is displayed in GitHub Settings - Pages after deployment.
pause
