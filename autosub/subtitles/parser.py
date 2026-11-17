import re

def is_translatable_ass_line(style_name, raw_text, effect=""):
    # 1. Ignorar estilos de karaoke/romaji/efectos de partículas/créditos de edición
    s_lower = (style_name or "").lower()
    if any(k in s_lower for k in ("romaji", "karaoke", "kara", "effector", "fx", "edits", "édits")):
        return False
    if effect and any(k in str(effect).lower() for k in ("karaoke", "fx", "effector")):
        return False

    # 2. Ignorar dibujos vectoriales ASS (\p1, \p2, etc. en cualquier lugar dentro de las etiquetas ASS)
    if re.search(r"\\p[1-9]", raw_text):
        return False

    # 3. Limpiar tags ASS {...}
    clean = re.sub(r"\{[^}]*\}", "", raw_text).replace("\\N", " ").replace("\\n", " ").strip()
    if not clean:
        return False

    # 4. Ignorar comandos de dibujo vectorial de Aegisub (m 0 0 l 150...)
    if re.match(r"^[mblspc]\s+[-0-9\s]+", clean):
        return False

    # 5. Debe contener al menos 2 letras legibles (alfabéticas)
    letters = re.findall(r"[a-zA-ZáéíóúÁÉÍÓÚñÑüÜàâçéèêëîïôûùÿœæ]", clean)
    if len(letters) < 2:
        return False

    return True

def mask_ass_tags(text: str) -> tuple[str, dict[str, str]]:
    """Enmascara etiquetas ASS entre llaves en tokens neutros <T0>, <T1> para protegerlas del LLM."""
    tag_map = {}
    counter = 0

    def repl(match):
        nonlocal counter
        token = f"<T{counter}>"
        tag_map[token] = match.group(0)
        counter += 1
        return token

    masked = re.sub(r"\{.*?\}", repl, text)
    return masked, tag_map

def unmask_ass_tags(text: str, tag_map: dict[str, str]) -> str:
    """Restaura los tokens <T0>, <T1> a sus etiquetas ASS originales exactas y purga cualquier alucinación."""
    result = text
    if tag_map:
        for token, original in tag_map.items():
            result = result.replace(token, original)
    # Purgar cualquier token o pseudotag <T#> o </T#> inventado por el LLM
    result = re.sub(r"</?T\d+>", "", result)
    return result

def parse_ass_dialogues(lines: list[str]) -> tuple[list[int], list[str]]:
    """Extrae índices de línea y textos de diálogo traducibles de un archivo ASS."""
    d_indices = []
    d_texts = []
    for idx, line in enumerate(lines):
        if line.startswith("Dialogue:"):
            parts = line.split(",", 9)
            if len(parts) == 10:
                s_name = parts[3].strip()
                eff = parts[8].strip()
                t_val = parts[9].rstrip("\r\n")
                if is_translatable_ass_line(s_name, t_val, eff):
                    d_indices.append(idx)
                    d_texts.append(t_val)
    return d_indices, d_texts

def parse_srt_blocks(content: str) -> tuple[list[dict], list[str]]:
    """Extrae bloques de subtítulos y líneas de un archivo SRT."""
    blocks = content.replace("\r\n", "\n").replace("\r", "\n").strip().split("\n\n")
    cues = []
    texts = []
    for b in blocks:
        lines = b.split("\n")
        if len(lines) >= 3 and "-->" in lines[1]:
            cues.append({
                "id": lines[0],
                "time": lines[1],
                "lines": lines[2:]
            })
            texts.append("\\N".join(lines[2:]))
    return cues, texts
