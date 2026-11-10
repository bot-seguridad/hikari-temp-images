# Bepo Media Lab — MP3YT Inspector + pruebas MP3/MP4

Página: https://mp3yt-public-inspector.onrender.com

## Funciones

1. **Inspección de MP3YT**: `GET /api/scrape` muestra etiquetas y formularios del HTML público de https://mp3yt.is. No inspecciona ni utiliza APIs internas de ese sitio.
2. **Pruebas reales MP3/MP4**: en la portada se pega un enlace individual de YouTube y se elige MP3 (audio) o MP4 (video). El servicio ejecuta `yt-dlp` con FFmpeg y Deno directamente contra YouTube, **no mediante MP3YT**. Se devuelven diagnósticos reales y, si se produjo el archivo, un enlace temporal.
3. **Estado**: `GET /api/health` informa si el servidor está funcionando y si las dependencias del motor están listas.

## Límites y seguridad

- Solo enlaces HTTPS individuales de los dominios permitidos de YouTube; no listas ni direcciones arbitrarias.
- Pruebas para videos de hasta 10 minutos, archivo final máximo 35 MB y MP4 hasta 480p.
- Una conversión simultánea por instancia y tiempo de ejecución máximo 115 segundos.
- Los archivos temporales caducan a los 10 minutos y no son persistentes.
- La prueba puede fallar si YouTube bloquea las solicitudes procedentes de Render, si falta un formato o si cambia su comportamiento. Se muestra el error en el panel.
- Usar solo contenido que tengas permiso de descargar; respetar los derechos y las condiciones de las plataformas.

## Implementación

El proyecto está en la rama `mp3yt-public-inspector` de `bot-seguridad/hikari-temp-images`. La rama principal queda sin cambios.

Runtime: Python 3.14 en Render. Arranque: `python app.py`. Las dependencias de conversión se preparan automáticamente en el arranque de la instancia. El plan de Render es Free.

## Desarrollo local

`python app.py`, luego http://localhost:10000. Para usar el motor de conversión, se necesita conexión de red al iniciar para instalar las dependencias.

## API

- `GET /`: interfaz web.
- `GET /api/health`: estado, motor y errores de instalación.
- `GET /api/scrape`: HTML público, con caché de 90 s.
- `POST /api/convert`: JSON `{"url":"https://www.youtube.com/watch?v=VIDEO_ID","format":"mp3"}` (o `mp4`). Devuelve resultado y enlace temporal, o el error del proceso.
- `GET /api/download/<token>`: descarga del archivo temporal si la conversión terminó correctamente.
