param(
    [string]$InitialPath = ''
)
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = 'Selecione a pasta para criar o atalho no Jarvis'
$dialog.ShowNewFolderButton = $true
if ($InitialPath -and (Test-Path -Path $InitialPath -PathType Container)) {
    $dialog.SelectedPath = $InitialPath
}

$form = New-Object System.Windows.Forms.Form
$form.TopMost = $true

$res = $dialog.ShowDialog($form)
if ($res -eq [System.Windows.Forms.DialogResult]::OK) {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    Write-Output $dialog.SelectedPath
}
