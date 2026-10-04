import unittest
from unittest import mock

from acmanga.errors import SourceError
from acmanga.sources.mangakatana import MangaKatanaSource
from acmanga.sources.mangapill import MangaPillSource
from acmanga.util import relevance

MK_SEARCH = '''
<html><body>
<a href="/manga/kokou_no_hito.11542"><img alt="Kokou no Hito">Kokou no Hito</a>
<a href="/manga/kokou_no_hito.11542">duplicate</a>
<a href="/manga/other.1">Other Work</a>
</body></html>
'''
MK_BERSERK = '''
<html><head>
<meta property="og:url" content="https://mangakatana.com/manga/berserk.1087">
</head><body>
<h1>Berserk</h1>
<div>Alt name(s): Berserk Max; Berserk Prototype</div>
<div>Author(s) / Artist(s): <a href="/author/mori">Mori Kouji</a> <a href="/author/miura">Miura Kentaro</a></div>
<div>Genres: <a href="/genre/action">Action</a> <a href="/genre/seinen">Seinen</a></div>
<div>Status: Ongoing</div>
<a href="/manga/holyland.1">Holyland</a>
<a href="/manga/dorohedoro.2">Dorohedoro</a>
<a href="/manga/berserk.1087/fc">First Chapter</a>
<a href="/manga/berserk.1087/download">Read offline</a>
<a href="/manga/berserk.1087/c386">Chapter 386</a>
<a href="/manga/berserk.1087/c1">Chapter 1</a>
</body></html>
'''
MK_CHAPTERS = '''
<html><body><div class="chapters"><table><tbody>
<tr><td><a href="/manga/kokou_no_hito.11542/c170">Chapter 170</a></td></tr>
<tr><td><a href="/manga/kokou_no_hito.11542/c169.5">Chapter 169.5</a></td></tr>
<tr><td><a href="/manga/kokou_no_hito.11542/fc">First Chapter</a></td></tr>
<tr><td><a href="/manga/kokou_no_hito.11542/download">Read offline</a></td></tr>
<tr><td><a href="/manga/kokou_no_hito.11542/c1">Chapter 1</a></td></tr>
</tbody></table></div></body></html>
'''
MK_PAGES = r'''<html><body>Server 1 image<script>var thzq = ['https:\/\/img.example\/001.jpg','https:\/\/img.example\/002.webp'];</script></body></html>'''
MK_MULTI_SERVER = r'''
<html><body>Server 1 Server 2 Server 3 image
<script>var thzq = ['https:\/\/s1.example\/001.jpg','https:\/\/s1.example\/002.jpg'];</script>
<script>const altServer = ['//s2.example/001.jpg','//s2.example/002.jpg'];</script>
<script>let third = ['https://s3.example/001.webp','https://s3.example/002.webp'];</script>
</body></html>
'''

MP_SEARCH = '''
<html><body>
<a href="/manga/2426/kokou-no-hito"><img alt="Kokou no Hito">Kokou no Hito</a>
<a href="/manga/2426/kokou-no-hito">duplicate</a>
<a href="/manga/9/another-title">Another Title</a>
</body></html>
'''
MP_DETAILS = '''
<html><body><h1>Bibliomania</h1>
<div>Type Manga Status Finished Year 2016</div>
<div>Genres <a href="/genre/psychological">Psychological</a> <a href="/genre/seinen">Seinen</a></div>
<h2>Chapters</h2><a href="/chapters/8526-10001000/bibliomania-chapter-1">Bibliomania Chapter 1</a>
</body></html>
'''
MP_DETAILS_ALIAS = '''
<html><body><h1>QP</h1>
<div class="subtitle">QP: Soul of Violence</div>
<p>After a four-year absence, this deliberately long description must never become an alias for the manga.</p>
<div>Type</div><div>manga</div><div>Status finished Year 1999</div>
<div>Genres <a href="/genre/action">Action</a> <a href="/genre/drama">Drama</a></div>
<h2>Chapters</h2><a href="/chapters/6965-10001000/qp-chapter-1">QP Chapter 1</a>
</body></html>
'''

