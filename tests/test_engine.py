import time
import unittest

from acmanga.engine import MultiSourceEngine
from acmanga.errors import SourceError


class FakeSource:
    def __init__(self, key, results=None, delay=0.0, search_error=None, chapters=None,
                 details_map=None, details_error=None, search_status=None):
        self.key = key
        self.name = key
        self.results = [dict(x) for x in (results or [])]
        self.delay = delay
        self.search_error = search_error
        self._chapters = [dict(x) for x in (chapters or [])]
        self.details_map = details_map or {}
        self.details_error = details_error
        self.calls = 0
        self.chapter_calls = []
        self.detail_calls = []
        self.invalidated = []
        self.search_status = search_status
        self.last_search_status = "unknown"

    def search(self, query):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.search_error:
            raise self.search_error
        self.last_search_status = self.search_status or ("ok" if self.results else "not_found")
        return [dict(x) for x in self.results]

    def details(self, ref, force=False):
        ident = str((ref or {}).get('id') if isinstance(ref, dict) else ref)
        self.detail_calls.append((ident, bool(force)))
        if self.details_error:
            raise self.details_error
        value = self.details_map.get(ident)
        if value is not None:
            return dict(value)
        for item in self.results:
            if str(item.get('id')) == ident:
                out = dict(item)
                out.setdefault('aliases', [])
                out.setdefault('year', '')
                out.setdefault('status', '')
                out.setdefault('type', 'manga')
                out.setdefault('genres', [])
                out.setdefault('ref', {'id': ident})
                return out
        return {'source': self.key, 'id': ident, 'title': ident, 'author': '', 'aliases': [],
                'year': '', 'status': '', 'type': 'manga', 'genres': [], 'language': 'en', 'ref': {'id': ident}}

    def chapters_all(self, ref, force=False):
        self.chapter_calls.append(bool(force))
        if self.details_error and getattr(self.details_error, 'kind', '') == 'chapter':
            raise self.details_error
        return {'items': [dict(x) for x in self._chapters], 'total': len(self._chapters)}

    def invalidate(self, ref=None):
        self.invalidated.append(ref)

    def diagnostic(self):
        return {'source': self.key, 'ok': True, 'detail': 'ok'}


def item(source, ident, title='Kokou no Hito', author='', **extra):
    out = {'source': source, 'id': ident, 'title': title, 'author': author, 'aliases': [], 'year': '',
           'status': '', 'type': 'manga', 'genres': [], 'language': 'en', 'ref': {'id': ident}}
    out.update(extra)
    return out


