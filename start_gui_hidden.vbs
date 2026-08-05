' 컨트롤 패널(GUI)을 띄우면서 봇도 자동 실행한다 (스크립트 위치 기준 상대경로)
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
baseDir = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = baseDir
shell.Run "pythonw """ & baseDir & "\src\bot_gui.py"" --autostart", 1, False
