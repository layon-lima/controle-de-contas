' Controle de Contas portátil: duplo clique, sem janela de terminal.
' Mantém o aplicativo instalado, seus dados e o sincronizador intactos.
Dim shell, fs, folder, launcher, q, url, ready, i, http
Dim shortcutScript, exitCode
Set shell = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
folder = fs.GetParentFolderName(WScript.ScriptFullName)
launcher = folder & "\START_CONTROLE_DE_CONTAS_PORTATIL.bat"
q = Chr(34)
If Not fs.FileExists(launcher) Then
    MsgBox "Iniciador portátil não encontrado. Sincronize os arquivos do projeto.", 48, "Controle de Contas"
    WScript.Quit 1
End If
shell.CurrentDirectory = folder
shell.Run "cmd.exe /d /c " & q & q & launcher & q & q, 0, False
url = "http://127.0.0.1:8767/"
ready = False
For i = 1 To 32
    WScript.Sleep 400
    On Error Resume Next
    Set http = CreateObject("MSXML2.ServerXMLHTTP.6.0")
    http.setTimeouts 900, 900, 900, 900
    http.open "GET", url, False
    http.send
    If Err.Number = 0 Then
        If http.Status = 200 Then
            If InStr(http.responseText, "<title>Controle de Contas</title>") > 0 Then ready = True
        End If
    End If
    Err.Clear
    On Error GoTo 0
    If ready Then Exit For
Next
If ready Then
    ' Criar/atualizar o atalho do Windows mesmo quando o usuario inicia pelo VBS.
    ' O app web ja respondeu: evitar configurar a versao nova se nao abriu.
    shortcutScript = folder & "\tools\SET_WEB_AS_PRIMARY.ps1"
    If fs.FileExists(shortcutScript) Then
        exitCode = shell.Run("powershell.exe -NoProfile -ExecutionPolicy Bypass -File " & q & shortcutScript & q, 0, True)
        If exitCode <> 0 Then
            MsgBox "O aplicativo abriu, mas nao consegui criar o atalho na Area de Trabalho. Verifique as permissoes da pasta Desktop.", 48, "Controle de Contas"
        End If
    End If
Else
    MsgBox "O aplicativo portátil não abriu. Use START_CONTROLE_DE_CONTAS_PORTATIL.bat para ver o erro.", 48, "Controle de Contas"
End If
