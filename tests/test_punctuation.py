import unittest
from autosub.engine.goldfish import clean_split_dialogue_punctuation

class TestCleanSplitDialoguePunctuation(unittest.TestCase):
    def test_split_question_mark_removed(self):
        # El caso real de Nia Liston:
        # Línea 1 abre pregunta con ¿ y no la cierra
        # Línea 2 vuelve a abrirla redundantemente con ¿
        texts = [
            "¿Te refieres a que...",
            "¿guerreros de todo el reino se enfrentarán\\Nen una prueba de fuerza sangrienta?",
            "Para nada."
        ]
        cleaned = clean_split_dialogue_punctuation(texts)
        self.assertEqual(cleaned[0], "¿Te refieres a que...")
        self.assertEqual(cleaned[1], "guerreros de todo el reino se enfrentarán\\Nen una prueba de fuerza sangrienta?")
        self.assertEqual(cleaned[2], "Para nada.")

    def test_split_question_with_ass_tags(self):
        # Con tags de posicionamiento o estilo ASS
        texts = [
            "{\\an8}¿Acaso crees que...",
            "{\\an8}¿podremos ganar el torneo?"
        ]
        cleaned = clean_split_dialogue_punctuation(texts)
        self.assertEqual(cleaned[0], "{\\an8}¿Acaso crees que...")
        self.assertEqual(cleaned[1], "{\\an8}podremos ganar el torneo?")

    def test_independent_questions_preserved(self):
        # Preguntas independientes completas NO deben alterarse
        texts = [
            "¿Quién es ese sujeto?",
            "¿Acaso tú lo conoces?",
            "¡No puede ser!"
        ]
        cleaned = clean_split_dialogue_punctuation(texts)
        self.assertEqual(cleaned, texts)

    def test_split_exclamation_mark_removed(self):
        # Exclamación dividida en dos líneas
        texts = [
            "¡No puedo creer...",
            "¡que esto esté pasando!"
        ]
        cleaned = clean_split_dialogue_punctuation(texts)
        self.assertEqual(cleaned[0], "¡No puedo creer...")
        self.assertEqual(cleaned[1], "que esto esté pasando!")

if __name__ == "__main__":
    unittest.main()