class EngineTests(unittest.TestCase):
    def make(self, a=None, b=None, prefs=None):
        a = a or FakeSource('mangakatana', [item('mangakatana', 'mk1')])
        b = b or FakeSource('mangapill', [item('mangapill', 'mp1')])
        return MultiSourceEngine([a, b], preferences=prefs or {})

    def test_parallel_search_and_merge(self):
        a = FakeSource('mangakatana', [item('mangakatana', 'mk1')], delay=0.12)
        b = FakeSource('mangapill', [item('mangapill', 'mp1')], delay=0.12)
        engine = self.make(a, b)
        started = time.monotonic()
        results, errors = engine.search('Kokou no Hito')
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 0.22)
        self.assertFalse(errors)
        self.assertEqual(len(results), 1)
        self.assertEqual([v['source'] for v in results[0]['variants']], ['mangakatana', 'mangapill'])

    def test_search_cache_avoids_second_network_call(self):
        a = FakeSource('mangakatana', [item('mangakatana', 'mk1')])
        b = FakeSource('mangapill', [item('mangapill', 'mp1')])
        engine = self.make(a, b)
        engine.search('Kokou no Hito')
        engine.search('Kokou no Hito')
        self.assertEqual((a.calls, b.calls), (1, 1))
        engine.search('Kokou no Hito', force=True)
        self.assertEqual((a.calls, b.calls), (2, 2))

    def test_source_failure_is_classified_and_other_source_survives(self):
        a = FakeSource('mangakatana', search_error=SourceError('offline', source='mangakatana', kind='network', transient=True))
        b = FakeSource('mangapill', [item('mangapill', 'mp1')])
        engine = self.make(a, b)
        results, errors = engine.search('Kokou no Hito')
        self.assertEqual(len(results), 1)
        self.assertIn('mangakatana', errors)
        self.assertEqual(engine.last_error_details['mangakatana']['kind'], 'network')
        self.assertTrue(engine.last_error_details['mangakatana']['transient'])

    def test_cross_source_separator_variant_is_fused_without_changing_ids(self):
        mk = FakeSource('mangakatana', [item('mangakatana', 'hima-ten.27370', 'HIMA-TEN!')])
        mp = FakeSource('mangapill', [item('mangapill', '8183/himaten', 'Himaten!')])
        engine = self.make(mk, mp)
        results, errors = engine.search('Himaten!')
        self.assertFalse(errors)
        self.assertEqual(len(results), 1)
        self.assertEqual({(v['source'], v['id']) for v in results[0]['variants']}, {
            ('mangakatana', 'hima-ten.27370'), ('mangapill', '8183/himaten'),
        })
        self.assertIn('HIMA-TEN!', [results[0]['title']] + results[0]['aliases'])
        self.assertIn('Himaten!', [results[0]['title']] + results[0]['aliases'])

    def test_alias_can_bridge_different_canonical_titles_across_sources(self):
        mk_item = item('mangakatana', 'kokou.1', 'Kokou no Hito', aliases=['The Climber'])
        mp_item = item('mangapill', '2426/kokou-no-hito', 'The Climber')
        engine = self.make(FakeSource('mangakatana', [mk_item]), FakeSource('mangapill', [mp_item]))
        results, _ = engine.search('The Climber')
        self.assertEqual(len(results), 1)
        self.assertEqual(len(results[0]['variants']), 2)
        self.assertIn('Kokou no Hito', [results[0]['title']] + results[0]['aliases'])
        self.assertIn('The Climber', [results[0]['title']] + results[0]['aliases'])

    def test_same_title_duplicate_in_one_source_is_not_fused(self):
        mp = FakeSource('mangapill', [
            item('mangapill', '6082/bibliomania', 'Bibliomania', year='2000'),
            item('mangapill', '8526/bibliomania', 'Bibliomania', year='2016'),
        ])
        mk = FakeSource('mangakatana', [])
        engine = self.make(mk, mp)
        results, _ = engine.search('Bibliomania')
        self.assertEqual(len(results), 2)
        self.assertEqual({r['variants'][0]['id'] for r in results}, {'6082/bibliomania', '8526/bibliomania'})

    def test_cross_source_same_title_with_conflicting_year_is_not_fused(self):
        mk = FakeSource('mangakatana', [item('mangakatana', 'mk', 'Same', year='2000')])
        mp = FakeSource('mangapill', [item('mangapill', 'mp', 'Same', year='2016')])
        engine = self.make(mk, mp)
        results, _ = engine.search('Same')
        self.assertEqual(len(results), 2)

    def test_resolve_drops_secondary_variant_on_explicit_author_conflict(self):
        mk_search = item('mangakatana', 'mk', 'Same')
        mp_search = item('mangapill', 'mp', 'Same')
        mk = FakeSource('mangakatana', [mk_search], details_map={'mk': item('mangakatana', 'mk', 'Same', author='Alice Example')})
        mp = FakeSource('mangapill', [mp_search], details_map={'mp': item('mangapill', 'mp', 'Same', author='Bob Other')})
        engine = self.make(mk, mp)
        manga = {'key': 'mangakatana:mk', 'identity': 'mangakatana:mk', 'title': 'Same', 'variants': [mk_search, mp_search]}
        resolved, errors = engine.resolve_manga(manga, force=True)
        self.assertEqual([(v['source'], v['id']) for v in resolved['variants']], [('mangakatana', 'mk')])
        self.assertIn('mangapill', errors)

    def test_manual_source_mode(self):
        engine = self.make(prefs={'source_mode': 'mangapill', 'source_priority': ['mangakatana', 'mangapill'], 'saved_sort': 'activity'})
        self.assertEqual(engine.active_source_keys(), ['mangapill'])

    def test_resolve_manga_enriches_metadata_without_changing_identity(self):
        mk_item = item('mangakatana', 'berserk.1087', 'Berserk')
        details = item('mangakatana', 'berserk.1087', 'Berserk', author='Mori Kouji, Miura Kentaro',
                       aliases=['Berserk Max'], status='Ongoing')
        mk = FakeSource('mangakatana', [mk_item], details_map={'berserk.1087': details})
        mp = FakeSource('mangapill', [])
        engine = self.make(mk, mp)
        manga = {'key': 'mangakatana:berserk.1087', 'identity': 'mangakatana:berserk.1087', 'title': 'Berserk',
                 'variants': [mk_item]}
        resolved, errors = engine.resolve_manga(manga)
        self.assertFalse(errors)
        self.assertIn('Miura Kentaro', resolved['author'])
        self.assertEqual(resolved['identity'], 'mangakatana:berserk.1087')

    def test_resolve_preserves_existing_alias_and_adds_source_title_alias(self):
        mk_search = item('mangakatana', 'x', 'Canonical', aliases=['Legacy Alias'])
        mp_search = item('mangapill', 'y', 'Canonical Alt')
        mk = FakeSource('mangakatana', [mk_search], details_map={'x': mk_search})
        mp = FakeSource('mangapill', [mp_search], details_map={'y': mp_search})
        engine = self.make(mk, mp)
        manga = {
            'key': 'mangakatana:x', 'identity': 'mangakatana:x', 'title': 'Canonical',
            'aliases': ['Old Stored Alias'], 'variants': [mk_search, mp_search],
        }
        resolved, _ = engine.resolve_manga(manga)
        self.assertIn('Old Stored Alias', resolved['aliases'])
        self.assertIn('Legacy Alias', resolved['aliases'])
        self.assertIn('Canonical Alt', resolved['aliases'])

    def test_chapter_merge_keeps_normal_fallback_but_group2_separate(self):
        mk_ch = [{'source': 'mangakatana', 'id': 'mk1', 'number': '1', 'name': 'Chapter 1', 'language': 'en', 'groups': []}]
        mp_ch = [
            {'source': 'mangapill', 'id': 'mpg', 'number': '1', 'name': 'Berserk Group 2 Chapter 1', 'language': 'en', 'groups': ['Group 2']},
            {'source': 'mangapill', 'id': 'mpn', 'number': '1', 'name': 'Berserk Chapter 1', 'language': 'en', 'groups': []},
        ]
        a = FakeSource('mangakatana', [item('mangakatana', 'mk')], chapters=mk_ch)
        b = FakeSource('mangapill', [item('mangapill', 'mp')], chapters=mp_ch)
        engine = self.make(a, b)
        manga = {'key': 'x', 'variants': [item('mangakatana', 'mk'), item('mangapill', 'mp')]}
        chapters, errors, totals = engine.chapters(manga)
        self.assertFalse(errors)
        self.assertEqual(totals, {'mangakatana': 1, 'mangapill': 2})
        self.assertEqual(len(chapters), 2)
        normal = next(ch for ch in chapters if not ch.get('groups'))
        grouped = next(ch for ch in chapters if ch.get('groups'))
        self.assertEqual(normal['source'], 'mangakatana')
        self.assertEqual(engine.chapter_alternates(normal)[0]['id'], 'mpn')
        self.assertEqual(grouped['id'], 'mpg')

    def test_force_chapters_propagates_to_adapter(self):
        a = FakeSource('mangakatana', [item('mangakatana', 'mk')], chapters=[{'source': 'mangakatana', 'id': 'x', 'number': '1'}])
        b = FakeSource('mangapill', [])
        engine = self.make(a, b)
        manga = {'key': 'x', 'variants': [item('mangakatana', 'mk')]}
        engine.chapters(manga)
        engine.chapters(manga)
        self.assertEqual(a.chapter_calls, [False])
        engine.chapters(manga, force=True)
        self.assertEqual(a.chapter_calls, [False, True])

    def test_source_report_missing_source_network_error_is_not_not_found(self):
        mk = FakeSource('mangakatana', [item('mangakatana', 'mk')], chapters=[])
        mp = FakeSource('mangapill', search_error=SourceError('timeout', source='mangapill', kind='timeout', transient=True))
        engine = self.make(mk, mp)
        manga = {'key': 'x', 'title': 'Kokou no Hito', 'variants': [item('mangakatana', 'mk')]}
        report = engine.source_report(manga)
        self.assertEqual(report['mangapill']['status'], 'error')
        self.assertFalse(report['mangapill']['verified'])

    def test_source_report_not_found_requires_successful_search(self):
        mk = FakeSource('mangakatana', [item('mangakatana', 'mk')])
        mp = FakeSource('mangapill', [])
        engine = self.make(mk, mp)
        manga = {'key': 'x', 'title': 'Kokou no Hito', 'variants': [item('mangakatana', 'mk')]}
        report = engine.source_report(manga)
        self.assertEqual(report['mangapill']['status'], 'not_found')
        self.assertTrue(report['mangapill']['verified'])

    def test_source_report_empty_unverified_search_stays_unknown(self):
        mk = FakeSource('mangakatana', [item('mangakatana', 'mk')])
        mp = FakeSource('mangapill', [], search_status='unknown')
        engine = self.make(mk, mp)
        manga = {'key': 'x', 'title': 'Berserk', 'variants': [item('mangakatana', 'mk', 'Berserk')]}
        report = engine.source_report(manga)
        self.assertEqual(report['mangapill']['status'], 'unknown')
        self.assertFalse(report['mangapill']['verified'])

    def test_source_report_ambiguous_exact_matches_is_not_found_false(self):
        mk = FakeSource('mangakatana', [item('mangakatana', 'mk')])
        mp = FakeSource('mangapill', [item('mangapill', 'a', 'Same'), item('mangapill', 'b', 'Same')])
        engine = self.make(mk, mp)
        manga = {'key': 'x', 'title': 'Same', 'variants': []}
        report = engine.source_report(manga)
        self.assertEqual(report['mangapill']['status'], 'ambiguous')

    def test_refresh_preserves_old_variant_on_transient_source_failure(self):
        old_mk = item('mangakatana', 'berserk.1087', 'Berserk')
        old_mp = item('mangapill', '1/berserk', 'Berserk')
        mk = FakeSource('mangakatana', search_error=SourceError('timeout', source='mangakatana', kind='timeout', transient=True),
                        details_error=SourceError('timeout', source='mangakatana', kind='timeout', transient=True))
        mp = FakeSource('mangapill', [old_mp])
        engine = self.make(mk, mp)
        manga = {'key': 'mangakatana:berserk.1087', 'identity': 'mangakatana:berserk.1087', 'title': 'Berserk',
                 'variants': [old_mk, old_mp]}
        fresh, errors = engine.refresh_manga(manga)
        self.assertIn('mangakatana', errors)
        self.assertEqual({(v['source'], v['id']) for v in fresh['variants']},
                         {('mangakatana', 'berserk.1087'), ('mangapill', '1/berserk')})

    def test_refresh_removes_old_variant_only_after_explicit_404(self):
        old_mk = item('mangakatana', 'gone.1', 'Gone')
        old_mp = item('mangapill', '2/gone', 'Gone')
        mk = FakeSource('mangakatana', [], details_error=SourceError('missing', source='mangakatana', status=404, kind='not_found'))
        mp = FakeSource('mangapill', [old_mp])
        engine = self.make(mk, mp)
        manga = {'key': 'mangakatana:gone.1', 'identity': 'mangakatana:gone.1', 'title': 'Gone', 'variants': [old_mk, old_mp]}
        fresh, _ = engine.refresh_manga(manga)
        self.assertEqual([(v['source'], v['id']) for v in fresh['variants']], [('mangapill', '2/gone')])

    def test_invalidate_propagates_to_sources(self):
        a = FakeSource('mangakatana', [item('mangakatana', 'mk')])
        b = FakeSource('mangapill', [item('mangapill', 'mp')])
        engine = self.make(a, b)
        manga = {'key': 'x', 'variants': [item('mangakatana', 'mk'), item('mangapill', 'mp')]}
        engine.invalidate_chapters(manga)
        self.assertEqual(a.invalidated[-1], {'id': 'mk'})
        self.assertEqual(b.invalidated[-1], {'id': 'mp'})


if __name__ == '__main__':
    unittest.main()
