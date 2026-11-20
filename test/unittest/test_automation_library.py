"""
Library manager (C35): scanning movies/tv/unidentified (files A2RM didn't make, ARM copy folders,
loose files, MakeMKV names, same titles), rename/move plans, merging without overwriting,
TV episode numbering, apply/undo, trash, and the pages.
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, '/opt/arm')
import arm.config.config as cfg  # noqa: E402
from arm.automation import library, store  # noqa: E402
from arm.ui import app  # noqa: E402


class LibraryCase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.lib = os.path.join(self.tmp.name, "completed")
        for root in library.ROOTS:
            os.makedirs(os.path.join(self.lib, root))
        self.patch = patch.dict(cfg.arm_config, {"DBFILE": os.path.join(self.tmp.name, "arm.db"),
                                                 "COMPLETED_PATH": self.lib, "EXTRAS_SUB": "extras",
                                                 "WRITE_NFO": False, "JELLYFIN_API_KEY": ""})
        self.patch.start()
        self.owners = patch.object(library, "tracked_jobs", return_value={})
        self.owners.start()
        self.running = patch.object(library, "running_jobs", return_value=[])
        self.running.start()

    def tearDown(self):
        self.running.stop()
        self.owners.stop()
        self.patch.stop()
        self.tmp.cleanup()

    def write(self, rel, size=10):
        path = os.path.join(self.lib, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(b"x" * size)
        return path

    def exists(self, rel):
        return os.path.exists(os.path.join(self.lib, rel))


class TestNames(LibraryCase):

    def test_parse_name(self):
        cases = {
            "Doctor Who Season 8 Ep. 5-9 (2005–)_179152795038": ("Doctor Who", "2005", 8, 5, True),
            "Psych (2006–2014)": ("Psych", "2006", None, None, False),
            "When Marnie was There (None)": ("When Marnie was There", "", None, None, False),
            "Doctor Who Season 10 Ep. 11-12 (2005–)": ("Doctor Who", "2005", 10, 11, False),
            "The Big Green (1995).mkv": ("The Big Green", "1995", None, None, False),
            "Annabelle- Creation (2017) - Shorter cut (-11 min).mkv":
                ("Annabelle- Creation", "2017", None, None, False),
            "7000085651_1": ("7000085651 1", "", None, None, False),
        }
        for name, (title, year, season, first, copy) in cases.items():
            parsed = library.parse_name(name)
            self.assertEqual((parsed["title"], parsed["year"], parsed["season"], parsed["first_episode"],
                              parsed["copy"]), (title, year, season, first, copy), name)

    def test_title_key_ignores_spelling(self):
        self.assertEqual(library.title_key("Annabelle- Creation"), library.title_key("Annabelle-Creation"))
        self.assertEqual(library.title_key("Fly Wheel"), library.title_key("Flywheel"))
        self.assertNotEqual(library.title_key("A", "1984"), library.title_key("A", "2010"))

    def test_paths_outside_the_library_are_refused(self):
        for bad in ("../etc", "movies/../../x", "", "/", "movies//x"):
            with self.assertRaises(library.LibraryError, msg=bad):
                library.absolute(bad)


class TestScan(LibraryCase):

    def test_finds_what_wasnt_done(self):
        self.write("movies/Ant-Man (2015)/Ant-Man (2015).mkv")
        self.write("movies/Annabelle-Creation (2017)/Annabelle-Creation (2017).mkv")
        self.write("movies/The Big Green (1995).mkv")
        self.write("movies/notes.txt")
        self.write("movies/.a2rm-trash/x/movies/Old (1990)/Old (1990).mkv")
        self.write("tv/Doctor Who (2005–)_179152795038/C1_t00.mkv")
        self.write("tv/Doctor Who (2005–)_179152795038/extras/B1_t04.mkv")
        self.write("tv/Doctor Who Season 8 Ep. 1-4 (2005–)/a.mkv")
        os.makedirs(os.path.join(self.lib, "tv", "Empty Show (2001)"))
        self.write("unidentified/Annabelle- Creation (2017)/Annabelle- Creation (2017).mkv")
        with patch.object(library, "tracked_jobs", return_value={"movies/Ant-Man (2015)": 12}):
            result = library.scan()
        items = {item["id"]: {issue["code"] for issue in item["issues"]} for item in result["items"]}
        self.assertEqual(set(items), {"movies/Ant-Man (2015)", "movies/Annabelle-Creation (2017)",
                                      "movies/The Big Green (1995).mkv", "tv/Doctor Who (2005–)_179152795038",
                                      "tv/Doctor Who Season 8 Ep. 1-4 (2005–)", "tv/Empty Show (2001)",
                                      "unidentified/Annabelle- Creation (2017)"})
        self.assertEqual(items["movies/Ant-Man (2015)"], set())  # tracked, named right: hyphen kept
        self.assertIn("untracked", items["movies/Annabelle-Creation (2017)"])
        self.assertIn("loose", items["movies/The Big Green (1995).mkv"])
        dw = items["tv/Doctor Who (2005–)_179152795038"]
        self.assertTrue({"copy", "raw_names", "duplicate"} <= dw, dw)
        self.assertIn("season_folder", items["tv/Doctor Who Season 8 Ep. 1-4 (2005–)"])
        self.assertIn("empty", items["tv/Empty Show (2001)"])
        self.assertIn("unidentified", items["unidentified/Annabelle- Creation (2017)"])
        raw = next(i for i in result["items"] if i["id"] == "tv/Doctor Who (2005–)_179152795038")
        self.assertIn("1 file still named", next(x["text"] for x in raw["issues"] if x["code"] == "raw_names"))
        self.assertEqual(store.load_state()["library"]["problems"], result["problems"])
        unidentified = next(i for i in result["items"] if i["root"] == "unidentified")
        self.assertEqual([o["id"] for o in library.same_title(unidentified, result["items"])],
                         ["movies/Annabelle-Creation (2017)"])
        attention = {item["id"] for item in result["items"] if item["attention"]}
        self.assertNotIn("movies/Ant-Man (2015)", attention)
        self.assertNotIn("movies/Annabelle-Creation (2017)", attention)  # only untracked, named right
        self.assertEqual(len(attention), 5)

    def test_remakes_arent_duplicates_but_show_folders_are(self):
        self.write("movies/A Nightmare on Elm Street (1984)/a.mkv")
        self.write("movies/A Nightmare on Elm Street (2010)/a.mkv")
        self.write("movies/Fly Wheel (2003)/a.mkv")
        self.write("movies/Flywheel (2003)/a.mkv")
        self.write("tv/Psych (2006–2014)/a.mkv")
        self.write("tv/Psych/a.mkv")
        result = library.scan()
        dup = {i["id"] for i in result["items"] if any(x["code"] == "duplicate" for x in i["issues"])}
        self.assertEqual(dup, {"movies/Fly Wheel (2003)", "movies/Flywheel (2003)", "tv/Psych (2006–2014)", "tv/Psych"})


class TestPlans(LibraryCase):

    def test_unidentified_to_movies_with_versions_and_side_files(self):
        base = "unidentified/Annabelle- Creation (2017)"
        self.write(f"{base}/Annabelle- Creation (2017).mkv", 100)
        self.write(f"{base}/Annabelle- Creation (2017).en.srt")
        self.write(f"{base}/Annabelle- Creation (2017) - Shorter cut (-11 min).mkv", 90)
        self.write(f"{base}/extras/A2_t01.mkv")
        plan = library.plan(base, {"kind": "movie", "title": "Annabelle: Creation", "year": "2017"})
        moves = {m["from"]: m["to"] for m in plan["moves"]}
        self.assertEqual(plan["target"], "movies/Annabelle Creation (2017)")
        self.assertEqual(moves, {
            f"{base}/Annabelle- Creation (2017).mkv": "movies/Annabelle Creation (2017)/Annabelle Creation (2017).mkv",
            f"{base}/Annabelle- Creation (2017).en.srt":
                "movies/Annabelle Creation (2017)/Annabelle Creation (2017).en.srt",
            f"{base}/Annabelle- Creation (2017) - Shorter cut (-11 min).mkv":
                "movies/Annabelle Creation (2017)/Annabelle Creation (2017) - Shorter cut (-11 min).mkv",
            f"{base}/extras/A2_t01.mkv": "movies/Annabelle Creation (2017)/extras/A2_t01.mkv"})
        self.assertFalse(self.exists("movies/Annabelle Creation (2017)"))  # only a plan

    def test_loose_file_gets_a_folder(self):
        self.write("movies/The Big Green (1995).mkv")
        plan = library.plan("movies/The Big Green (1995).mkv", {"kind": "movie", "title": "The Big Green",
                                                                "year": "1995"})
        self.assertEqual([(m["from"], m["to"]) for m in plan["moves"]],
                         [("movies/The Big Green (1995).mkv", "movies/The Big Green (1995)/The Big Green (1995).mkv")])

    def test_merge_never_overwrites(self):
        self.write("movies/Flywheel (2003)/Flywheel (2003).mkv", 50)
        self.write("movies/Fly Wheel (2003)/Fly Wheel (2003).mkv", 50)       # same size: left alone
        self.write("movies/Fly Wheel (2003)/extras/Making of.mkv", 7)
        self.write("movies/Flywheel (2003)/extras/Making of.mkv", 8)       # different: gets " (2)"
        self.write("movies/Flywheel (2003)/movie.nfo", 3)
        self.write("movies/Fly Wheel (2003)/movie.nfo", 4)                 # not ours, and one is there
        plan = library.plan("movies/Fly Wheel (2003)", {"kind": "movie", "title": "Flywheel", "year": "2003"})
        self.assertEqual([s["from"] for s in plan["same"]], ["movies/Fly Wheel (2003)/Fly Wheel (2003).mkv"])
        self.assertEqual([m["to"] for m in plan["moves"]], ["movies/Flywheel (2003)/extras/Making of (2).mkv"])
        self.assertTrue(any("already there" in note for note in plan["notes"]))
        self.assertTrue(any("movie.nfo stays where it is" in note for note in plan["notes"]))

    def test_tv_disc_numbered_from_the_season(self):
        base = "tv/Doctor Who (2005–)_179152795038"
        for name, size in (("C1_t00.mkv", 40), ("C2_t01.mkv", 41), ("C3_t02.mkv", 42), ("B1_t04.mkv", 170)):
            self.write(f"{base}/{name}", size)
        roles = library.default_roles(base, "series")
        self.assertEqual(roles["B1_t04.mkv"], "extra")  # the play-all
        plan = library.plan(base, {"kind": "series", "title": "Doctor Who", "year": "2005", "season": "8",
                                   "first_episode": "5"})
        moves = {m["from"].split("/")[-1]: m["to"] for m in plan["moves"]}
        self.assertEqual(moves["C1_t00.mkv"], "tv/Doctor Who (2005)/Season 08/Doctor Who S08E05.mkv")
        self.assertEqual(moves["C3_t02.mkv"], "tv/Doctor Who (2005)/Season 08/Doctor Who S08E07.mkv")
        self.assertEqual(moves["B1_t04.mkv"], "tv/Doctor Who (2005)/extras/B1_t04.mkv")

    def test_tv_without_season_keeps_names(self):
        self.write("tv/Psych (2006–2014)_179123969604/B1_t00.mkv")
        plan = library.plan("tv/Psych (2006–2014)_179123969604", {"kind": "series", "title": "Psych", "year": "2006"})
        self.assertEqual([m["to"] for m in plan["moves"]], ["tv/Psych (2006)/B1_t00.mkv"])
        self.assertTrue(any("No season" in note for note in plan["notes"]))

    def test_bad_values(self):
        self.write("movies/X (2001)/X (2001).mkv")
        for values in ({"kind": "music", "title": "X"}, {"kind": "movie", "title": " "},
                       {"kind": "movie", "title": "X", "year": "01"}, {"kind": "movie", "title": "X", "imdb_id": "123"},
                       {"kind": "series", "title": "X", "season": "one"}):
            with self.assertRaises(library.LibraryError, msg=values):
                library.plan("movies/X (2001)", values)


class TestApply(LibraryCase):

    def test_apply_then_undo(self):
        base = "tv/Doctor Who (2005–)_179152795038"
        self.write(f"{base}/C1_t00.mkv", 40)
        self.write(f"{base}/C2_t01.mkv", 41)
        self.write("tv/Doctor Who (2005)/Season 08/Doctor Who S08E01.mkv", 39)
        entry = library.apply(base, {"kind": "series", "title": "Doctor Who", "year": "2005", "season": "8",
                                     "first_episode": "5"})
        self.assertTrue(self.exists("tv/Doctor Who (2005)/Season 08/Doctor Who S08E06.mkv"))
        self.assertFalse(self.exists(base))  # emptied folder removed
        self.assertEqual(library.history()[0]["id"], entry["id"])
        library.undo(entry["id"])
        self.assertTrue(self.exists(f"{base}/C1_t00.mkv") and self.exists(f"{base}/C2_t01.mkv"))
        self.assertFalse(self.exists("tv/Doctor Who (2005)/Season 08/Doctor Who S08E05.mkv"))
        self.assertTrue(self.exists("tv/Doctor Who (2005)/Season 08/Doctor Who S08E01.mkv"))
        with self.assertRaises(library.LibraryError):
            library.undo(entry["id"])

    def test_nothing_to_do(self):
        self.write("movies/X (2001)/X (2001).mkv")
        with self.assertRaises(library.LibraryError):
            library.apply("movies/X (2001)", {"kind": "movie", "title": "X", "year": "2001"})

    def test_failed_move_is_rolled_back(self):
        base = "movies/A (2001)"
        self.write(f"{base}/A (2001).mkv", 20)
        self.write(f"{base}/A (2001) - Extended.mkv", 10)
        real_move = library.shutil.move
        calls = []

        def flaky(source, target):
            calls.append(source)
            if len(calls) == 2:
                raise OSError("disk full")
            return real_move(source, target)
        with patch.object(library.shutil, "move", side_effect=flaky):
            with self.assertRaises(OSError):
                library.apply(base, {"kind": "movie", "title": "B", "year": "2001"})
        self.assertTrue(self.exists(f"{base}/A (2001).mkv") and self.exists(f"{base}/A (2001) - Extended.mkv"))
        self.assertFalse(self.exists("movies/B (2001)"))

    def test_trash_and_restore(self):
        self.write("movies/Don't Breathe (2016)_1791/x.mkv", 5)
        entry = library.trash("movies/Don't Breathe (2016)_1791")
        self.assertFalse(self.exists("movies/Don't Breathe (2016)_1791"))
        self.assertEqual(library.trash_size(), (1, 5))
        self.assertNotIn("movies/.a2rm-trash", [i["id"] for i in library.scan()["items"]])
        library.undo(entry["id"])
        self.assertTrue(self.exists("movies/Don't Breathe (2016)_1791/x.mkv"))
        library.trash("movies/Don't Breathe (2016)_1791")
        self.assertEqual(library.empty_trash(), 1)
        self.assertEqual(library.trash_size(), (0, 0))
        self.assertTrue(library.history()[0]["undone"])


class TestPages(LibraryCase):

    def setUp(self):
        super().setUp()
        self.config = patch.dict(app.config, {"WTF_CSRF_ENABLED": False, "LOGIN_DISABLED": True})
        self.config.start()
        self.client = app.test_client()
        self.busy = patch("arm.automation.health.busy", return_value=False)
        self.busy.start()

    def tearDown(self):
        self.busy.stop()
        self.config.stop()
        super().tearDown()

    def test_list_preview_apply(self):
        self.write("unidentified/7000085651_1/7000085651_1.mkv", 30)
        self.write("unidentified/7000085651_1/7000085651_1 - Shorter cut (-22 min).mkv", 20)
        page = self.client.get("/library/files")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"7000085651_1", page.data)
        item = "unidentified/7000085651_1"
        self.assertEqual(self.client.get("/library/files/item", query_string={"id": item}).status_code, 200)
        form = {"id": item, "kind": "movie", "title": "When Marnie Was There", "year": "2014",
                "file_0": "7000085651_1 - Shorter cut (-22 min).mkv", "role_0": "version",
                "file_1": "7000085651_1.mkv", "role_1": "main", "action": "preview"}
        preview = self.client.post("/library/files/item", data=form)
        self.assertIn("When Marnie Was There (2014) - Shorter cut (-22 min).mkv".encode(), preview.data)
        self.assertFalse(self.exists("movies/When Marnie Was There (2014)"))
        done = self.client.post("/library/files/item", data=dict(form, action="apply"))
        self.assertEqual(done.status_code, 302)
        self.assertTrue(self.exists("movies/When Marnie Was There (2014)/When Marnie Was There (2014).mkv"))
        self.assertFalse(self.exists(item))
        undo = self.client.post("/library/files", data={"action": "undo", "entry": library.history()[0]["id"]})
        self.assertEqual(undo.status_code, 302)
        self.assertTrue(self.exists(f"{item}/7000085651_1.mkv"))

    def test_apply_waits_for_a_rip_of_the_same_title(self):
        self.write("movies/X/X.mkv")
        job = {"job_id": 47, "name": "X (2001)", "key": library.title_key("X", "2001"), "item": None}
        with patch.object(library, "running_jobs", return_value=[job]):
            page = self.client.post("/library/files/item", data={"id": "movies/X", "kind": "movie", "title": "X",
                                                                 "year": "2001", "action": "apply"})
        self.assertTrue(self.exists("movies/X/X.mkv"))
        self.assertIn(b"Job 47 (X (2001)) is still running", page.data)

    def test_unknown_item_redirects(self):
        self.assertEqual(self.client.get("/library/files/item", query_string={"id": "movies/nope"}).status_code, 302)
        self.assertEqual(self.client.get("/library/files/item", query_string={"id": "../x"}).status_code, 302)


if __name__ == "__main__":
    unittest.main()
