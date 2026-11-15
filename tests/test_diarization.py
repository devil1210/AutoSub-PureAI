import os
import tempfile
import unittest
from unittest.mock import patch

from autosub.engine.diarization import (
    get_speaker_cache_path,
    load_cached_speakers,
    save_cached_speakers,
    build_character_roster_summary,
    attribute_episode_speakers
)

class TestDiarization(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.video_path = os.path.join(self.temp_dir, "Series - S01E01.mkv")

    def test_cache_path(self):
        cache_path = get_speaker_cache_path(self.video_path)
        self.assertTrue(cache_path.endswith(".speakers.json"))
        self.assertIn(".speakers", cache_path)

    def test_save_and_load_cache(self):
        speakers = ["Nia", "Rinokis", "Leliared"]
        saved = save_cached_speakers(self.video_path, speakers)
        self.assertTrue(saved)

        loaded = load_cached_speakers(self.video_path, expected_count=3)
        self.assertEqual(loaded, speakers)

        # Si el conteo esperado difiere, debe retornar None
        self.assertIsNone(load_cached_speakers(self.video_path, expected_count=5))

    def test_roster_summary(self):
        profile = {
            "characters": [
                {"name": "Nia Liston", "gender": "Femenino", "personality": "Guerrera intrépida"},
                {"name": "Rinokis", "gender": "Femenino", "personality": "Sirvienta devota"}
            ]
        }
        summary = build_character_roster_summary(profile)
        self.assertIn("Nia Liston", summary)
        self.assertIn("Rinokis", summary)
        self.assertIn("Femenino", summary)

    @patch("autosub.engine.diarization.call_ai")
    def test_attribute_episode_speakers(self, mock_ai):
        mock_ai.return_value = '{"speakers": ["Nia", "Rinokis"]}'
        raw = ["Hello", "Yes, young mistress"]
        profile = {"series_title": "Nia Liston", "characters": [{"name": "Nia"}, {"name": "Rinokis"}]}

        res = attribute_episode_speakers(self.video_path, raw, profile)
        self.assertEqual(len(res), 2)
        self.assertEqual(res, ["Nia", "Rinokis"])

if __name__ == "__main__":
    unittest.main()
