"""build.py 검사:  python -m unittest discover -s tools"""
import unittest

import build


def page(number, body, cont=False):
    flag = " | 이어짐" if cont else ""
    return f"<!-- p.{number} | 卷三 張1a{flag} -->\n\n{body}\n"


class CheckTest(unittest.TestCase):
    def test_bracket_spanning_two_pages_is_ok(self):
        pages = {1: page(1, "【衾】〈古禮至"), 2: page(2, "小斂去之〉", cont=True)}
        self.assertEqual(build.check(pages, 1, 2, final=True), [])

    def test_unclosed_bracket_is_error(self):
        pages = {1: page(1, "〈古禮"), 2: page(2, "之")}
        errors = build.check(pages, 1, 2, final=True)
        self.assertTrue(any("〈" in e for e in errors), errors)

    def test_wrongly_nested_brackets_are_error(self):
        pages = {1: page(1, "〈｛古〉禮｝")}
        errors = build.check(pages, 1, 1, final=True)
        self.assertTrue(any("p.1" in e for e in errors), errors)

    def test_open_bracket_before_untranscribed_page_is_allowed_while_in_progress(self):
        pages = {1: page(1, "〈古禮"), 3: page(3, "之〉", cont=True)}
        self.assertEqual(build.check(pages, 1, 4, final=False), [])

    def test_missing_page_is_error_only_when_final(self):
        pages = {1: page(1, "古"), 3: page(3, "禮")}
        self.assertEqual(build.check(pages, 1, 3, final=False), [])
        errors = build.check(pages, 1, 3, final=True)
        self.assertTrue(any("p.2" in e for e in errors), errors)

    def test_hangul_in_body_is_error_but_comment_and_marker_are_not(self):
        ok = {1: page(1, "古[한글 생략]禮\n<!-- 확인: 설명 -->")}
        self.assertEqual(build.check(ok, 1, 1, final=True), [])
        bad = {1: page(1, "古례")}
        errors = build.check(bad, 1, 1, final=True)
        self.assertTrue(any("한글" in e for e in errors), errors)

    def test_header_page_number_must_match_file(self):
        errors = build.check({2: page(1, "古")}, 2, 2, final=True)
        self.assertTrue(any("머리줄" in e for e in errors), errors)


class AssembleTest(unittest.TestCase):
    def test_continuation_joins_previous_paragraph_and_keeps_page_mark(self):
        pages = {1: page(1, "### 復\n\n男子不絕於"),
                 2: page(2, "婦人之手\n\n【諸具】", cont=True)}
        out = build.assemble("卷之三", pages, [1, 2])
        self.assertIn("男子不絕於<!-- p.2 -->婦人之手\n\n【諸具】", out)

    def test_continuation_of_quoted_note_drops_second_quote_mark(self):
        pages = {1: page(1, "> 〈〔按〕冠"), 2: page(2, "> 服條〉", cont=True)}
        out = build.assemble("卷之三", pages, [1, 2])
        self.assertIn("> 〈〔按〕冠<!-- p.2 -->服條〉", out)

    def test_page_without_continuation_starts_new_paragraph(self):
        pages = {1: page(1, "古"), 2: page(2, "禮")}
        out = build.assemble("卷之三", pages, [1, 2])
        self.assertIn("古\n\n<!-- p.2 -->\n\n禮", out)

    def test_continuation_does_not_join_across_a_missing_page(self):
        pages = {1: page(1, "古"), 3: page(3, "禮", cont=True)}
        out = build.assemble("卷之三", pages, [1, 3])
        self.assertNotIn("古<!-- p.3 -->", out)
        self.assertIn("미전사", out)

    def test_contents_list_headings_with_page_numbers(self):
        pages = {1: page(1, "## 初終\n\n### 復\n\n古")}
        out = build.assemble("卷之三", pages, [1])
        self.assertIn("- 初終 (p.1)", out)
        self.assertIn("  - 復 (p.1)", out)


class UncertainTest(unittest.TestCase):
    def test_lists_uncertain_and_unreadable_with_page_and_context(self):
        pages = {7: page(7, "嘗論要設[?]中用孝巾□行祭")}
        found = build.uncertain(pages)
        self.assertEqual([n for n, _ in found], [7, 7])
        self.assertIn("要設[?]", found[0][1])
        self.assertIn("□", found[1][1])


class ReportTest(unittest.TestCase):
    def test_row_shows_volume_and_section_carried_over_from_earlier_page(self):
        pages = {132: page(132, "## 初終\n\n古"), 133: page(133, "要設[?]中")}
        out = build.report(pages)
        self.assertIn("# 확인 필요 (1곳)", out)
        self.assertIn("| 쪽 | 권 | 편 | 문맥 |", out)
        self.assertIn("| 133 | 권3 | 初終 | 要設[?]中 |", out)

    def test_mark_before_a_new_heading_belongs_to_previous_section(self):
        pages = {132: page(132, "## 襲\n\n古"),
                 133: page(133, "甲[?]\n\n## 小斂\n\n乙[?]")}
        rows = build.report(pages).splitlines()[-2:]
        self.assertTrue(rows[0].startswith("| 133 | 권3 | 襲 |"), rows)
        self.assertTrue(rows[1].startswith("| 133 | 권3 | 小斂 |"), rows)

    def test_subheading_does_not_replace_section(self):
        pages = {132: page(132, "## 初終\n\n### 復\n\n古[?]")}
        self.assertIn("| 132 | 권3 | 初終 |", build.report(pages))

    def test_section_does_not_carry_into_next_volume(self):
        pages = {193: page(193, "## 大斂\n\n古"), 210: page(210, "禮[?]")}
        self.assertIn("| 210 | 권4 |  | 禮[?] |", build.report(pages))


if __name__ == "__main__":
    unittest.main()
