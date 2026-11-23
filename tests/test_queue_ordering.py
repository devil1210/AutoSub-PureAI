import unittest
from autosub.core.queue import sort_jobs_canonically

class TestQueueCanonicalOrdering(unittest.TestCase):
    def test_single_series_emission_order(self):
        # Shuffled files from the user's active series
        input_jobs = [
            ("/data/tvshows/The World's Finest Assassin/Season 1/[EMBER] Sekai Saikou no Ansatsusha S01E08.mkv", True),
            ("/data/tvshows/The World's Finest Assassin/Season 1/[EMBER] Sekai Saikou no Ansatsusha S01E07.mkv", True),
            ("/data/tvshows/The World's Finest Assassin/Season 1/[EMBER] Sekai Saikou no Ansatsusha S01E01.mkv", True),
            ("/data/tvshows/The World's Finest Assassin/Season 1/[EMBER] Sekai Saikou no Ansatsusha S01E09.mkv", True),
            ("/data/tvshows/The World's Finest Assassin/Season 1/[EMBER] Sekai Saikou no Ansatsusha S01E02.mkv", True),
            ("/data/tvshows/The World's Finest Assassin/Season 1/[EMBER] Sekai Saikou no Ansatsusha S01E03.mkv", True),
            ("/data/tvshows/The World's Finest Assassin/Season 1/[EMBER] Sekai Saikou no Ansatsusha S01E04.mkv", True),
            ("/data/tvshows/The World's Finest Assassin/Season 1/[EMBER] Sekai Saikou no Ansatsusha S01E11.mkv", True),
            ("/data/tvshows/The World's Finest Assassin/Season 1/[EMBER] Sekai Saikou no Ansatsusha S01E12.mkv", True),
            ("/data/tvshows/The World's Finest Assassin/Season 1/[EMBER] Sekai Saikou no Ansatsusha S01E05.mkv", True),
            ("/data/tvshows/The World's Finest Assassin/Season 1/[EMBER] Sekai Saikou no Ansatsusha S01E10.mkv", True),
            ("/data/tvshows/The World's Finest Assassin/Season 1/[EMBER] Sekai Saikou no Ansatsusha S01E06.mkv", True),
        ]

        sorted_jobs = sort_jobs_canonically(input_jobs)
        expected_eps = [f"S01E{i:02d}" for i in range(1, 13)]
        actual_eps = [
            f"S01E{int(j[0].split('S01E')[1][:2]):02d}" for j in sorted_jobs
        ]
        self.assertEqual(actual_eps, expected_eps)

    def test_multi_series_fifo_and_emission_order(self):
        input_jobs = [
            ("/data/tvshows/SeriesBeta/Season 1/Beta S01E03.mkv", False),
            ("/data/tvshows/SeriesAlpha/Season 1/Alpha S01E02.mkv", False),
            ("/data/tvshows/SeriesBeta/Season 1/Beta S01E01.mkv", False),
            ("/data/tvshows/SeriesAlpha/Season 1/Alpha S01E01.mkv", False),
        ]

        sorted_jobs = sort_jobs_canonically(input_jobs)
        actual_paths = [j[0] for j in sorted_jobs]
        expected_paths = [
            "/data/tvshows/SeriesBeta/Season 1/Beta S01E01.mkv",
            "/data/tvshows/SeriesBeta/Season 1/Beta S01E03.mkv",
            "/data/tvshows/SeriesAlpha/Season 1/Alpha S01E01.mkv",
            "/data/tvshows/SeriesAlpha/Season 1/Alpha S01E02.mkv",
        ]
        self.assertEqual(actual_paths, expected_paths)

    def test_bare_string_paths(self):
        input_paths = [
            "/data/tvshows/Anime/Season 1/Ep 10.mkv",
            "/data/tvshows/Anime/Season 1/Ep 02.mkv",
            "/data/tvshows/Anime/Season 1/Ep 01.mkv",
        ]
        sorted_paths = sort_jobs_canonically(input_paths)
        expected_paths = [
            "/data/tvshows/Anime/Season 1/Ep 01.mkv",
            "/data/tvshows/Anime/Season 1/Ep 02.mkv",
            "/data/tvshows/Anime/Season 1/Ep 10.mkv",
        ]
        self.assertEqual(sorted_paths, expected_paths)

if __name__ == "__main__":
    unittest.main()
