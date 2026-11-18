# AutoSub PureAI v4.0 EGM 🎬✨
> **Motor Cognitivo Elephant-Goldfish, Curador Nocturno Autónomo y Panel de Control de Subtítulos**  
> *Diseñado para servidores multimedia modernos (Plex, Jellyfin, Emby) con integración nativa a Sonarr.*

---

## 🌟 ¿Qué es AutoSub PureAI v4.0?

A diferencia de los traductores automáticos convencionales que traducen líneas aisladas palabra por palabra (perdiendo el género de los personajes, el tono, las muletillas y la coherencia de la trama), **AutoSub PureAI v4.0** opera bajo la arquitectura cognitiva **Elephant-Goldfish (EGM)**:

```
                  ┌────────────────────────────────────────────────────────┐
                  │                 🐘 THE ELEPHANT                        │
                  │  (Memoria a Largo Plazo, Continuidad y Diarización)     │
                  │  • Perfiles por Serie (.profiles/<serie>.json)         │
                  │  • Continuidad episódica y evolución de trama          │
                  │  • Mapeo y atribución de personajes (Hablantes)       │
                  │  • Triage QA Preventivo (0 tokens para subs aprobados) │
                  └──────────────────────────┬─────────────────────────────┘
                                             │ Contexto / Roster / Glosario
                                             ▼
                  ┌────────────────────────────────────────────────────────┐
                  │                 🐟 THE GOLDFISH                        │
                  │   (Traducción Atómica, Alta Velocidad y Tier 2 Critic) │
                  │  • Boundary Slicing Dinámico (40-60 cues optimizados)  │
                  │  • Traducción ultrarrápida (~6-7 min por episodio)     │
                  │  • Tier 2 Self-Critic (Revisión y pulido en tiempo real)│
                  │  • Salida dual sincronizada (.es-419.ass + .es-419.srt) │
                  └──────────────────────────┬─────────────────────────────┘
                                             │ Subtítulos listos
                                             ▼
                  ┌────────────────────────────────────────────────────────┐
                  │                 🏆 QA INSPECTOR & NOTIFICADOR          │
                  │  • Auditoría Técnica (CPS, longitud, sintaxis)        │
                  │  • Auditoría Lingüística (Latino neutro sin calcos)    │
                  │  • Envío de tarjeta enriquecida a Telegram con nota QA │
                  └────────────────────────────────────────────────────────┘
```

---

## 🚀 Nuevas Capacidades y Arquitectura v4.0 EGM

### 1. 🐘 The Elephant (Memoria Narrativa y Diarización de Personajes)
- **Continuidad Episódica**: Mantiene un registro permanente de eventos, relaciones y giros argumentales para que la traducción de cada episodio respete lo ocurrido en capítulos anteriores.
- **Atribución de Hablantes**: Reconoce qué personaje pronuncia cada línea antes de traducir para asegurar coherencia de género (masculino/femenino), formalidad y registro de voz.
- **Triage QA Preventivo**: Si un subtítulo ya existente en disco o en el contenedor supera los 90 puntos en la auditoría de calidad, se valida como aprobado sin gastar un solo token de API.

### 2. 🐟 The Goldfish (Lotes Dinámicos de 40 a 60 Cues)
- **Boundary Slicing Inteligente**: Agrupa los diálogos en bloques naturales respetando pausas y silencios en vez de cortes rígidos.
- **Velocidad Duplicada**: Reduce el número de peticiones por episodio a más de la mitad, logrando tiempos de traducción de **6 a 7 minutos por episodio** (en lugar de 15 minutos).
- **Tier 2 Self-Critic**: Revisa cada lote traducido antes de aceptarlo; si detecta calcos o anomalías, aplica correcciones automáticas de forma instantánea.

### 3. 🌙 Curador y Regularizador Nocturno (21:00 PM)
- **Aprovechamiento de Excedentes de Cuota**: Se ejecuta automáticamente a las **21:00 PM** para regularizar episodios pendientes usando la cuota sobrante del día antes del reinicio diario de Google (05:00 AM).
- **Integración Nativa con Sonarr**: Consulta canónicamente las series autorizadas directamente desde la API de Sonarr, ordenando los episodios en orden cronológico real (S01E01, S01E02...) sin importar formatos dispares de descarga.
- **🛡️ Quota Guard (04:30 AM a 05:05 AM)**: Pausa preventiva automática que congela el procesamiento si la sesión se acerca a las 04:30 AM, garantizando que **nunca se consuma la cuota fresca del día siguiente**.

### 4. 📂 Soporte Integral de Subtítulos Externos y Formatos MP4 / MKV
- **Ingesta Externa Automática**: Si un contenedor `.mp4` o `.mkv` carece de subtítulos incrustados, AutoSub localiza automáticamente el archivo de subtítulos de origen en disco (`.srt`, `.ass`, `.en.srt`, etc.).
- **Generación Dual Completa**: A partir de cualquier archivo de origen (incluso `.srt` plano), genera tanto el `.es-419.srt` como el `.es-419.ass` con cabeceras de resolución 1080p, márgenes cinematográficos y estilo adaptado para Plex, Jellyfin y Emby.
- **Diagnóstico Transparente**: Si un archivo no tiene subtítulos en ningún formato, se registra en el historial con el estado explicativo `Error: Sin subtítulos` en lugar de desaparecer silenciosamente.

