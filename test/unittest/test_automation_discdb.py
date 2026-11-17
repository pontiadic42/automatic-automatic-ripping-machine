"""
TheDiscDB (C24): content hash from the disc's file sizes, parsing the GraphQL answer,
title roles and episode numbers from a hit, and the confidence bonus.
"""
import hashlib
import os
import struct
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, '/opt/arm')
import arm.config.config as cfg  # noqa: E402
from arm.automation import discdb, organize, store, titles  # noqa: E402
from arm.automation.titles import Title  # noqa: E402

HASH = "0123456789ABCDEF0123456789ABCDEF"


def answer(**node):
    disc = {"contentHash": HASH, "index": 1, "name": "Disc 1", "format": "DVD", "titles": node.pop("titles", [])}
    base = {"title": "Doctor Who", "year": 2005, "type": "Series", "slug": "doctor-who-2005",
            "externalids": {"tmdb": "57243", "imdb": "tt0436992"},
            "releases": [{"title": "Series 11", "year": 2018, "slug": "s11", "discs": [disc]}]}
    base.update(node)
    return {"data": {"mediaItems": {"nodes": [base]}}}


def entry(index, duration, kind, season=None, episode=None, name="", source=""):
    return {"index": index, "duration": duration, "sourceFile": source, "size": 1,
            "item": {"title": name, "season": season, "episode": episode, "type": kind}}


