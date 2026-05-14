@echo off
echo Generating self-signed certificate for gateway...

openssl req -x509 -newkey rsa:4096 -nodes ^
    -keyout gateway.key ^
    -out gateway.crt ^
    -days 365 ^
    -subj "/CN=localhost" ^
    -addext "subjectAltName=IP:127.0.0.1"

echo.
echo Done! Files created:
echo   gateway.crt  (share this with edge devices)
echo   gateway.key  (keep this on the gateway only, never share)
echo.
pause