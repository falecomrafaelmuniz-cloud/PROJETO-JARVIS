Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.OpenFileDialog
$dialog.Title = 'Selecione o arquivo para criar o atalho no Jarvis'
$dialog.Filter = 'Todos os Arquivos (*.*)|*.*|Documentos (*.pdf;*.docx;*.xlsx;*.txt)|*.pdf;*.docx;*.xlsx;*.txt|Executáveis (*.exe;*.lnk;*.bat)|*.exe;*.lnk;*.bat'

$form = New-Object System.Windows.Forms.Form
$form.TopMost = $true

$res = $dialog.ShowDialog($form)
if ($res -eq [System.Windows.Forms.DialogResult]::OK) {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    Write-Output $dialog.FileName
}
