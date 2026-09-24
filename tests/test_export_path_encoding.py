"""SBPL path literals must reproduce the real directory bytes and keep the deny rules.

The exported sandbox profile is the only place that decides which directory the
native helper may write to, so a literal that decodes to anything other than the
job directory silently breaks the export (it used to be JSON-encoded, which SBPL
does not parse). These checks are string-level and need no sandbox; the real
`sandbox-exec` round trip is run separately.
"""
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / 'work/export-path-tests'
sys.path.insert(0, str(ROOT / 'engine'))
import native_export as e

# The seven path forms reviewed for this change, plus the control bytes that a
# shell cannot easily pass (json.dumps destroyed all of these forms). macOS
# rejects file names that are not valid UTF-8, so an undecodable byte can only be
# asserted on the literal itself, below.
NAMES = ('ascii', '路径-中文', '带 空格', '带"引号', '带\\反斜杠', '带\n换行',
         '空"格\\双\n新 行', '带\x01abcdef', '带\t制表', '带\r回车', '带\x7fDEL')

RULES = (b'(deny network*)', b'(deny file-write*)',
         b'(deny file-read-data (subpath "/Users"))',
         b'(deny file-read-data (subpath "/Library/Keychains"))',
         b'(allow file-write* (literal "/dev/null"))')


def decode_sbpl_literal(literal):
    """Independent decoder for the literal subset the export profile may contain."""
    if not (literal.startswith(b'"') and literal.endswith(b'"')):
        raise ValueError('SBPL string is not quoted: ' + repr(literal))
    named = {ord('\\'): 0x5c, ord('"'): 0x22, ord('n'): 0x0a, ord('r'): 0x0d, ord('t'): 0x09}
    decoded = bytearray()
    index = 1
    while index < len(literal) - 1:
        byte = literal[index]
        if byte != 0x5c:
            decoded.append(byte)
            index += 1
        elif literal[index + 1] == ord('x'):
            decoded.append(int(literal[index + 2:index + 4], 16))
            index += 4
        else:
            decoded.append(named[literal[index + 1]])
            index += 2
    return bytes(decoded)


def subpath_literals(profile):
    """Both subpath literals of the generated profile, in profile order."""
    findings = []
    for prefix in (b'(allow file-read-data (subpath ', b'(allow file-write* (subpath '):
        for line in profile.splitlines():
            if line.startswith(prefix) and line.endswith(b'))'):
                findings.append(line[len(prefix):-2])
    return findings


class ExportPathEncoding(unittest.TestCase):
    def setUp(self):
        WORK.mkdir(parents=True, exist_ok=True)
        folder = tempfile.mkdtemp(prefix='case-', dir=WORK)
        self.addCleanup(shutil.rmtree, folder)
        self.root = Path(folder)

    def test_literal_matches_the_real_directory_name(self):
        for name in NAMES:
            with self.subTest(name=name):
                directory = self.root / name
                directory.mkdir()
                profile = e.sandbox_profile(directory)
                self.assertIsInstance(profile, bytes)
                literals = subpath_literals(profile)
                self.assertEqual(len(literals), 2)
                self.assertEqual(literals[0], literals[1])
                self.assertEqual([decode_sbpl_literal(value) for value in literals],
                                 [os.fsencode(str(directory))] * 2)

    def test_profile_keeps_the_deny_rules_and_no_json_escape(self):
        for name in NAMES:
            with self.subTest(name=name):
                directory = self.root / name
                directory.mkdir()
                profile = e.sandbox_profile(directory)
                for rule in RULES:
                    self.assertIn(rule, profile)
                self.assertIsNone(re.search(rb'\\u[0-9a-fA-F]{4}', profile))

    def test_escaping_is_exact_and_never_rejected(self):
        path = os.fsdecode(b'quote"\\\n\r\t\x01c\x7f\xff')
        literal = e.sbpl_literal(path)
        self.assertEqual(literal, b'"quote\\"\\\\\\n\\r\\t\\x01c\\x7f\xff"')
        self.assertEqual(decode_sbpl_literal(literal), os.fsencode(path))

    def test_adjacent_hex_characters_do_not_extend_the_escape(self):
        # SBPL reads at most two hex digits after \x, so a following hex character
        # must survive verbatim; an octal escape here would be consumed wrongly.
        path = '带\x01abcdef'
        self.assertEqual(e.sbpl_literal(path), b'"\xe5\xb8\xa6\\x01abcdef"')
        self.assertEqual(decode_sbpl_literal(e.sbpl_literal(path)), os.fsencode(path))


if __name__ == '__main__':
    unittest.main()
