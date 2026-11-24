# backend (3018) 持久启动器 (计划任务用)
# 仓库根已有功能完整的 start-backend.ps1 (幂等/自愈装依赖/等待就绪),
# 本脚本是它的计划任务薄封装: 端口已占则直接跳过 (快速返回, 不阻塞调度器)。
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

if (Get-NetTCPConnection -LocalPort 3018 -State Listen -ErrorAction SilentlyContinue) {
  exit 0
}
$env:NO_PROXY = "127.0.0.1,localhost,::1"
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "start-backend.ps1") | Out-Null
exit $LASTEXITCODE