MP_CHAPTERS = '''
<html><body><h2>Chapters</h2>
<a href="/chapters/1-20001000/berserk-group-2-chapter-1">Berserk Group 2 Chapter 1</a>
<a href="/chapters/1-10001000/berserk-chapter-1">Berserk Chapter 1</a>
<a href="/chapters/1-10002000/berserk-chapter-2">Berserk Chapter 2</a>
</body></html>
'''
MP_PAGES = '''
<html><body>page Image
<picture><img class="js-page" data-src="https://cdn.example/mangap/001.jpg"></picture>
<picture><img class="js-page lazy" data-src="https://cdn.example/mangap/002.webp"></picture>
<img data-src="https://mangapill.com/i/cover.jpg">
</body></html>
'''


class MangaKatanaTests(unittest.TestCase):
    def test_search_contract_deduplicates(self):
        source = MangaKatanaSource()
        with mock.patch.object(source, "_get", return_value=MK_SEARCH) as get:
            results = source.search("Kokou no Hito")
        self.assertEqual(results[0]["id"], "kokou_no_hito.11542")
        self.assertEqual(results[0]["title"], "Kokou no Hito")
        self.assertEqual(len([r for r in results if r["id"] == "kokou_no_hito.11542"]), 1)
        self.assertNotIn("other.1", [r["id"] for r in results])
        self.assertEqual(get.call_args.kwargs["timeout"], 7)
        self.assertEqual(get.call_args.kwargs["retries"], 1)

    def test_valid_empty_search_page_returns_empty_not_parser_error(self):
        source = MangaKatanaSource()
        page = '<html><body><form><input name="search"></form><p>No results</p></body></html>'
        with mock.patch.object(source, "_get", return_value=page):
            self.assertEqual(source.search("does-not-exist"), [])

    def test_exact_direct_berserk_beats_related_links_and_resolves_author(self):
        source = MangaKatanaSource()
        with mock.patch.object(source, "_get", return_value=MK_BERSERK):
            results = source.search("Berserk")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], "berserk.1087")
        self.assertEqual(results[0]["title"], "Berserk")
        self.assertIn("Mori Kouji", results[0]["author"])
        self.assertIn("Miura Kentaro", results[0]["author"])
        self.assertNotIn("Holyland", [r["title"] for r in results])

    def test_direct_fiche_can_match_query_by_alias(self):
        source = MangaKatanaSource()
        with mock.patch.object(source, "_get", return_value=MK_BERSERK):
            results = source.search("Berserk Max")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], "berserk.1087")

    def test_title_falls_back_to_slug_instead_of_becoming_blank(self):
        source = MangaKatanaSource()
        page = '<html><body><a href="/manga/variant.55"><img alt=""></a></body></html>'
        with mock.patch.object(source, "_get", return_value=page):
            results = source.search("Variant")
        self.assertEqual(results[0]["title"], "Variant")

    def test_search_matches_himaten_without_hyphen_to_hima_ten(self):
        source = MangaKatanaSource()
        page = """<html><body><form><input name='search'></form>
        <a href='/manga/hima-ten.27370' title='HIMA-TEN!'>HIMA-TEN!</a>
        <a href='/manga/another.1' title='Another'>Another</a></body></html>"""
        with mock.patch.object(source, "_get", return_value=page):
            results = source.search("Himaten!")
        self.assertEqual([item['id'] for item in results], ['hima-ten.27370'])
        self.assertGreater(relevance(results[0]['title'], 'Himaten!'), 900)
        self.assertEqual(source.last_search_status, 'ok')

    def test_chapters_ignore_fc_and_download(self):
        source = MangaKatanaSource()
        with mock.patch.object(source, "_get", return_value=MK_CHAPTERS):
            items = source.chapters_all({"id": "kokou_no_hito.11542"})["items"]
        self.assertEqual([c["number"] for c in items], ["170", "169.5", "1"])
        raw = [(c["source_ref"] or {}).get("chapter_id") for c in items]
        self.assertNotIn("fc", raw)
        self.assertNotIn("download", raw)

    def test_same_raw_chapter_id_is_unique_between_mangas(self):
        source = MangaKatanaSource()
        self.assertNotEqual(source._stable_chapter_id("berserk.1087", "c1"),
                            source._stable_chapter_id("freesia.123", "c1"))

    def test_meta_refresh_is_followed_before_final_html_validation(self):
        source = MangaKatanaSource()
        redirect = '<meta http-equiv="refresh" content="0; url=/manga/kokou_no_hito.11542">'
        with mock.patch("acmanga.sources.mangakatana.request_text_info", side_effect=[
                (redirect, "https://mangakatana.com/page/1?search=Kokou"),
                (MK_CHAPTERS, "https://mangakatana.com/manga/kokou_no_hito.11542")]) as request:
            page = source._get("/page/1", {"search": "Kokou no Hito"}, retries=0)
        self.assertEqual(page, MK_CHAPTERS)
        self.assertEqual(request.call_count, 2)
        self.assertIn("/manga/kokou_no_hito.11542", request.call_args_list[1].args[0])

    def test_multi_server_lists_are_discovered(self):
        source = MangaKatanaSource()
        chapter = {"id": "legacy-c1", "title_id": "berserk.1087",
                   "source_ref": {"manga_id": "berserk.1087", "chapter_id": "c1"}}
        with mock.patch.object(source, "_get", return_value=MK_MULTI_SERVER):
            sets = source._page_url_sets(chapter)
        self.assertEqual(len(sets), 3)
        self.assertEqual(sets[0][0], "https://s1.example/001.jpg")
        self.assertEqual(sets[1][0], "https://s2.example/001.jpg")

    def test_prepare_pages_falls_back_to_second_server(self):
        source = MangaKatanaSource()
        chapter = {"id": "x", "title_id": "berserk.1087",
                   "source_ref": {"manga_id": "berserk.1087", "chapter_id": "c1"}}
        with mock.patch.object(source, "_page_url_sets", return_value=[["https://s1/a", "https://s1/b"], ["https://s2/a", "https://s2/b"]]), \
             mock.patch("acmanga.sources.mangakatana.download_image_set", side_effect=[SourceError("s1 down"), ["ok1", "ok2"]]) as download:
            pages = source.prepare_pages(chapter, "/tmp/chapter")
        self.assertEqual(pages, ["ok1", "ok2"])
        self.assertEqual(download.call_count, 2)
        self.assertIn("/manga/berserk.1087/c1", download.call_args.kwargs["referer"] if "referer" in download.call_args.kwargs else download.call_args.args[3])

    def test_missing_page_list_is_explicit_parser_error(self):
        source = MangaKatanaSource()
        chapter = {"title_id": "x", "source_ref": {"manga_id": "x", "chapter_id": "c1"}}
        with mock.patch.object(source, "_get", return_value="<html><body>Server image</body></html>"):
            with self.assertRaises(SourceError) as ctx:
                source._page_urls(chapter)
        self.assertEqual(ctx.exception.kind, "parser")

    def test_direct_real_shape_without_og_or_canonical_uses_profile_actions(self):
        source = MangaKatanaSource()
        page = """<html><body><h1>Variante</h1>
        <div>Alt name(s): Variante - Requiem for the World</div>
        <div>Author(s) / Artist(s): <a href='/author/sugimoto'>Sugimoto Iqura</a></div>
        <div>Status: Completed</div>
        <a href='/manga/variante.11343/fc'>First Chapter</a>
        <a href='/manga/variante.11343/download'>Read offline</a>
        <a href='/manga/shiki.1'>Shiki</a></body></html>"""
        with mock.patch.object(source, "_get", return_value=(page, "https://mangakatana.com/page/1?search=Variante")):
            results = source.search("Variante")
        self.assertEqual([(r['id'], r['title']) for r in results], [('variante.11343', 'Variante')])
        self.assertIn('Sugimoto Iqura', results[0]['author'])

    def test_http_final_url_identifies_direct_berserk_even_without_metadata(self):
        source = MangaKatanaSource()
        page = """<html><body><h1>Berserk</h1><div>Author(s) / Artist(s): Miura Kentaro</div>
        <div>Status: Ongoing</div><a href='/manga/holyland.1'>Holyland</a></body></html>"""
        with mock.patch.object(source, "_get", return_value=(page, 'https://mangakatana.com/manga/berserk.1087')):
            results = source.search('Berserk')
        self.assertEqual(results[0]['id'], 'berserk.1087')
        self.assertEqual(results[0]['title'], 'Berserk')

    def test_vagabond_chapters_fall_back_to_reader_select_options(self):
        source = MangaKatanaSource()
        fiche = """<html><body><h1>Vagabond</h1><div>Author(s) / Artist(s): Inoue Takehiko</div>
        <a href='/manga/vagabond.3120/fc'>First Chapter</a></body></html>"""
        reader = """<html><body>Server 1<select>
        <option value='/manga/vagabond.3120/c327'>Chapter 327</option>
        <option value='https://mangakatana.com/manga/vagabond.3120/c326'>Chapter 326</option>
        <option value='/manga/vagabond.3120/c1'>Chapter 1: Shinmen Takezo</option>
        </select></body></html>"""
        def fake_get(path, *args, **kwargs):
            if path.endswith('/fc'):
                return (reader, 'https://mangakatana.com/manga/vagabond.3120/c1') if kwargs.get('with_url') else reader
            return fiche
        with mock.patch.object(source, '_get', side_effect=fake_get):
            chapters = source.chapters_all({'id': 'vagabond.3120'}, force=True)['items']
        self.assertEqual([c['number'] for c in chapters], ['327', '326', '1'])
        self.assertTrue(all((c.get('source_ref') or {}).get('chapter_id', '').startswith('c') for c in chapters))



