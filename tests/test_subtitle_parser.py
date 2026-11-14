import unittest
from autosub.subtitles.parser import (
    is_translatable_ass_line,
    mask_ass_tags,
    unmask_ass_tags,
    parse_ass_dialogues,
    parse_srt_blocks
)
from autosub.subtitles.serializer import (
    sanitize_subtitle_text,
    format_srt_text
)

class TestSubtitleParser(unittest.TestCase):
    def test_is_translatable_ass_line(self):
        # Translatable lines
        self.assertTrue(is_translatable_ass_line("Default", "¡Hola! ¿Cómo estás?"))
        self.assertTrue(is_translatable_ass_line("Main", "{\\i1}Hello world{\\i0}"))
        self.assertTrue(is_translatable_ass_line("Default", "Line with dialogue\\Nsecond line"))

        # Non-translatable drawing or signs
        self.assertFalse(is_translatable_ass_line("Default", "{\\p1}m 0 0 l 100 0 100 100 0 100{\\p0}"))
        self.assertFalse(is_translatable_ass_line("Default", "{\\pos(10,20)}"))
        self.assertFalse(is_translatable_ass_line("Default", "   "))

    def test_tag_masking_and_unmasking(self):
        original = "{\\pos(192,840)\\fad(200,200)}¡Cuidado, Laios!{\\i1}Viene un monstruo.{\\i0}"
        masked, tag_map = mask_ass_tags(original)

        # Tags should be replaced by placeholders
        self.assertIn("<T0>", masked)
        self.assertIn("<T1>", masked)
        self.assertIn("¡Cuidado, Laios!", masked)

        # Unmasking should restore the exact string byte-for-byte
        restored = unmask_ass_tags(masked, tag_map)
        self.assertEqual(restored, original)

    def test_parse_ass_dialogues(self):
        sample_ass = [
            "[Script Info]\n",
            "Title: Test\n",
            "[Events]\n",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n",
            "Dialogue: 0,0:01:10.00,0:01:12.50,Default,,0,0,0,,¡No mires o te dará una paliza!\n",
            "Dialogue: 0,0:01:13.00,0:01:15.00,Sign,,0,0,0,,{\\pos(10,20)}\n", # Non-translatable
            "Dialogue: 0,0:01:16.00,0:01:18.00,Default,,0,0,0,,{\\i1}Entendido.{\\i0}\n"
        ]

        indices, texts = parse_ass_dialogues(sample_ass)
        self.assertEqual(len(indices), 2)
        self.assertEqual(indices, [4, 6])
        self.assertEqual(texts[0], "¡No mires o te dará una paliza!")
        self.assertEqual(texts[1], "{\\i1}Entendido.{\\i0}")

    def test_parse_srt_blocks(self):
        sample_srt = (
            "1\n00:01:10,000 --> 00:01:12,500\nPrimera línea\nSegunda línea\n\n"
            "2\n00:01:13,000 --> 00:01:15,000\nTercera línea\n"
        )
        cues, texts = parse_srt_blocks(sample_srt)
        self.assertEqual(len(cues), 2)
        self.assertEqual(texts[0], "Primera línea\\NSegunda línea")
        self.assertEqual(texts[1], "Tercera línea")

    def test_sanitize_subtitle_text(self):
        # Broken words after \N
        corrupted = "\\No puedo creer que esté \\Nazonada."
        clean = sanitize_subtitle_text(corrupted)
        self.assertEqual(clean, "\\N No puedo creer que esté \\Nsazonada.")

    def test_format_srt_text(self):
        raw = "{\\an8}Texto arriba\\Nsegunda línea"
        formatted = format_srt_text(raw)
        self.assertEqual(formatted, "Texto arriba\nsegunda línea")

if __name__ == "__main__":
    unittest.main()
