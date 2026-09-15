@echo off
rem OpenTacho.exe + tasinabilir klasor uretir:  dist\OpenTacho\  (ve dist\OpenTacho-win64.zip)
rem Gerekli: pip install pyinstaller pywebview reportlab qrcode
setlocal
cd /d "%~dp0\.."

python -m PyInstaller --noconfirm --clean --windowed --name OpenTacho ^
  --icon app\assets\logo.ico ^
  --add-data "app;app" ^
  --add-data "lang;lang" ^
  --add-binary "lib\SII_Decrypt.dll;lib" ^
  --collect-all webview ^
  --collect-all reportlab ^
  --collect-submodules qrcode ^
  --exclude-module numpy --exclude-module tkinter --exclude-module lxml ^
  --exclude-module PIL._avif --exclude-module PIL.AvifImagePlugin --exclude-module PIL._webp --exclude-module PIL.WebPImagePlugin ^
  --exclude-module PIL._imagingcms --exclude-module PIL.ImageCms --exclude-module PIL._imagingtk --exclude-module PIL.ImageTk --exclude-module PIL.ImageQt ^
  OpenTacho.py
if errorlevel 1 (echo Build failed & exit /b 1)

rem Kullanicinin oyuna kopyalayacagi eklenti + lisanslar + README'ler exe'nin yanina
xcopy /E /I /Y plugin dist\OpenTacho\plugin >nul
xcopy /E /I /Y third_party dist\OpenTacho\third_party >nul
copy /Y LICENSE dist\OpenTacho\ >nul
copy /Y README.md dist\OpenTacho\ >nul
copy /Y README.tr.md dist\OpenTacho\ >nul

rem Test calistirmasindan kalan kullanici verisi pakete girmesin
for %%f in (state.json history.json error.log custom_bg.img) do if exist dist\OpenTacho\%%f del dist\OpenTacho\%%f
for %%d in (profiles exports) do if exist dist\OpenTacho\%%d rd /s /q dist\OpenTacho\%%d
if exist dist\OpenTacho-win64.zip del dist\OpenTacho-win64.zip
powershell -NoProfile -Command "Compress-Archive -Path 'dist\OpenTacho\*' -DestinationPath 'dist\OpenTacho-win64.zip' -CompressionLevel Optimal"

echo.
echo Cikti: dist\OpenTacho\OpenTacho.exe   ve   dist\OpenTacho-win64.zip
endlocal
