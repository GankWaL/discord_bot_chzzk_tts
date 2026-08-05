' Launch the control panel with bot autostart, for the Startup folder shortcut.
' Prefers the exe build, falls back to python source. (this script lives in bat\)
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
repoDir = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
shell.CurrentDirectory = repoDir

newExe = repoDir & "\dist\tts_bot_gui_new.exe"
guiExe = repoDir & "\dist\tts_bot_gui.exe"
oldExe = repoDir & "\dist\tts_bot_gui_old.exe"

' clean up leftovers from in-place GUI rebuild
On Error Resume Next
If fso.FileExists(oldExe) Then fso.DeleteFile oldExe, True
On Error GoTo 0

' swap in rebuilt GUI exe if present (legacy fallback path)
If fso.FileExists(newExe) Then
    If fso.FileExists(guiExe) Then fso.DeleteFile guiExe, True
    fso.MoveFile newExe, guiExe
End If

If fso.FileExists(guiExe) Then
    shell.Run """" & guiExe & """ --autostart", 1, False
Else
    shell.Run "pythonw """ & repoDir & "\src\bot_gui.py"" --autostart", 1, False
End If
