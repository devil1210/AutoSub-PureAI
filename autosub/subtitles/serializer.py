import re

def sanitize_subtitle_text(text: str) -> str:
    if not text:
        return ""
    # Corregir palabras mutiladas comunes tras saltos \N o etiquetas de control
    # 1. 'azonada' -> 'sazonada'
    text = re.sub(r'\\N\s*azonad([a-zñáéíóú]*)', r'\\Nsazonad\1', text, flags=re.IGNORECASE)
    # 2. '\No puedo', '\No es', '\No hay', etc. (donde el salto \N se comió el espacio de 'No')
    text = re.sub(r'\\No\s+(pued|sé|se\b|te\b|me\b|le\b|lo\b|la\b|los\b|las\b|nos\b|es\b|son\b|era\b|hay\b|teng|tien|quier|voy|vas|va|vam|van|import|pas|deb|est|hag|dig|cre|val|qued|tem|olvid)', r'\\N No \1', text, flags=re.IGNORECASE)
    # 3. '\No' pegado sin espacio a verbos de negación
    text = re.sub(r'\\No(puedo|pude|podemos|podrá|sabía|sé|tengo|tiene|tenemos|quiero|quiere|queremos|voy|vamos|importa|pasa|debo|debe|debemos|estoy|está|estamos|hago|hace|hacemos|digo|dice|creo|queda|temas)', r'\\N No \1', text, flags=re.IGNORECASE)
    return text

def format_srt_text(raw_text: str) -> str:
    clean = sanitize_subtitle_text(raw_text.strip())
    clean = re.sub(r'\{.*?\}', '', clean)
    clean = clean.replace('\\N', '\n').replace('\\n', '\n').strip()
    return clean

def save_ass_and_srt_outputs(out_ass_path, out_srt_path, original_ass_lines, dialogue_indices, final_texts, speakers=None):
    """Guarda los subtítulos ASS actualizados y genera el SRT correspondiente."""
    new_lines = list(original_ass_lines)
    for idx_dialogue, real_line_idx in enumerate(dialogue_indices):
        if idx_dialogue < len(final_texts):
            parts = new_lines[real_line_idx].split(",", 9)
            if len(parts) == 10:
                if speakers and idx_dialogue < len(speakers) and speakers[idx_dialogue]:
                    parts[4] = str(speakers[idx_dialogue]).strip()
                new_lines[real_line_idx] = ",".join(parts[:9]) + "," + sanitize_subtitle_text(final_texts[idx_dialogue]) + "\n"

    with open(out_ass_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    with open(out_srt_path, "w", encoding="utf-8") as f:
        c_idx = 1
        for real_line_idx in dialogue_indices:
            line = new_lines[real_line_idx]
            parts = line.split(",", 9)
            start, end = parts[1], parts[2]
            srt_txt = format_srt_text(parts[9])
            if not srt_txt:
                continue
            m1 = re.match(r"(\d+):(\d{2}):(\d{2})\.(\d{2})", start)
            m2 = re.match(r"(\d+):(\d{2}):(\d{2})\.(\d{2})", end)
            if m1 and m2:
                s_fmt = f"{int(m1.group(1)):02d}:{m1.group(2)}:{m1.group(3)},{int(m1.group(4))*10:03d}"
                e_fmt = f"{int(m2.group(1)):02d}:{m2.group(2)}:{m2.group(3)},{int(m2.group(4))*10:03d}"
                f.write(f"{c_idx}\n{s_fmt} --> {e_fmt}\n{srt_txt}\n\n")
                c_idx += 1

def save_srt_output(out_srt_path, cues, final_texts):
    """Guarda subtítulos en formato SRT a partir de bloques cue."""
    with open(out_srt_path, "w", encoding="utf-8") as f:
        for idx, c in enumerate(cues):
            text = final_texts[idx] if idx < len(final_texts) else "\\N".join(c.get("lines", []))
            text = format_srt_text(text)
            f.write(f"{idx + 1}\n{c['time']}\n{text}\n\n")

def save_cues_as_ass_and_srt(out_ass_path, out_srt_path, cues, final_texts, speakers=None):
    """Guarda subtítulos a partir de cues SRT generando tanto el archivo SRT como el archivo ASS estilizado."""
    save_srt_output(out_srt_path, cues, final_texts)

    header = """[Script Info]
Title: AutoSub Pure AI Subtitle
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,50,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2.5,1.5,2,40,40,35,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ass_lines = [header]

    def _srt_time_to_ass(t_str):
        p = t_str.strip().replace(",", ".")
        parts = p.split(":")
        if len(parts) == 3:
            h = int(parts[0])
            m = parts[1]
            s_parts = parts[2].split(".")
            s = s_parts[0]
            ms = s_parts[1][:2] if len(s_parts) > 1 else "00"
            return f"{h}:{m}:{s}.{ms}"
        return p

    for idx, c in enumerate(cues):
        t_span = c.get("time", "")
        if "-->" not in t_span:
            continue
        p = t_span.split("-->")
        start_ass = _srt_time_to_ass(p[0])
        end_ass = _srt_time_to_ass(p[1])
        spk = str(speakers[idx]).strip() if (speakers and idx < len(speakers) and speakers[idx]) else ""
        raw_text = final_texts[idx] if idx < len(final_texts) else "\\N".join(c.get("lines", []))
        clean_text = sanitize_subtitle_text(raw_text.replace("\n", "\\N").replace("\r", ""))
        ass_lines.append(f"Dialogue: 0,{start_ass},{end_ass},Default,{spk},0,0,0,,{clean_text}\n")

    with open(out_ass_path, "w", encoding="utf-8") as f:
        f.writelines(ass_lines)

