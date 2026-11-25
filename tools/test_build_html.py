"""build_html.py 단위 테스트: python -m unittest discover -s tools"""
import unittest
from html.parser import HTMLParser

import build_html
import hanja_readings


class StrictTagValidator(HTMLParser):
    """열고 닫는 HTML 태그가 정확히 중첩되는지 검증한다."""
    VOID_TAGS = {"meta", "link", "input", "br", "hr", "img"}

    def __init__(self):
        super().__init__()
        self.stack = []
        self.errors = []

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID_TAGS:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.VOID_TAGS:
            return
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"Mismatched </{tag}>, stack={self.stack[-3:]}")
        else:
            self.stack.pop()


class BuildHtmlTest(unittest.TestCase):
    def test_cross_paragraph_bracket_produces_valid_html(self):
        stack = []
        ref = [404]
        p1 = build_html.render_inline("孝曾孫〈屬稱", stack, {}, ref)
        p2 = build_html.render_inline("隨改〉某", stack, {}, ref)
        self.assertEqual(stack, [])
        validator = StrictTagValidator()
        validator.feed(f"<p>{p1}</p><p>{p2}</p>")
        self.assertEqual(validator.errors, [])
        self.assertEqual(validator.stack, [])
        self.assertIn("cont-open", p2)

    def test_jegu_separator_only_after_closed_note(self):
        stack = []
        ref = [136]
        rendered = build_html.render_inline(
            "【執事者】【幃】〈或屏〉【席】【褥】〈並施牀上〉",
            stack,
            {},
            ref,
            jegu_items=True,
        )
        # 【幃】 앞이나 【褥】 앞에는 줄바꿈 구분자가 없고, 〈或屏〉 뒤의 【席】 앞에만 들어간다
        self.assertEqual(rendered.count('class="jegu-sep"'), 1)

    def test_uncertain_and_ids_render_tooltips(self):
        stack = []
        ref = [180]
        notes = {180: ["要設 — 문맥상 要訣", "⿱須巾 — 위 須 아래 巾"]}
        rendered = build_html.render_inline("論要設[?]中用⿱須巾", stack, notes, ref)
        self.assertIn('class="uncertain-mark"', rendered)
        self.assertIn('class="ids-glyph"', rendered)
        self.assertIn("문맥상 要訣", rendered)

    def test_full_project_html_is_valid_and_complete(self):
        import re
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent / "transcription"
        pages = {
            int(p.stem[1:]): p.read_text(encoding="utf-8")
            for p in sorted((root / "pages").glob("p*.md"))
        }
        doc = build_html.generate_html_document(pages)

        validator = StrictTagValidator()
        validator.feed(doc)
        self.assertEqual(validator.errors, [])
        self.assertEqual(validator.stack, [])

        page_ids = re.findall(r'id="p(\d+)"', doc)
        self.assertEqual(len(page_ids), len(pages))
        self.assertEqual(len(set(page_ids)), len(pages))

        all_ids = set(re.findall(r'id="([^"]+)"', doc))
        for target in re.findall(r'href="#([^"]+)"', doc):
            self.assertIn(target, all_ids)

        self.assertIsNone(re.search(r"[\x00\x01]|<!--|-->|###", doc))
        # 권3 전편(초종·습·소렴·대렴) 국역 대역 탑재 검증
        self.assertIn("초종(初終)", doc)
        self.assertIn("습(襲)", doc)
        self.assertIn("소렴(小斂)", doc)
        self.assertIn("대렴(大斂)", doc)
        self.assertIn("병이 위중해지면 정침으로 옮겨 거처한다", doc)
        self.assertIn("모위 좌전", doc)
        self.assertIn("모봉(某封) 모관(某貫, 본관) 모씨지구(某氏之柩)", doc)
        self.assertIn("집사자가 소렴의 의금(衣衾)을 진설한다", doc)
        self.assertIn("교대로 곡하는 이(代哭者)를 그치게 한다", doc)
        # 원문 한자 루비(독음) 탑재 검증
        self.assertIn("<ruby>四<rt>사</rt></ruby><ruby>禮<rt>례</rt></ruby><ruby>便<rt>편</rt></ruby><ruby>覽<rt>람</rt></ruby>", doc)
        self.assertIn("<ruby>斬<rt>참</rt></ruby><ruby>衰<rt>최</rt></ruby>", doc)
        self.assertIn("<ruby>齊<rt>자</rt></ruby><ruby>衰<rt>최</rt></ruby>", doc)
        self.assertIn("<ruby>秫<rt>술</rt></ruby><ruby>灰<rt>회</rt></ruby>", doc)
        self.assertIn("한자음: 켜짐", doc)

    def test_hanja_readings_cover_entire_corpus_and_polyphones(self):
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent / "transcription" / "pages"
        all_text = "".join(p.read_text(encoding="utf-8") for p in sorted(root.glob("p*.md")))
        for ch in set(all_text):
            if hanja_readings.is_cjk_char(ch):
                self.assertIn(ch, hanja_readings.CHAR_READINGS, f"독음 누락 한자: {ch} ({hex(ord(ch))})")
                self.assertTrue(hanja_readings.CHAR_READINGS[ch], f"빈 독음 한자: {ch}")

        self.assertEqual(hanja_readings.plain_reading_string("四禮便覽"), "사례편람")
        self.assertEqual(hanja_readings.plain_reading_string("禮記"), "예기")
        self.assertEqual(hanja_readings.plain_reading_string("斬衰齊衰"), "참최자최")
        self.assertEqual(hanja_readings.plain_reading_string("不脫絰帶"), "불탈질대")
        self.assertEqual(hanja_readings.plain_reading_string("不得"), "부득")
        self.assertEqual(hanja_readings.plain_reading_string("無則"), "무즉")
        self.assertEqual(hanja_readings.plain_reading_string("㺽珓"), "배교")
        self.assertEqual(hanja_readings.plain_reading_string("飯蓋扱匕正筯"), "반개삽비정저")
        self.assertEqual(hanja_readings.plain_reading_string("褡⿰衤蒦"), "답호")

    def test_translation_files_have_balanced_brackets(self):
        from pathlib import Path

        trans_dir = Path(__file__).resolve().parent.parent / "translation"
        pairs = [("｛", "｝"), ("〈", "〉"), ("〔", "〕"), ("【", "】")]
        files = sorted(trans_dir.glob("*.md"))
        self.assertGreaterEqual(len(files), 4)
        for f in files:
            text = f.read_text(encoding="utf-8")
            for op, cl in pairs:
                self.assertEqual(
                    text.count(op),
                    text.count(cl),
                    f"{f.name}: {op}/{cl} 괄호 개수 불일치 ({text.count(op)} != {text.count(cl)})",
                )


if __name__ == "__main__":
    unittest.main()
