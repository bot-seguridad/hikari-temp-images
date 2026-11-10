# MP3YT · Automatización web en Render

Página: https://mp3yt-public-inspector.onrender.com

Este proyecto prueba **la interfaz pública de https://mp3yt.is**, no YouTube directamente. Para la automatización se usa Playwright con Chromium headless, que intenta hacer lo mismo que haría una persona en la web: abrir MP3YT, pegar una URL de YouTube autorizada, enviar el formulario, seleccionar MP3/MP4 si el sitio lo ofrece y mostrar el resultado.

**No usa yt-dlp, youtube-dl, FFmpeg ni ninguna API interna de MP3YT.** Se eliminó el antiguo motor de conversión. Esta prueba no garantiza que MP3YT acepte la automatización ni que produzca un archivo descargable. No se eluden los CAPTCHAs ni las restricciones.

## Panel

El panel tiene:
- Campo para pegar un enlace HTTPS de YouTube.
- Selector MP3/MP4 y botón de prueba.
- Lista de pasos intentados y errores del navegador.
- Captura de pantalla de MP3YT al finalizar y respuestas HTTP de su dominio.
- Inspector HTML público separado.

## Endpoints

- `GET /`: panel.
- `GET /api/health`: estado de la aplicación y disponibilidad del navegador.
- `GET /api/scrape`: inspección del HTML público, con caché de 90 segundos.
- `POST /api/convert`: JSON `{"url":"https://www.youtube.com/watch?v=VIDEO_ID","format":"mp3"}` o `mp4`. Ejecuta navegación y devuelve pasos/captura/diagnóstico (no una conversión propia).
- `GET /api/screenshot/<token>`: captura PNG temporal.

## Despliegue

El servicio Render usa la rama `mp3yt-public-inspector` de `bot-seguridad/hikari-temp-images` (main permanece sin cambios) y el plan gratuito. El servidor Python `app.py` inicia inmediatamente mientras se prepara Playwright/Chromium en segundo plano. La inicialización puede tardar al arrancar instancias nuevas y puede fallar por dependencias o restricciones del hosting.

## Uso responsable

Solo para videos propios o cuyo uso esté autorizado. Un HTTP 200 indica acceso a la página, no conversión exitosa. Las pruebas son limitadas a una simultánea por instancia. Si el sitio bloquea el navegador, se informa en el diagnóstico sin intentar evadirlo.
