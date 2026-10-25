# Guía de Sincronización Automática de Audio Latino (Nyaa + Telegram)

Herramienta diseñada para transferir pistas de audio latino de fuentes en baja resolución (ej: videos 720p de Telegram) a versiones de máxima calidad (1080p, BDRip o Web-DL de Nyaa), alineando el audio a nivel de milisegundo de forma 100% automática y sin perder calidad de video.

---

## 🛠️ ¿Cómo funciona?

1. **Análisis Acústico (MFCC Cross-Correlation):**
   * Compara las frecuencias espectrales (Música de fondo, efectos de sonido SFX y el Opening) entre ambos archivos usando `audio-offset-finder`.
   * Como los efectos y la música son idénticos tanto en japonés como en español, el algoritmo localiza el pico de coincidencia y obtiene el **desfase exacto** (por ejemplo: `+1.450s` o `-0.820s`).

2. **Remuxing sin Pérdida (MKVToolNix):**
   * En lugar de re-codificar el video (lo que tardaría minutos y degradaría la imagen), utiliza `mkvmerge` para transferir la pista de audio con el parámetro `--sync <id>:<offset_ms>`.
   * Conserva el video 1080p intacto, el audio japonés original, todos los subtítulos y fuentes adjuntas.
   * La pista transferida se etiqueta como `spa` y nombre `"Español Latino"`.
   * **Tiempo por episodio:** ~3 a 6 segundos.

---

## 🌐 Modo Web UI (AutoDub Studio)

Si prefieres usar una interfaz gráfica en tu navegador similar a AutoSub:

```powershell
python tools/sync_anime_dub.py --web
```
O directamente:
```powershell
python tools/sync_web.py
```

* Abre automáticamente en tu navegador: `http://localhost:5056`
* **Selector de Series:** Muestra todas tus series/animes en `E:\Descargas` (o la carpeta que elijas).
* **Inspección de Pistas:** Indica qué capítulos tienen ya audio japonés (`🇯🇵 Audio JP`), subtítulos (`💬 Sub ES`), o si ya tienen audio latino sincronizado (`🎙️ Latino OK`).
* **Selector de Archivos de Telegram con botón `📂`:** Abre el diálogo de archivos nativo de Windows para seleccionar el video o audio descargado sin tener que escribir la ruta a mano.
* **Barra de Progreso y Muxing:** Muestra el desfase calculado y crea el archivo en 3 a 5 segundos con un solo clic en `⚡ Sincronizar`.

---

## 🚀 Uso por Línea de Comandos (CLI)

### 1. Sincronizar un solo capítulo

```powershell
python tools/sync_anime_dub.py "E:\Anime\Nyaa_Ep01_1080p.mkv" "E:\Anime\Telegram_Ep01_720p.mp4"
```

Generará automáticamente:
`E:\Anime\Nyaa_Ep01_1080p [Dual Audio].mkv`

*(Opcional: Si quieres que el audio latino quede como la pista predeterminada al reproducir, añade `--default`).*

---

### 2. Sincronizar una temporada completa (Modo Carpeta)

Si tienes una carpeta con los episodios de Nyaa y otra con los de Telegram:

```powershell
python tools/sync_anime_dub.py --nyaa-dir "E:\Anime\DanMachi S5\Nyaa_1080p" --dub-dir "E:\Anime\DanMachi S5\Telegram_720p" --output "E:\Anime\DanMachi S5\Dual_Audio"
```

El script:
* Detecta los números de capítulo en ambas carpetas (`E01` con `E01`, `02` con `02`, etc.).
* Procesa cada par secuencialmente mostrando el desfase y el nivel de confianza de la alineación.

---

### 3. Modo de Prueba (Dry-Run)

Para verificar qué desfase detecta sin escribir ningún archivo en disco:

```powershell
python tools/sync_anime_dub.py "nyaa.mkv" "telegram.mp4" --dry-run
```

---

## ⚙️ Opciones Avanzadas

| Parámetro | Descripción | Valor por defecto |
| :--- | :--- | :--- |
| `--trim <segundos>` | Segundos a analizar desde el inicio para hallar la correlación. | `360` (6 minutos) |
| `--track-name "<nombre>"` | Título de la pista de audio agregada en el contenedor MKV. | `"Español Latino"` |
| `--lang <código>` | Código ISO de idioma para la pista. | `spa` |
| `--default` | Marca la pista en español latino como la predeterminada al reproducir. | `Desactivado` |
| `--dry-run` | Solo calcula el desfase y muestra estadísticas sin muxear. | `Desactivado` |
