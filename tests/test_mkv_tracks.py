import unittest
from autosub.subtitles.mkv import (
    is_forced_or_signs_track,
    is_supported_text_codec,
    select_source_track,
    find_matching_target_track
)

class TestMkvTracks(unittest.TestCase):
    def test_forced_or_signs_track_detection(self):
        # Normal dialogue track
        t_dialogue = {"properties": {"track_name": "Full Dialogue", "forced_track": False}}
        self.assertFalse(is_forced_or_signs_track(t_dialogue))

        # Forced tracks
        t_forced_prop = {"properties": {"forced_track": True}}
        self.assertTrue(is_forced_or_signs_track(t_forced_prop))

        # Track with sign/song keywords in name
        t_signs = {"properties": {"track_name": "Signs & Songs"}}
        self.assertTrue(is_forced_or_signs_track(t_signs))

        t_carteles = {"properties": {"track_name": "Solo Carteles"}}
        self.assertTrue(is_forced_or_signs_track(t_carteles))

    def test_supported_text_codec_filtering(self):
        # Supported text codecs
        self.assertTrue(is_supported_text_codec({"codec": "SubStationAlpha"}))
        self.assertTrue(is_supported_text_codec({"codec": "SubRip/SRT"}))
        self.assertTrue(is_supported_text_codec({"properties": {"codec_id": "S_TEXT/ASS"}}))
        self.assertTrue(is_supported_text_codec({"properties": {"codec_id": "S_TEXT/UTF8"}}))

        # Bitmap codecs (PGS, VobSub) must be rejected
        self.assertFalse(is_supported_text_codec({"codec": "HDMV PGS"}))
        self.assertFalse(is_supported_text_codec({"properties": {"codec_id": "S_IMAGE/PGS"}}))
        self.assertFalse(is_supported_text_codec({"codec": "VobSub"}))

    def test_select_source_track_priority(self):
        sub_tracks = [
            {"id": 1, "codec": "HDMV PGS", "properties": {"language": "ja", "track_name": "Japanese (PGS)"}},
            {"id": 2, "codec": "SubRip/SRT", "properties": {"language": "en", "track_name": "English Dialogue"}},
            {"id": 3, "codec": "SubStationAlpha", "properties": {"language": "ja", "track_name": "Japanese ASS"}},
        ]
        priority = ["ja", "en"]
        selected = select_source_track(sub_tracks, priority)
        # Should pick Japanese ASS (id 3) over PGS (id 1) because PGS is not a text codec
        self.assertIsNotNone(selected)
        self.assertEqual(selected["id"], 3)

    def test_find_matching_target_track(self):
        sub_tracks = [
            {"id": 1, "codec": "SubRip/SRT", "properties": {"language": "en", "track_name": "English"}},
            {"id": 2, "codec": "SubStationAlpha", "properties": {"language": "es", "track_name": "Spanish [LAT]"}},
            {"id": 3, "codec": "SubStationAlpha", "properties": {"language": "es", "track_name": "Castellano (España)"}},
        ]
        # Searching for es-419 should pick Spanish [LAT] (id 2)
        match_lat = find_matching_target_track(sub_tracks, "es-419")
        self.assertIsNotNone(match_lat)
        self.assertEqual(match_lat["id"], 2)

        # Searching for es (Spain) should pick Castellano (id 3)
        match_es = find_matching_target_track(sub_tracks, "es")
        self.assertIsNotNone(match_es)
        self.assertEqual(match_es["id"], 3)

if __name__ == "__main__":
    unittest.main()
