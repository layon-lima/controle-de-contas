# Gera um icone ICO multirresolucao para o atalho Windows.
# Nao usa nem depende do executavel instalado.
param()
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing
$Root = Split-Path -Parent $PSScriptRoot
$Destination = Join-Path $Root "source_current\assets\controle_contas_web.ico"
New-Item -ItemType Directory -Path (Split-Path -Parent $Destination) -Force | Out-Null

function Rounded-Rectangle([single]$X, [single]$Y, [single]$W, [single]$H, [single]$R) {
    $path = [System.Drawing.Drawing2D.GraphicsPath]::new()
    $d = [single]($R * 2)
    $path.AddArc($X, $Y, $d, $d, 180, 90)
    $path.AddArc([single]($X+$W-$d), $Y, $d, $d, 270, 90)
    $path.AddArc([single]($X+$W-$d), [single]($Y+$H-$d), $d, $d, 0, 90)
    $path.AddArc($X, [single]($Y+$H-$d), $d, $d, 90, 90)
    $path.CloseFigure()
    return $path
}

$Sizes = @(16, 32, 48, 256)
$Images = [System.Collections.Generic.List[byte[]]]::new()
foreach ($Size in $Sizes) {
    $Bitmap = [System.Drawing.Bitmap]::new(
        [int]$Size, [int]$Size, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $G = [System.Drawing.Graphics]::FromImage($Bitmap)
    $G.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $G.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $G.ScaleTransform([single]($Size / 256.0), [single]($Size / 256.0))
    $Navy = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::FromArgb(255, 19, 69, 123))
    $Blue = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::FromArgb(255, 31, 119, 210))
    $White = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::White)
    $Line = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::FromArgb(255, 149, 183, 215))
    $Green = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::FromArgb(255, 5, 153, 107))
    $CheckPen = [System.Drawing.Pen]::new([System.Drawing.Color]::White, [single]13)
    try {
        $Back = Rounded-Rectangle 8 8 240 240 50
        $Face = Rounded-Rectangle 18 18 220 220 44
        $Paper = Rounded-Rectangle 64 43 126 171 15
        try {
            $G.FillPath($Navy, $Back)
            $G.FillPath($Blue, $Face)
            $G.FillPath($White, $Paper)
            $G.FillRectangle($Line, [single]86, [single]87, [single]80, [single]9)
            $G.FillRectangle($Line, [single]86, [single]112, [single]72, [single]9)
            $G.FillRectangle($Line, [single]86, [single]137, [single]57, [single]9)
            $G.FillEllipse($White, [single]155, [single]151, [single]81, [single]81)
            $G.FillEllipse($Green, [single]160, [single]156, [single]71, [single]71)
            $CheckPen.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
            $CheckPen.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
            $CheckPen.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round
            $G.DrawLines($CheckPen, [System.Drawing.PointF[]]@(
                [System.Drawing.PointF]::new(176,192),
                [System.Drawing.PointF]::new(190,205),
                [System.Drawing.PointF]::new(215,180)
            ))
        } finally { $Paper.Dispose(); $Face.Dispose(); $Back.Dispose() }
        $Png = [System.IO.MemoryStream]::new()
        try {
            $Bitmap.Save($Png, [System.Drawing.Imaging.ImageFormat]::Png)
            $Images.Add($Png.ToArray())
        } finally { $Png.Dispose() }
    } finally {
        $CheckPen.Dispose()
        $Navy.Dispose(); $Blue.Dispose(); $White.Dispose(); $Line.Dispose(); $Green.Dispose()
        $G.Dispose(); $Bitmap.Dispose()
    }
}
$Stream = [System.IO.File]::Open($Destination, [System.IO.FileMode]::Create)
$Writer = [System.IO.BinaryWriter]::new($Stream)
try {
    $Writer.Write([uint16]0)       # reservado
    $Writer.Write([uint16]1)       # imagem ICO
    $Writer.Write([uint16]$Sizes.Count)
    $Offset = [uint32](6 + 16 * $Sizes.Count)
    for ($i = 0; $i -lt $Sizes.Count; $i++) {
        $data = $Images[$i]
        $Writer.Write([byte]($Sizes[$i] % 256))
        $Writer.Write([byte]($Sizes[$i] % 256))
        $Writer.Write([byte]0)
        $Writer.Write([byte]0)
        $Writer.Write([uint16]1)
        $Writer.Write([uint16]32)
        $Writer.Write([uint32]$data.Length)
        $Writer.Write([uint32]$Offset)
        $Offset = [uint32]($Offset + $data.Length)
    }
    foreach ($data in $Images) { $Writer.Write([byte[]]$data) }
} finally { $Writer.Dispose(); $Stream.Dispose() }
if (-not (Test-Path -LiteralPath $Destination -PathType Leaf)) {
    throw "Nao foi possivel gerar o icone Windows."
}
Write-Host "Icone de atalho web gerado: $Destination"
