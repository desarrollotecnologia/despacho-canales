# Crea static/plantilla_impresion_etiquetas.xlsm con la macro ImprimirFilasVisibles
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$out = Join-Path $root "static\plantilla_impresion_etiquetas.xlsm"
$bas = Join-Path $root "static\vba_imprimir_etiquetas.bas"
if (-not (Test-Path $bas)) { throw "No esta $bas" }

$basText = Get-Content -Path $bas -Raw -Encoding UTF8
# Quitar Attribute line para Import
$basImport = Join-Path $env:TEMP "ColbeefEtiquetas.bas"
($basText -replace '(?m)^Attribute VB_Name = .*?\r?\n', '') | Set-Content -Path $basImport -Encoding ASCII

$xl = New-Object -ComObject Excel.Application
$xl.Visible = $false
$xl.DisplayAlerts = $false
try {
  $wb = $xl.Workbooks.Add()
  $ws0 = $wb.Worksheets.Item(1)
  $ws0.Name = "PORTADA"
  $ws0.Range("A1").Value2 = "Colbeef · Etiquetas desde Excel"
  $ws0.Range("A1").Font.Size = 18
  $ws0.Range("A1").Font.Bold = $true
  $ws0.Range("A3").Value2 = "1. Habilita macros (Habilitar contenido)."
  $ws0.Range("A4").Value2 = "2. Ve a la hoja GENERAL (o un OPL)."
  $ws0.Range("A5").Value2 = "3. Filtra por cliente, cava, riel..."
  $ws0.Range("A6").Value2 = "4. Alt+F8 → ImprimirFilasVisibles → Ejecutar"
  $ws0.Range("A7").Value2 = "   (o el boton de abajo si aparece)."
  $ws0.Range("A9").Value2 = "Requiere Zebra Browser Print abierto en este equipo."
  $ws0.Columns.Item(1).ColumnWidth = 80

  $cfg = $wb.Worksheets.Add()
  $cfg.Name = "CONFIG"
  $cfg.Range("A1").Value2 = "API_URL"
  $cfg.Range("B1").Value2 = "http://192.168.20.205:8012"
  $cfg.Range("A2").Value2 = "FECHA"
  $cfg.Range("B2").Value2 = ""
  $cfg.Visible = 2  # xlSheetVeryHidden

  $vbproj = $wb.VBProject
  # Quitar modules viejos si re-run
  foreach ($comp in @($vbproj.VBComponents)) {
    if ($comp.Name -eq "ColbeefEtiquetas") { $vbproj.VBComponents.Remove($comp) }
  }
  $vbproj.VBComponents.Import($basImport)

  # Botón en PORTADA
  $btn = $ws0.Buttons.Add(50, 180, 280, 36)
  $btn.OnAction = "ImprimirFilasVisibles"
  $btn.Text = "Imprimir filas visibles (hoja activa)"

  if (Test-Path $out) { Remove-Item $out -Force }
  # 52 = xlOpenXMLWorkbookMacroEnabled
  $wb.SaveAs($out, 52)
  $wb.Close($false)
  "OK plantilla: $out"
} finally {
  $xl.Quit()
  [System.Runtime.InteropServices.Marshal]::ReleaseComObject($xl) | Out-Null
  Remove-Item $basImport -ErrorAction SilentlyContinue
}
