import unittest
from autosub.engine.elephant import (
    build_scene_brief,
    estimate_tokens,
    extract_series_name,
    extract_episode_label
)

class TestElephantMemory(unittest.TestCase):
    def setUp(self):
        self.sample_profile = {
            "series_title": "Dungeon Meshi",
            "official_title": "Delicious in Dungeon",
            "setting": "Un grupo de aventureros explora una mazmorra fantástica mientras cocinan monstruos para sobrevivir.",
            "characters": [
                {"name": "Laios", "gender": "Masculino", "personality": "Líder apasionado por los monstruos, tono curioso y directo"},
                {"name": "Marcille", "gender": "Femenino", "personality": "Maga elfa estudiosa, quejumbrosa y prudente"},
                {"name": "Chilchuck", "gender": "Masculino", "personality": "Cerrajero semi-humano pragmático y cínico"},
                {"name": "Senshi", "gender": "Masculino", "personality": "Enano cocinero experto, tradicional y sereno"},
                {"name": "Falin", "gender": "Femenino", "personality": "Hermana bondadosa de Laios"}
            ],
            "glossary": {
                "Red Dragon": "Dragón Rojo",
                "Living Armor": "Armadura Viviente",
                "Mandrake": "Mandrágora",
                "Slime": "Limo",
                "Kelpie": "Kelpie",
                "Basilisk": "Basilisco"
            },
            "tone_instructions": "Español latinoamericano neutro con terminología fantástica coherente."
        }

    def test_build_scene_brief_token_bound(self):
        batch = [
            "Laios, look at that Red Dragon!",
            "Is it edible, Senshi?",
            "We must prepare the Mandrake carefully."
        ]
        prev_ctx = ["We entered the fourth floor.", "Watch your step."]
        brief = build_scene_brief(self.sample_profile, batch, prev_context=prev_ctx, max_tokens=350)

        # Must not exceed 350 tokens
        tokens = estimate_tokens(brief)
        self.assertLessEqual(tokens, 350)
        self.assertIn("Dungeon Meshi", brief)
        self.assertIn("Laios", brief)
        self.assertIn("Marcille", brief)

    def test_glossary_filtering_only_matches_batch(self):
        # Only "Red Dragon" and "Mandrake" are in batch, "Living Armor" is not
        batch = [
            "We found a Mandrake under the stone.",
            "Beware of the red dragon nearby!"
        ]
        brief = build_scene_brief(self.sample_profile, batch, max_tokens=350)

        self.assertIn("'Red Dragon' -> 'Dragón Rojo'", brief)
        self.assertIn("'Mandrake' -> 'Mandrágora'", brief)
        # Unmatched terms should not appear in brief
        self.assertNotIn("Living Armor", brief)
        self.assertNotIn("Kelpie", brief)

    def test_truncation_hierarchy_huge_profile(self):
        # Create a profile with 30 characters and verbose descriptions
        huge_profile = dict(self.sample_profile)
        huge_profile["characters"] = [
            {"name": f"Character_{i}", "gender": "Masculino", "personality": "Descripción extremadamente detallada y redundante con muchos términos largos para probar el límite de tokens."}
            for i in range(30)
        ]
        batch = ["Hello world"]
        brief = build_scene_brief(huge_profile, batch, max_tokens=350)

        tokens = estimate_tokens(brief)
        self.assertLessEqual(tokens, 350)

    def test_extract_series_and_episode(self):
        video_path = r"E:\Anime\Dungeon Meshi\Season 01\Dungeon.Meshi.S01E05.1080p.mkv"
        series = extract_series_name(video_path)
        ep = extract_episode_label(video_path)
        self.assertEqual(series, "Dungeon Meshi")
        self.assertEqual(ep, "S01E05")

if __name__ == "__main__":
    unittest.main()