class Store(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = patch.dict(cfg.arm_config, {"DBFILE": os.path.join(self.tmp.name, "arm.db"),
                                                 "DISCDB_LOOKUP": True, "MINLENGTH": 600,
                                                 "SKIP_DUPLICATE_TITLES": True, "KEEP_ALTERNATE_CUTS": True})
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()


class TestHash(Store):

    def test_hash_is_md5_of_little_endian_sizes(self):
        expected = hashlib.md5(struct.pack("<q", 12288) + struct.pack("<q", 1073709056)).hexdigest().upper()
        self.assertEqual(discdb.content_hash([12288, 1073709056]), expected)
        self.assertRegex(expected, discdb.HASH)

    def test_dvd_files_in_name_order(self):
        video_ts = os.path.join(self.tmp.name, "disc", "VIDEO_TS")
        os.makedirs(video_ts)
        for name, size in (("VTS_01_1.VOB", 30), ("VIDEO_TS.IFO", 10), ("VTS_01_0.IFO", 20)):
            with open(os.path.join(video_ts, name), "wb") as handle:
                handle.write(b"x" * size)
        files = discdb.disc_files(os.path.join(self.tmp.name, "disc"), "dvd")
        self.assertEqual(files, [("VIDEO_TS.IFO", 10), ("VTS_01_0.IFO", 20), ("VTS_01_1.VOB", 30)])
        job = SimpleNamespace(job_id=5, disctype="dvd", mountpoint=os.path.join(self.tmp.name, "disc"))
        self.assertEqual(discdb.compute(job), discdb.content_hash([10, 20, 30]))
        self.assertEqual(store.load_job(5)["discdb"]["hash"], discdb.content_hash([10, 20, 30]))

    def test_bluray_only_m2ts(self):
        stream = os.path.join(self.tmp.name, "bd", "BDMV", "STREAM")
        os.makedirs(stream)
        for name in ("00001.m2ts", "00000.m2ts", "notes.txt"):
            with open(os.path.join(stream, name), "wb") as handle:
                handle.write(b"x" * (5 if name.startswith("00000") else 7))
        self.assertEqual(discdb.disc_files(os.path.join(self.tmp.name, "bd"), "bluray"),
                         [("00000.m2ts", 5), ("00001.m2ts", 7)])

    def test_off_computes_nothing(self):
        job = SimpleNamespace(job_id=5, disctype="dvd", mountpoint=self.tmp.name)
        with patch.dict(cfg.arm_config, {"DISCDB_LOOKUP": False}):
            self.assertIsNone(discdb.compute(job))


class TestLookup(Store):

    def test_parse(self):
        hit = discdb.parse(answer(titles=[entry(0, "0:47:07", "Episode", 11, 1, "The Woman Who Fell to Earth")]), HASH)
        self.assertEqual((hit["title"], hit["type"], hit["imdb"], hit["release"]),
                         ("Doctor Who", "series", "tt0436992", "Series 11"))
        self.assertEqual(hit["titles"][0]["seconds"], 47 * 60 + 7)
        self.assertEqual((hit["titles"][0]["season"], hit["titles"][0]["episode"]), (11, 1))
        self.assertIsNone(discdb.parse(answer(), "F" * 32))

    def test_bad_hash_is_never_sent(self):
        with patch.object(discdb.requests, "post") as post:
            self.assertIsNone(discdb.query('x" } } evil'))
        post.assert_not_called()

    def test_hit_adopts_the_title_unless_set_by_hand(self):
        store.update_job(7, discdb={"hash": HASH})
        job = SimpleNamespace(job_id=7, title_manual=None, imdb_id="tt9", title="King Kong Escapes",
                              poster_url="p", video_type="movie")
        response = SimpleNamespace(status_code=200, json=lambda: answer())
        with patch.object(discdb.requests, "post", return_value=response), \
                patch("arm.automation.confidence.adopt") as adopt:
            hit = discdb.lookup(job)
        self.assertEqual(hit["imdb"], "tt0436992")
        self.assertEqual(adopt.call_args.args[1:], ("Doctor Who", "2005", "tt0436992", None, "series"))
        self.assertTrue(store.load_job(7)["discdb"]["checked"])
        job.title_manual = "Mine"
        with patch.object(discdb.requests, "post", return_value=response), \
                patch("arm.automation.confidence.adopt") as adopt:
            discdb.lookup(job)
        adopt.assert_not_called()

    def test_hit_raises_confidence_only_for_the_same_title(self):
        from arm.automation import confidence
        store.update_job(8, discdb={"hash": HASH, "hit": discdb.parse(answer(), HASH)})
        job = SimpleNamespace(job_id=8, imdb_id="tt0436992", video_type="series", label="DR_WHO_D23",
                              title="Doctor Who", hasnicetitle=True, crc_id=None)
        with patch.object(confidence, "prior_rip", return_value=None), \
                patch.object(confidence.tmdb, "runtime_minutes", return_value=None):
            result = confidence.evaluate(job, None, [])
            self.assertGreaterEqual(result["score"], confidence.DISCDB_SCORE)
            self.assertIn("TheDiscDB", result["reasons"][0])
            job.imdb_id = "tt0000001"
            self.assertLess(confidence.evaluate(job, None, [])["score"], confidence.DISCDB_SCORE)


class TestRoles(Store):

    def test_episodes_numbered_and_play_all_kept_out(self):
        store.update_job(9, discdb={"hash": HASH, "hit": discdb.parse(answer(titles=[
            entry(0, "1:16:43", "Episode", 11, 1), entry(1, "0:47:07", "Episode", 11, 2),
            entry(2, "0:46:34", "Episode", 11, 3), entry(3, "0:48:07", "Episode", 11, 4)]), HASH)})
        job = SimpleNamespace(job_id=9, imdb_id="tt0436992", video_type="series")
        disc = [Title(0, 4603), Title(1, 2827), Title(2, 2794), Title(3, 2887), Title(4, 13111)]
        decisions = titles.analyze_series(disc)
        self.assertEqual(discdb.apply(job, disc, decisions), 4)
        self.assertEqual(decisions[4]["role"], "playall")
        episodes = [d for d in decisions.values() if d["role"] == "episode"]
        numbers = discdb.episode_numbers(episodes)
        self.assertEqual(numbers, {0: (11, 1), 1: (11, 2), 2: (11, 3), 3: (11, 4)})
        with patch.object(organize, "chosen_season", return_value=None):
            found, note = organize.match_episodes(job, episodes)
        self.assertEqual(found, numbers)
        self.assertIn("TheDiscDB", note)

    def test_main_movie_from_discdb(self):
        store.update_job(10, discdb={"hash": HASH, "hit": discdb.parse(answer(
            type="Movie", externalids={"imdb": "tt1"},
            titles=[entry(1, "1:50:00", "MainMovie", source="00801.mpls"),
                    entry(2, "0:20:00", "Extra", name="Making of", source="00802.mpls")]), HASH)})
        job = SimpleNamespace(job_id=10, imdb_id="tt1", video_type="movie")
        disc = [Title(0, 120 * 60, segments=(1, 2, 3), source="00800.mpls"),
                Title(1, 110 * 60, segments=(4, 5, 6), source="00801.mpls"),
                Title(2, 20 * 60, segments=(9,), source="00802.mpls")]
        decisions, _ambiguous = titles.analyze_movie(disc, "eng", None)
        self.assertEqual(decisions[0]["role"], "main")
        discdb.apply(job, disc, decisions)
        self.assertEqual((decisions[1]["role"], decisions[0]["role"]), ("main", "cut"))
        self.assertEqual(decisions[2]["label"], "Making of")

    def test_untagged_titles_are_left_alone(self):
        untagged = entry(1, "0:44:00", "")
        untagged["item"] = None
        store.update_job(12, discdb={"hash": HASH, "hit": discdb.parse(answer(titles=[untagged]), HASH)})
        job = SimpleNamespace(job_id=12, imdb_id="tt0436992", video_type="series")
        disc = [Title(0, 44 * 60), Title(1, 44 * 60 + 30)]
        decisions = titles.analyze_series(disc)
        self.assertEqual(discdb.apply(job, disc, decisions), 0)
        self.assertEqual(decisions[1]["role"], "episode")

    def test_no_hit_changes_nothing(self):
        job = SimpleNamespace(job_id=11, imdb_id="tt1", video_type="movie")
        disc = [Title(0, 100 * 60)]
        decisions, _ = titles.analyze_movie(disc, "eng", None)
        self.assertEqual(discdb.apply(job, disc, decisions), 0)
        self.assertEqual(discdb.episode_numbers([{"tid": 0}]), {})


if __name__ == '__main__':
    unittest.main()
