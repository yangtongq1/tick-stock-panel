' TSP aksource 免费数据源启动器 (端口 3021)
' 独立 VBS: 由 wscript 直接运行, 进程脱离任何终端会话, 不随 shell 退出
' 路径自适应: 相对本文件定位仓库根 (git clone 到任意目录可用)
' python: 复用 TSP backend venv (fastapi/uvicorn/pandas/httpx 齐全)
set fs = CreateObject("Scripting.FileSystemObject")
set ws = CreateObject("Wscript.Shell")
strHere = fs.GetParentFolderName(WScript.ScriptFullName)   ' ...\aksource
strRoot = fs.GetParentFolderName(strHere)                  ' 仓库根
strPy = strRoot & "\backend\.venv\Scripts\python.exe"
strLog = strRoot & "\data\logs\aksource.log"

' backend venv 不存在 (未跑过 quick-start) 则提示后退出
If Not fs.FileExists(strPy) Then
  MsgBox "未找到 backend venv: " & strPy & vbCrLf & _
         "请先运行仓库根目录的 quick-start.ps1 安装依赖。", 48, "aksource"
  WScript.Quit 1
End If

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
ws.CurrentDirectory = strHere
ws.Run "cmd /c """ & strPy & """ server.py >> """ & strLog & """ 2>&1", 0, False
