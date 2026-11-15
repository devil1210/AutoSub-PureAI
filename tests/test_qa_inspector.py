import unittest
from autosub.engine.qa import audit_technical_quality, audit_linguistic_heuristics, run_full_subtitle_qa

class TestQAInspector(unittest.TestCase):
    def test_technical_audit_clean(self):
        texts = [
            "Hola, ¿cómo estás?",
            "Estoy muy bien, gracias.",
            "¡Qué alegría verte de nuevo!"
        ]
        durations = [2.0, 2.5, 3.0]
        res = audit_technical_quality(texts, durations)
        self.assertEqual(res["score"], 100.0)
        self.assertEqual(res["lines_exceeded_count"], 0)
        self.assertEqual(res["unbalanced_brackets_count"], 0)

    def test_technical_audit_unbalanced_brackets(self):
        texts = [
            "{\\an8Texto con llave rota",
            "Texto normal"
        ]
        res = audit_technical_quality(texts)
        self.assertTrue(res["score"] < 100.0)
        self.assertEqual(res["unbalanced_brackets_count"], 1)

    def test_linguistic_audit_peninsular(self):
        texts = [
            "Vosotros tenéis que venir al coche.",
            "¡Hostia, qué guay este chaval!"
        ]
        res = audit_linguistic_heuristics(texts)
        self.assertTrue(res["score"] < 100.0)
        self.assertTrue(res["peninsular_count"] >= 3)

    def test_full_qa_without_llm(self):
        raw = ["Hello, Nia!"]
        trans = ["¡Hola, Nia!"]
        durs = [2.0]
        res = run_full_subtitle_qa(raw, trans, durations=durs, run_llm_check=False)
        self.assertEqual(res["grade"], "EXCELENTE")
        self.assertTrue(res["overall_score"] >= 95.0)
        self.assertIn("Fidelidad", res["summary_line"])

    def test_calque_and_typo_heuristics(self):
        texts = [
            "Crescí en una isla flotante.",
            "pequeña señorita Liston.",
            "Esto hace sentido."
        ]
        res = audit_linguistic_heuristics(texts)
        self.assertTrue(res["score"] < 100.0)
        self.assertEqual(res["calques_count"], 3)

if __name__ == "__main__":
    unittest.main()
