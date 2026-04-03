# Data Agent - Environment Setup Script (PowerShell)
# UTF-8 encoding for better compatibility

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "Data Agent - Environment Setup Script" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# Check if conda is available
$condaExists = Get-Command conda -ErrorAction SilentlyContinue
if (-not $condaExists) {
    Write-Host "[ERROR] Conda not found" -ForegroundColor Red
    Write-Host "Please install Anaconda or Miniconda first" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host "[1/5] Checking for dataagent environment..." -ForegroundColor Yellow
$envList = conda env list 2>$null | Out-String
if ($envList -match "dataagent") {
    Write-Host "[OK] dataagent environment exists" -ForegroundColor Green
    Write-Host ""
    $recreate = Read-Host "Recreate environment? (will delete existing) [Y/N]"
    if ($recreate -eq "Y" -or $recreate -eq "y") {
        Write-Host "[INFO] Removing existing environment..." -ForegroundColor Yellow
        conda deactivate 2>$null
        conda env remove -n dataagent -y
        Write-Host "[OK] Environment removed" -ForegroundColor Green
    } else {
        Write-Host "[INFO] Using existing environment" -ForegroundColor Yellow
        goto install_deps
    }
}

Write-Host "[2/5] Creating Python 3.8 environment..." -ForegroundColor Yellow
conda create -n dataagent python=3.8 -y
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Failed to create environment" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Host "[OK] Python 3.8 environment created" -ForegroundColor Green
Write-Host ""

:install_deps
Write-Host "[3/5] Activating dataagent environment..." -ForegroundColor Yellow
conda activate dataagent
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Failed to activate environment" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host "[OK] Environment activated" -ForegroundColor Green
python --version
Write-Host ""

Write-Host "[4/5] Installing backend dependencies..." -ForegroundColor Yellow
Set-Location "$PSScriptRoot\backend"
pip install -r requirements-py38.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host "[WARNING] Some dependencies failed, trying original requirements..." -ForegroundColor Yellow
    pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Failed to install backend dependencies" -ForegroundColor Red
        Read-Host "Press Enter to exit"
        exit 1
    }
}
Write-Host "[OK] Backend dependencies installed" -ForegroundColor Green
Write-Host ""

Write-Host "[5/5] Checking frontend dependencies..." -ForegroundColor Yellow
Set-Location "$PSScriptRoot\frontend"
if (-not (Test-Path "node_modules")) {
    Write-Host "[INFO] Installing frontend dependencies..." -ForegroundColor Yellow
    npm install
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Failed to install frontend dependencies" -ForegroundColor Red
        Read-Host "Press Enter to exit"
        exit 1
    }
    Write-Host "[OK] Frontend dependencies installed" -ForegroundColor Green
} else {
    Write-Host "[OK] Frontend dependencies already installed" -ForegroundColor Green
}
Write-Host ""

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "Environment Setup Complete!" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "  1. Run start-all.bat to start the system"
Write-Host "  2. Or manually activate: conda activate dataagent"
Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Read-Host "Press Enter to exit"
