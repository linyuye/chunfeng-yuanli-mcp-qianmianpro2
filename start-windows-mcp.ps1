param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8000
)

$ErrorActionPreference = 'Stop'
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw '找不到项目虚拟环境。请先按照 README 安装 Python 依赖。'
}

$previousBridge = [Environment]::GetEnvironmentVariable('TFPTPRO_WEB_BRIDGE', 'Process')
$previousName = [Environment]::GetEnvironmentVariable('DOKIDOKI_DEVICE_NAME', 'Process')
try {
    $env:TFPTPRO_WEB_BRIDGE = '0'
    $env:DOKIDOKI_DEVICE_NAME = 'TF-PTPRO'
    Push-Location -LiteralPath $PSScriptRoot
    try {
        Write-Host "Windows BLE MCP: http://127.0.0.1:$Port/mcp"
        & $python -m heartbeat_yuanli_mcp.server --transport http --host 127.0.0.1 --port $Port
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    } finally {
        Pop-Location
    }
} finally {
    [Environment]::SetEnvironmentVariable('TFPTPRO_WEB_BRIDGE', $previousBridge, 'Process')
    [Environment]::SetEnvironmentVariable('DOKIDOKI_DEVICE_NAME', $previousName, 'Process')
}
