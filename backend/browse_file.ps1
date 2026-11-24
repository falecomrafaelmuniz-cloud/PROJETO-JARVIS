[System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms') | Out-Null
[System.Reflection.Assembly]::LoadWithPartialName('System.Drawing') | Out-Null
[System.Windows.Forms.Application]::EnableVisualStyles()

$dialog = New-Object System.Windows.Forms.OpenFileDialog
$dialog.Title = 'Selecione o arquivo para criar o atalho no Jarvis'
$dialog.Filter = 'Todos os Arquivos (*.*)|*.*|Documentos (*.pdf;*.docx;*.xlsx;*.txt)|*.pdf;*.docx;*.xlsx;*.txt|Executáveis (*.exe;*.lnk;*.bat)|*.exe;*.lnk;*.bat'

$form = New-Object System.Windows.Forms.Form
$form.TopMost = $true
$form.StartPosition = [System.Windows.Forms.FormStartPosition]::CenterScreen
$form.Size = New-Object System.Drawing.Size(1, 1)
$form.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::None
$form.ShowInTaskbar = $false
$form.Opacity = 0
$form.Show()
$form.Activate()
$form.BringToFront()

$res = $dialog.ShowDialog($form)
$form.Close()
$form.Dispose()

if ($res -eq [System.Windows.Forms.DialogResult]::OK) {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    Write-Output $dialog.FileName
}