### 5. 🔍 Inspector de Calidad Subtitle QA (0 a 100)
Cada subtítulo generado o existente es auditado bajo un baremo estricto:
- **Técnica**: Velocidad de lectura (CPS - Caracteres Por Segundo), líneas desbordadas, corchetes y etiquetas ASS balanceadas.
- **Lingüística / Dialecto**: Eliminación de términos peninsulares ("vosotros", "coged") hacia español latino neutro y detección de calcos del inglés ("para nada", etc.).
- **Semántica**: Fidelidad al diálogo original y consistencia con el perfil de la serie.
- **Medallas**:
  - 🟢 **Excelente (90 - 100)**: Certificado de máxima fidelidad y naturalidad.
  - 🟡 **Aceptable (80 - 89)**: Subtítulo fluido con sugerencias menores.
  - 🔴 **Requiere Revisión (< 80)**: Prioritario para remasterización automática por el Curador.

### 6. 🛡️ Failover Automático y Pool Multi-Cuenta (KeyPool)
- **Rotación Multi-Key**: Soporta múltiples claves de API de Google Gemini separadas por coma en la configuración para alternar automáticamente en caso de agotar la cuota de una cuenta.
- **Instant 503 Guard**: Si Google experimenta picos de alta demanda temporal (HTTP 503), el sistema salta de inmediato al siguiente modelo disponible en el pool sin quedarse colgado esperando reintentos lentos.

### 7. 🎧 Audio Quirúrgico Spot-Check (Ground-Truth Multimodal en Vivo con Gemini 3.5 Flash-Lite)
- **Extracción Milimétrica en Memoria**: Ante líneas con ambigüedad de género en inglés (*"I'm ready"*, *"I'm glad"*, *"To be honest"* $\to$ ¿listo o lista?), expresiones exclamativas cortas o dudas de contexto, FFmpeg extrae directamente a memoria (`pipe:1`, 0.1s) los 2 a 4 segundos de audio del archivo de video sin crear archivos temporales en disco.
- **Multimodalidad Nativa de Gemini**: `gemini-3.5-flash-lite` escucha el fragmento de audio en japonés, identifica el género real del actor o actriz de voz, transcribe el diálogo original (`「ですね。」`) y sugiere la adaptación neutra.
- **Eficiencia Extrema de Tokens**: Resuelve todas las dudas de género y concordancia consumiendo menos de 1.500 tokens de audio por episodio completo (apenas 10 a 20 micro-consultas), preservando intacta la cuota diaria de API.

---

## 🌐 Idiomas de Destino Soportados

| Código | Idioma | Extensiones Generadas |
| :--- | :--- | :--- |
| **es-419** | 🇲🇽 Español Latinoamericano Neutro | `.es-419.ass` / `.es-419.srt` |
| **es** | 🇪🇸 Español de España / Castellano | `.es.ass` / `.es.srt` |
| **en** | 🇬🇧 English | `.en.ass` / `.en.srt` |
| **fr** | 🇫🇷 Français | `.fr.ass` / `.fr.srt` |
| **de** | 🇩🇪 Deutsch | `.de.ass` / `.de.srt` |
| **pt-BR** | 🇧🇷 Português do Brasil | `.pt-br.ass` / `.pt-br.srt` |
| **it** | 🇮🇹 Italiano | `.it.ass` / `.it.srt` |
| **ja** | 🇯🇵 日本語 | `.ja.ass` / `.ja.srt` |
| **ru** | 🇷🇺 Русский | `.ru.ass` / `.ru.srt` |
| **zh-CN** | 🇨🇳 简体中文 | `.zh.ass` / `.zh.srt` |

---

## 🛠️ Requisitos del Sistema

- **Python 3.10+** (probado en Linux Alpine/Ubuntu y Windows con Python 3.10 a 3.14).
- **MKVToolNix** (`mkvmerge` y `mkvextract` instalados y accesibles en el `PATH`).
  - En Debian/Ubuntu: `sudo apt install mkvtoolnix`
  - En Alpine Linux: `apk add mkvtoolnix`
  - En Windows: Añadir la carpeta de MKVToolNix a las Variables de Entorno.
- (Opcional) Clave de API gratuita de Google Gemini (Google AI Studio) y/o servidor Ollama local con GPU.

---

## 📦 Instalación y Puesta en Marcha

1. **Clonar el repositorio**:
   ```bash
   git clone https://github.com/devil1210/AutoSub-PureAI.git
   cd AutoSub-PureAI
   ```

2. **Instalar dependencias**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Iniciar el servicio**:
   ```bash
   python autosub.py
   ```

4. **Acceder al Panel de Control Web**:
   Abre tu navegador en: `http://localhost:5055/` (o la IP de tu servidor Proxmox / LXC / NAS).

---

## ⚙️ Configuración y Programación Nocturna

### Configuración de Sonarr
En la pestaña **Configuración** del panel web, introduce:
- **Sonarr URL**: `http://<ip-sonarr>:8989`
- **Sonarr API Key**: Tu clave de API generada en Sonarr (`Ajustes -> General -> Seguridad`).

### Programar el Curador Nocturno (Cron)
Para ejecutar la regularización automática diaria a las **21:00 PM** con la protección de cuota de las 04:30 AM:
```bash
0 21 * * * python3 /opt/autosub_nightly_curator.py >> /opt/autosub_curator.log 2>&1
```

O para simular la ejecución sin consumir tokens:
```bash
python3 tools/autosub_nightly_curator.py --dry-run
```

---

## 📄 Licencia

Distribuido bajo la Licencia MIT. Consulta el archivo [LICENSE](LICENSE) para más detalles.