class MangaPillTests(unittest.TestCase):
    def test_search_contract_and_dedup(self):
        source = MangaPillSource()
        with mock.patch.object(source, "_get", return_value=MP_SEARCH) as get:
            results = source.search("Kokou no Hito")
        self.assertEqual(results[0]["id"], "2426/kokou-no-hito")
        self.assertEqual(results[0]["title"], "Kokou no Hito")
        self.assertEqual(len([r for r in results if r["id"].startswith("2426/")]), 1)
        self.assertNotIn("9/another-title", [r["id"] for r in results])
        self.assertEqual(get.call_args.kwargs["timeout"], 7)

    def test_search_prefers_visible_exact_title_over_decorated_title_attribute(self):
        source = MangaPillSource()
        page = """<html><body><a href='/manga/2426/kokou-no-hito' title='Kokou no Hito Manga - MangaPill'>Kokou no Hito</a></body></html>"""
        with mock.patch.object(source, '_get', return_value=(page, 'https://mangapill.com/search?q=Kokou')):
            results = source.search('Kokou no Hito')
        self.assertEqual(results[0]['title'], 'Kokou no Hito')
        self.assertEqual(results[0]['id'], '2426/kokou-no-hito')

    def test_decorated_title_only_is_cleaned_to_exact_name(self):
        source = MangaPillSource()
        page = """<html><body><a href='/manga/2426/kokou-no-hito' title='Kokou no Hito Manga - MangaPill'></a></body></html>"""
        with mock.patch.object(source, '_get', return_value=(page, 'https://mangapill.com/search?q=Kokou')):
            results = source.search('Kokou no Hito')
        self.assertEqual(results[0]['title'], 'Kokou no Hito')

    def test_valid_empty_search_page_returns_empty_not_parser_error(self):
        source = MangaPillSource()
        page = '<html><body><h1>Search Manga</h1><form><input name="q"></form><p>No results</p></body></html>'
        with mock.patch.object(source, "_get", return_value=page):
            self.assertEqual(source.search("does-not-exist"), [])

    def test_blank_anchor_uses_slug_title(self):
        source = MangaPillSource()
        page = '<html><body><a href="/manga/9/variant"></a></body></html>'
        with mock.patch.object(source, "_get", return_value=page):
            results = source.search("Variant")
        self.assertEqual(results[0]["title"], "Variant")

    def test_details_extract_year_status_type_and_genres_without_fake_author(self):
        source = MangaPillSource()
        with mock.patch.object(source, "_get", return_value=MP_DETAILS):
            item = source.details({"id": "8526/bibliomania"}, force=True)
        self.assertEqual(item["title"], "Bibliomania")
        self.assertEqual(item["year"], "2016")
        self.assertEqual(item["status"].casefold(), "finished")
        self.assertEqual(item["author"], "")
        self.assertIn("Seinen", item["genres"])

    def test_details_extract_short_alternate_title_without_eating_description(self):
        source = MangaPillSource()
        with mock.patch.object(source, "_get", return_value=MP_DETAILS_ALIAS):
            item = source.details({"id": "6965/qp"}, force=True)
        self.assertEqual(item["aliases"], ["QP: Soul of Violence"])
        self.assertNotIn("four-year absence", " ".join(item["aliases"]))

    def test_description_only_is_not_mistaken_for_alias(self):
        source = MangaPillSource()
        page = '''<html><body><h1>Berserk</h1><p>Guts is a former mercenary whose extremely long story description should never be treated as an alternate title in metadata.</p><div>Type</div><div>manga</div><div>Status publishing Year 1989</div><h2>Chapters</h2></body></html>'''
        with mock.patch.object(source, "_get", return_value=page):
            item = source.details({"id": "1/berserk"}, force=True)
        self.assertEqual(item["aliases"], [])

    def test_details_without_real_h1_is_rejected_instead_of_fabricated_from_slug(self):
        source = MangaPillSource()
        page = '<html><body>Type Manga Status Finished Year 2016 Chapters <a href="/chapters/1/a">Chapter 1</a></body></html>'
        with mock.patch.object(source, "_get", return_value=page):
            with self.assertRaises(SourceError) as ctx:
                source.details({"id": "999/fake-title"}, force=True)
        self.assertEqual(ctx.exception.kind, "parser")

    def test_groups_are_preserved_and_normal_chapter_has_no_group(self):
        source = MangaPillSource()
        with mock.patch.object(source, "_get", return_value=MP_CHAPTERS):
            items = source.chapters_all({"id": "1/berserk"})["items"]
        self.assertEqual(items[0]["groups"], ["Group 2"])
        self.assertEqual(items[1]["groups"], [])
        self.assertEqual(items[0]["number"], "1")
        self.assertEqual(items[1]["number"], "1")
        self.assertNotEqual(items[0]["id"], items[1]["id"])

    def test_page_list_uses_js_page_and_ignores_cover(self):
        source = MangaPillSource()
        chapter = {"source_ref": {"chapter_ref": "1-10001000/berserk-chapter-1"}}
        with mock.patch.object(source, "_get", return_value=MP_PAGES):
            pages = source._page_urls(chapter)
        self.assertEqual(pages, ["https://cdn.example/mangap/001.jpg", "https://cdn.example/mangap/002.webp"])

    def test_fallback_accepts_numbered_page_on_known_reader_host(self):
        source = MangaPillSource()
        page = '<html><body>page Image<img alt="Page 1" src="https://cdn.readdetectiveconan.com/file/1.jpg"></body></html>'
        chapter = {"source_ref": {"chapter_ref": "1-1/berserk-chapter-1"}}
        with mock.patch.object(source, "_get", return_value=page):
            self.assertEqual(source._page_urls(chapter), ["https://cdn.readdetectiveconan.com/file/1.jpg"])

    def test_generic_cdn_image_is_not_accepted_as_page(self):
        source = MangaPillSource()
        page = '<html><body>page Image<img src="https://cdn.example/ad.jpg"><img src="https://cdn.example/cover.jpg"></body></html>'
        chapter = {"source_ref": {"chapter_ref": "1-1/berserk-chapter-1"}}
        with mock.patch.object(source, "_get", return_value=page):
            with self.assertRaises(SourceError) as ctx:
                source._page_urls(chapter)
        self.assertEqual(ctx.exception.kind, "parser")


if __name__ == "__main__":
    unittest.main()
