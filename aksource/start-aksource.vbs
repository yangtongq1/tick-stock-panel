' TSP aksource 免费数据源启动器 (端口 3021)
' 独立 VBS: 由 wscript 直接运行, 进程脱离任何终端会话, 不随 shell 退出
set fs = CreateObject("Scripting.FileSystemObject")
set ws = CreateObject("Wscript.Shell")
strRoot = "C:\Users\admin\WorkBuddy\2026-11-13-14-08-19\tick-stock-panel"
strPy = "C:\Users\admin\.workbuddy\binaries\python\versions\3.13.12\python.exe"
strLog = strRoot & "\data\logs\aksource.log"

' 端口守卫: 已在运行则退出 (用 netstat 检查)
Dim executed, out
executed = False
Set objExec = ws.Exec("cmd /c netstat -ano | findstr :3021 | findstr LISTENING")
out = objExec.StdOut.ReadAll()
If InStr(out, "3021") > 0 Then
  WScript.Quit 0
End If

If Not fs.FolderExists(strRoot & "\data\logs") Then
  fs.CreateFolder(strRoot & "\data\logs")
End If

' working dir 设为 aksource, 输出重定向到日志, 完全分离运行
ws.CurrentDirectory = strRoot & "\aksource"
ws.Run "cmd /c """ & strPy & """ server.py >> """ & strLog & """ 2>&1", 0, False
