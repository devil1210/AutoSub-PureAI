import unittest
from autosub.engine.goldfish import (
    calculate_cps,
    smart_boundary_slicing,
    tier1_validate_batch,
    ass_timestamp_to_seconds,
    srt_timestamp_to_seconds
)
from autosub.subtitles.parser import mask_ass_tags, unmask_ass_tags

class TestGoldfishQuality(unittest.TestCase):
    def test_calculate_cps_arithmetic(self):
        # 30 chars in 2.0 seconds = 15.0 CPS (comfortable)
        text = "Esta es una frase normalita."
        cps = calculate_cps(text, 2.0)
        self.assertAlmostEqual(cps, len(text) / 2.0, places=2)

        # Tags and line breaks must be ignored from character count
        tagged_text = "{\\pos(100,200)}Hola mundo\\Nsegunda parte"
        # "Hola mundo segunda parte" = 24 chars
        cps_tagged = calculate_cps(tagged_text, 2.0)
        self.assertAlmostEqual(cps_tagged, 24 / 2.0, places=1)

        # Masked AST tokens <T0> must also be ignored from CPS
        masked_text = "<T0>Hola mundo<T1>\\Nsegunda parte"
        cps_masked = calculate_cps(masked_text, 2.0)
        self.assertAlmostEqual(cps_masked, 24 / 2.0, places=1)

    def test_calculate_cps_zero_duration(self):
        self.assertEqual(calculate_cps("Texto", 0.0), 0.0)
        self.assertEqual(calculate_cps("Texto", -1.0), 0.0)

    def test_timestamp_conversions(self):
        self.assertAlmostEqual(ass_timestamp_to_seconds("0:01:23.45"), 83.45, places=2)
        self.assertAlmostEqual(ass_timestamp_to_seconds("1:00:00.00"), 3600.0, places=2)
        self.assertAlmostEqual(srt_timestamp_to_seconds("00:01:23,450"), 83.45, places=2)
        self.assertEqual(ass_timestamp_to_seconds("invalid"), 0.0)

    def test_smart_boundary_slicing_basic(self):
        # 50 items should be split into 2 batches (min 20, max 35)
        items = [f"Line {i}." for i in range(50)]
        slices = smart_boundary_slicing(items, min_chunk=20, max_chunk=35)
        self.assertGreater(len(slices), 1)
        total_items = sum(len(s) for s in slices)
        self.assertEqual(total_items, 50)
        for s in slices:
            self.assertLessEqual(len(s), 35)

    def test_smart_boundary_slicing_punctuation(self):
        # 52 items total: line 22 ends with punctuation, leaving 30 items in slice 2
        items = ["Continuación de diálogo sin punto" for _ in range(21)]
        items.append("Esta línea sí termina en punto final.")
        items.extend(["Siguiente diálogo" for _ in range(30)])

        slices = smart_boundary_slicing(items, min_chunk=20, max_chunk=35)
        # Should cut at line 22 (index 21 + 1 = 22 items), slice 2 will have 30 items
        self.assertEqual(len(slices), 2)
        self.assertEqual(len(slices[0]), 22)
        self.assertEqual(len(slices[1]), 30)

    def test_tier1_validate_batch_valid(self):
        raw = ["Hello world", "<T0>Take this!<T1>"]
        trans = ["Hola mundo", "<T0>¡Toma esto!<T1>"]
        tag_maps = [{}, {"<T0>": "{\\b1}", "<T1>": "{\\b0}"}]
        durations = [2.0, 2.0]

        defects = tier1_validate_batch(raw, trans, tag_maps=tag_maps, durations=durations)
        self.assertEqual(len(defects), 0)

    def test_tier1_validate_line_count_mismatch(self):
        raw = ["Line 1", "Line 2"]
        trans = ["Línea 1"]
        defects = tier1_validate_batch(raw, trans)
        self.assertEqual(len(defects), 1)
        self.assertEqual(defects[0]["type"], "COUNT_MISMATCH")

    def test_tier1_validate_tag_missing(self):
        raw = ["<T0>Attack!<T1>"]
        trans = ["<T0>¡Ataque!"] # Missing <T1>
        tag_maps = [{"<T0>": "{\\i1}", "<T1>": "{\\i0}"}]
        defects = tier1_validate_batch(raw, trans, tag_maps=tag_maps)
        self.assertEqual(len(defects), 1)
        self.assertEqual(defects[0]["type"], "TAG_MISSING")

    def test_tier1_validate_bracket_unbalanced(self):
        raw = ["Test line"]
        trans = ["Texto {con llave abierta"]
        defects = tier1_validate_batch(raw, trans)
        self.assertEqual(len(defects), 1)
        self.assertEqual(defects[0]["type"], "BRACKET_UNBALANCED")

    def test_tier1_validate_cps_exceeded(self):
        raw = ["Short line"]
        # 60 chars in 2.0 seconds = 30 CPS (> 20 CPS limit for >1.5s)
        trans = ["Este es un texto excesivamente largo para dos segundos de duración."]
        durations = [2.0]
        defects = tier1_validate_batch(raw, trans, durations=durations)
        self.assertEqual(len(defects), 1)
        self.assertEqual(defects[0]["type"], "CPS_EXCEEDED")

    def test_ast_tag_masking_round_trip(self):
        original = "{\\pos(192,200)\\an8}Texto importante con {\\b1}negrita{\\b0}"
        masked, tag_map = mask_ass_tags(original)
        self.assertIn("<T0>", masked)
        self.assertIn("<T1>", masked)
        self.assertIn("<T2>", masked)
        self.assertEqual(tag_map["<T0>"], "{\\pos(192,200)\\an8}")
        self.assertEqual(tag_map["<T1>"], "{\\b1}")
        self.assertEqual(tag_map["<T2>"], "{\\b0}")

        # Simulate LLM preserving tokens
        translated_sim = "<T0>Texto importante con <T1>negrita<T2>"
        restored = unmask_ass_tags(translated_sim, tag_map)
        self.assertEqual(restored, "{\\pos(192,200)\\an8}Texto importante con {\\b1}negrita{\\b0}")

    def test_strip_llm_label_prefix_handles_traduccion_actual(self):
        from autosub.engine.goldfish import strip_llm_label_prefix, clean_split_dialogue_punctuation
        self.assertEqual(strip_llm_label_prefix("Traducción actual: Un final que parece una broma."), "Un final que parece una broma.")
        self.assertEqual(strip_llm_label_prefix("Original: A joke => Traducción actual: Una broma."), "Una broma.")
        self.assertEqual(strip_llm_label_prefix("{\\i1}Traducción actual: Hola"), "{\\i1}Hola")
        cleaned = clean_split_dialogue_punctuation(["Traducción actual: No puedo sonreír."])
        self.assertEqual(cleaned, ["No puedo sonreír."])

if __name__ == "__main__":
    unittest.main()
