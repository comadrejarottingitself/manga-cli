"""Freeze 0.7.5 behavior while permitting named UI-message substitutions.

Expected fingerprints are derived from the supplied 0.7.5 ZIP, except reader.py,
which now also includes the narrowly scoped 0.8.3 reader compatibility,
bidirectional-prefetch and uppercase fit-key changes. Other protected modules remain
frozen. Tests normalize only localized message calls/imports, or Lua's generated
text table, not command/protocol strings.
"""
import ast
import hashlib
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


def lua_tokens(text):
    text = re.sub(r'-- BEGIN GENERATED TEXT\n.*?-- END GENERATED TEXT', '', text, flags=re.S)
    tokens = re.findall(r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|--[^\n]*|[A-Za-z_][A-Za-z_0-9]*|[0-9]+(?:\.[0-9]*)?|\S", text)
    return '\n'.join(token for token in tokens if not token.startswith('--'))


class EraseMessages(ast.NodeTransformer):
    def visit_ImportFrom(self, node):
        if node.module == 'i18n' or (node.module or '').endswith('.i18n'):
            return None
        return node

    def visit_Call(self, node):
        if (isinstance(node.func, ast.Name) and node.func.id == 'tr' and len(node.args) == 1
                and isinstance(node.args[0], ast.Constant) and not node.keywords):
            return ast.Constant('<text:' + node.args[0].value + '>')
        return self.generic_visit(node)


def ast_payload(node):
    # Python 3.12 added empty type_params fields to existing AST nodes. Exclude
    # this unused field so the baseline is also stable on target Python 3.11.
    if isinstance(node, ast.AST):
        return (type(node).__name__, tuple((field, ast_payload(value))
                for field, value in ast.iter_fields(node) if field != 'type_params'))
    if isinstance(node, list):
        return tuple(ast_payload(value) for value in node)
    return node


def fingerprint(tree):
    return hashlib.sha256(repr(ast_payload(tree)).encode()).hexdigest()


PROTECTED_BASELINE = {'acmanga/reader.py': 'dc04677d5494b3b2088ba7402312a76e15707303d97178465e552df3b5bc89ac', 'acmanga/streaming.py': 'f85cb6ff67c6678f5c562e20d2d01f3762c82184d1eceeb059c0ceaba82a118a', 'acmanga/engine.py': 'b7bae9e84343e0925787a23310527fabd91de879a2286659e3d1f017ac472948', 'acmanga/util.py': '8d1addea2f8401e390e531ac8939f71130fdfd09f7ea523887754eb3d3c09f6f', 'acmanga/sources/mangakatana.py': '27308926b02af6fc5a49ddf0ba9e0542b13eaead10912f0517ccb4e1949488a2', 'acmanga/sources/mangapill.py': '29a99c19652842af5c4f9d3fb9b0ac85f356ce53f9565c33beb559838e57d866', 'manga.py:read_sequence': '2ab15da9192914b855f34d3b92c13326bf4a55766fc812eb97a4572dc9d07935'}
LUA_PROTECTED_BASELINE = 'd946b6201e89d1dd69389b2216e58f8a49ad4d3ef8edfa98656f217690ba2c84'
STATE_PROTECTED_BASELINE = 'b28ad0cc5a1fd09f82bf17fe3e9f5a8eccaaba35b5b03c7c07207df1890ef3dd'


class ProtectedContractTests(unittest.TestCase):
    def test_reader_controller_matches_reviewed_contract(self):
        if (ROOT / 'acmanga/reader_android.lua').exists():
            self.skipTest('Android branch intentionally extends reader.py; desktop behavior is covered behaviorally')
        self.check_file('acmanga/reader.py')

    def test_prefetch_and_cache_match_reviewed_contract(self):
        self.check_file('acmanga/streaming.py')

    def test_source_engine_matches_075_except_localized_text(self):
        self.check_file('acmanga/engine.py')

    def test_http_utilities_match_075_except_localized_text(self):
        self.check_file('acmanga/util.py')

    def test_mangakatana_matches_075_except_localized_text(self):
        self.check_file('acmanga/sources/mangakatana.py')

    def test_mangapill_matches_075_except_localized_text(self):
        self.check_file('acmanga/sources/mangapill.py')

    def check_file(self, rel):
        tree = EraseMessages().visit(ast.parse((ROOT / rel).read_text(encoding='utf-8')))
        self.assertEqual(fingerprint(tree), PROTECTED_BASELINE[rel], rel)

    def test_cross_chapter_flow_matches_075_except_localized_text(self):
        tree = ast.parse((ROOT / 'manga.py').read_text(encoding='utf-8'))
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'read_sequence')
        self.assertEqual(fingerprint(EraseMessages().visit(node)), PROTECTED_BASELINE['manga.py:read_sequence'])

    def test_lua_control_tokens_match_reviewed_contract(self):
        text = (ROOT / 'acmanga/reader.lua').read_text(encoding='utf-8')
        self.assertEqual(hashlib.sha256(lua_tokens(text).encode()).hexdigest(), LUA_PROTECTED_BASELINE)

    def test_state_storage_is_byte_identical_to_reviewed_baseline(self):
        self.assertEqual(hashlib.sha256((ROOT / 'acmanga/state.py').read_bytes()).hexdigest(), STATE_PROTECTED_BASELINE)
