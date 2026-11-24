param(
    [string]$InitialPath = ''
)
[System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms') | Out-Null
[System.Reflection.Assembly]::LoadWithPartialName('System.Drawing') | Out-Null
[System.Windows.Forms.Application]::EnableVisualStyles()

$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = 'Selecione a pasta para criar o atalho no Jarvis'
$dialog.ShowNewFolderButton = $true
if ($InitialPath -and (Test-Path -LiteralPath $InitialPath -PathType Container)) {
    $dialog.SelectedPath = $InitialPath
}

# Cria um Form invisível, mas com TopMost e foco garantido para trazer o diálogo para a frente de tudo
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
    Write-Output $dialog.SelectedPath
}
