param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8765
)

$ErrorActionPreference = 'Stop'
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw '找不到项目虚拟环境。请先按照 README 安装 Python 依赖。'
}

& $python (Join-Path $PSScriptRoot 'web\server.py') --host 127.0.0.1 --port $Port
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
