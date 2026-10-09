' Abrir com duplo clique. Janela do terminal fica oculta.
' Não altera PATH, registro, instalador ou dados do aplicativo.
Dim shell, fileSystem, scriptFolder, launcher, quote
Set shell = CreateObject("WScript.Shell")
Set fileSystem = CreateObject("Scripting.FileSystemObject")
scriptFolder = fileSystem.GetParentFolderName(WScript.ScriptFullName)
launcher = scriptFolder & "\START_CONTROLE_DE_CONTAS_PORTATIL.bat"
quote = Chr(34)
shell.CurrentDirectory = scriptFolder
shell.Run "cmd.exe /d /c " & quote & quote & launcher & quote & quote, 0, False
