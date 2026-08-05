' Run the bot headless without a console window (this script lives in bat\)
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
repoDir = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
shell.CurrentDirectory = repoDir
shell.Run """" & repoDir & "\bat\start_bot.bat""", 0, False
