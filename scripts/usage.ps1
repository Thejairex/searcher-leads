# usage.ps1 - Monitoreo de consumo Places API (barato vs caro + cupos gratis)
# Uso:  .\scripts\usage.ps1 [-BaseUrl http://localhost:8001] [-Key dev-key-123] [-Month 2026-09]
param(
    [string]$BaseUrl = "http://localhost:8001",
    [string]$Key = "dev-key-123",
    [string]$Month = ""
)
$H = @{ "X-API-Key" = $Key }
$uri = "$BaseUrl/api/usage"
if ($Month) { $uri += "?month=$Month" }
$data = Invoke-RestMethod -Uri $uri -Headers $H -Method Get

Write-Host "`n=== Uso Places API - Mes: $($data.month) ===" -ForegroundColor Cyan
Write-Host ("{0,-24} {1,8} {2,8} {3,10} {4,12}" -f "SKU", "Llamadas", "Cupo", "Exceso", "Costo USD")
Write-Host ("{0,-24} {1,8} {2,8} {3,10} {4,12}" -f ("-"*24), ("-"*8), ("-"*8), ("-"*10), ("-"*12))
foreach ($s in $data.by_sku) {
    $label = if ($s.sku -eq "text_search_enterprise") { "Barato (TS Enterprise)" } elseif ($s.sku -eq "enterprise_atmosphere") { "Caro (Ent+Atmos)" } else { $s.sku }
    Write-Host ("{0,-24} {1,8} {2,8} {3,10} {4,12}" -f $label, $s.calls, $s.free, $s.chargeable, $s.cost)
}
Write-Host ("{0,-24} {1,8} {2,8} {3,10} {4,12}" -f ("-"*24), ("-"*8), ("-"*8), ("-"*10), ("-"*12))
Write-Host ("{0,-24} {1,8} {2,8} {3,10} {4,12}" -f "TOTAL", "", "", "", $data.total_cost) -ForegroundColor Green
Write-Host ""
if ($data.total_cost -eq 0) {
    Write-Host "Todo dentro del cupo gratis -> \$0 USD" -ForegroundColor Green
} else {
    Write-Host "ATENCION: hay exceso de cupo, costo mensual estimado: \$($data.total_cost) USD" -ForegroundColor Yellow
}