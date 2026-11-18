import unittest
from unittest.mock import patch, MagicMock

from autosub.engine.audio_probe import (
    is_audio_spotcheck_enabled,
    detect_ambiguous_cues,
    build_roster_summary_for_audio,
    extract_audio_slice,
    probe_cue_ground_truth,
    refine_dialogue_with_audio_spotcheck
)

class TestAudioProbe(unittest.TestCase):

    def test_is_audio_spotcheck_enabled(self):
        self.assertTrue(is_audio_spotcheck_enabled())

    def test_detect_ambiguous_cues(self):
        raw_texts = [
            "Good morning.",
            "I'm glad you could make it.",  # Ambiguo (glad)
            "The weather is nice today.",
            "To be honest, I was surprised.",  # Ambiguo (honest, surprised)
            "Yes.",  # Corto sin hablante
            "None of you can defeat me."  # Ambiguo (none of you)
        ]
        speakers = ["Nia Liston", "", "Narrator", "", "", "Gnadolph"]

        cues = detect_ambiguous_cues(raw_texts, speakers, max_count=5)
        # Debería detectar al menos las líneas 1 (glad), 3 (honest/surprised) y 5 (none of you)
        self.assertIn(1, cues)
        self.assertIn(3, cues)
        self.assertIn(5, cues)
        self.assertLessEqual(len(cues), 5)

    def test_build_roster_summary_for_audio(self):
        profile = {
            "characters": [
                {"name": "Nia Liston", "gender": "Femenino", "personality": "Niña noble prodigio"},
                {"name": "Gnadolph", "gender": "Masculino", "personality": "Maestro de dojo veterano"}
            ]
        }
        summary = build_roster_summary_for_audio(profile)
        self.assertIn("Nia Liston (Femenino)", summary)
        self.assertIn("Gnadolph (Masculino)", summary)

    def test_extract_audio_slice_nonexistent_file(self):
        res = extract_audio_slice("nonexistent_video_path.mkv", 10.0, 15.0)
        self.assertIsNone(res)

    @patch("autosub.engine.audio_probe.extract_audio_slice")
    @patch("autosub.engine.audio_probe.call_gemini_audio")
    def test_probe_cue_ground_truth(self, mock_gemini, mock_extract):
        mock_extract.return_value = b"RIFFfake_wav_data" * 200
        mock_gemini.return_value = '{"speaker_name": "Nia Liston", "speaker_gender": "femenino", "transcript_ja": "ですね。", "translation_es": "Así es.", "confidence": "alta"}'

        res = probe_cue_ground_truth("dummy.mkv", 10.0, 13.0, "Being honest...", "Nia", "Roster")
        self.assertIsNotNone(res)
        self.assertEqual(res.get("speaker_name"), "Nia Liston")
        self.assertEqual(res.get("speaker_gender"), "femenino")
        self.assertEqual(res.get("transcript_ja"), "ですね。")
        self.assertEqual(res.get("translation_es"), "Así es.")

    @patch("autosub.engine.audio_probe.probe_cue_ground_truth")
    @patch("os.path.isfile")
    def test_refine_dialogue_with_audio_spotcheck(self, mock_isfile, mock_probe):
        mock_isfile.return_value = True
        mock_probe.return_value = {
            "speaker_name": "Nia Liston",
            "speaker_gender": "femenino",
            "transcript_ja": "ですね。",
            "translation_es": "Es verdad.",
            "confidence": "alta"
        }

        timestamps = [(10.0, 13.0), (20.0, 23.0)]
        raw_texts = ["To be honest, I was surprised.", "Let's go."]
        speakers = ["", "Lydie"]
        profile = {"series_title": "Nia"}

        refined_speakers, ground_truths = refine_dialogue_with_audio_spotcheck(
            "dummy.mkv",
            timestamps,
            raw_texts,
            speakers,
            profile,
            max_spotchecks=2
        )

        self.assertEqual(refined_speakers[0], "Nia Liston")
        self.assertIn(0, ground_truths)
        self.assertEqual(ground_truths[0]["gender"], "femenino")

if __name__ == "__main__":
    unittest.main()
