# MP3YT Public Inspector

Laboratorio web para inspeccionar el HTML público de https://mp3yt.is/ desde Render. Recoge metadatos, encabezados, campos, formularios y enlaces. No convierte videos, no descarga archivos y no consulta APIs privadas.

## Endpoints
- `GET /`: panel de pruebas.
- `GET /api/health`: estado del servidor.
- `GET /api/scrape`: extracción limitada y caché de 90 segundos.

## Ejecución local
Python 3.10+ sin dependencias adicionales. Ejecutar `python app.py` y abrir `http://localhost:10000`.

## Publicación
Render despliega automáticamente la rama `mp3yt-public-inspector` de `bot-seguridad/hikari-temp-images` en el plan gratuito. El servicio público está en:
https://mp3yt-public-inspector.onrender.com

Esta rama es independiente; no modifica la rama main del repositorio utilizado para las imágenes temporales del bot.

Si el sitio de origen devuelve 403 o 429, el panel mostrará el error y no intentará evadir los controles.
