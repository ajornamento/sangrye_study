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


class Rehydrator(HTMLParser):
    """data-r 에 모아 둔 독음으로 루비를 다시 만든다(브라우저의 hydrate 와 같은 규칙)."""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.out = []
        self.units = []  # [태그, 같은 태그 중첩 깊이, 독음, 다음 위치]
        self.in_rt = 0

    def handle_starttag(self, tag, attrs):
        attrs_d = dict(attrs)
        raw = self.get_starttag_text()
        if "data-r" in attrs_d:
            raw = raw.replace(f' data-r="{attrs_d["data-r"]}"', "")
            self.units.append([tag, 0, attrs_d["data-r"], 0])
        if self.units and tag == self.units[-1][0]:
            self.units[-1][1] += 1
        self.in_rt += tag == "rt"
        self.out.append(raw)

    def handle_endtag(self, tag):
        self.in_rt -= tag == "rt"
        self.out.append(f"</{tag}>")
        if self.units and tag == self.units[-1][0]:
            self.units[-1][1] -= 1
            if self.units[-1][1] == 0:
                self.units.pop()

    def handle_data(self, data):
        self._text([(ch, ch) for ch in data])

    def handle_entityref(self, name):
        self._text([(f"&{name};", None)])

    def handle_charref(self, name):
        self._text([(f"&#{name};", None)])

    def handle_decl(self, decl):
        self.out.append(f"<!{decl}>")

    def _text(self, chars):
        for raw, _ in chars:
            if self.units and not self.in_rt:
                unit = self.units[-1]
                reading = unit[2][unit[3]:unit[3] + 1]
                unit[3] += 1
                if reading not in ("", "."):
                    raw = f"<ruby>{raw}<rt>{reading}</rt></ruby>"
            self.out.append(raw)


def rehydrate(doc):
    parser = Rehydrator()
    parser.feed(doc)
    parser.close()
    return "".join(parser.out)


class BuildHtmlTest(unittest.TestCase):
    def test_defer_ruby_moves_readings_to_unit_attribute_and_is_reversible(self):
        rendered = build_html.render_inline("四禮〈便覽〉、A&B　用⿱須巾", [], {}, [180])
        unit = f'<p class="body-para">{rendered}</p>'
        deferred = build_html.defer_ruby(unit)
        self.assertNotIn("<ruby>", deferred)
        self.assertIn('<p class="body-para" data-r="사례', deferred)
        self.assertIn('class="ids-glyph"', deferred)  # 한 글자가 아닌 루비는 그대로
        self.assertEqual(rehydrate(deferred), unit)

    def test_defer_ruby_leaves_unit_without_readings_untouched(self):
        unit = '<p class="body-para">abc</p>'
        self.assertEqual(build_html.defer_ruby(unit), unit)

    def test_script_creates_ruby_only_near_viewport(self):
        script = build_html.JS_SCRIPT
        self.assertIn("function hydrate(unit)", script)
        self.assertIn("function dehydrate(unit)", script)
        self.assertIn("rootMargin: '1500px 0px'", script)
        self.assertIn("'beforeprint'", script)
        # 루비를 품은 검색 표시를 지울 때 독음이 본문 글자로 섞이면 안 된다
        self.assertIn("m.replaceWith(...m.childNodes)", script)
        self.assertNotIn("createTextNode(m.textContent)", script)
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
        # 권4 전편(성복·조·문상) 국역 대역 탑재 검증
        self.assertIn("성복(成服)", doc)
        self.assertIn("조문(弔)", doc)
        self.assertIn("문상(聞喪, 상 소식을 들음)", doc)
        self.assertIn("상복의 제도는, 첫째가 참최(斬衰) 삼년이다", doc)
        self.assertIn("남의 부모상을 위문하는 소(疏)의 서식", doc)
        self.assertIn("만약 분상하지 못하면 나흘째에 성복한다", doc)
        # 권5 전편(치장·천구·발인·급묘·성분·반곡) 국역 대역 탑재 검증
        self.assertIn("치장(治葬, 장사의 준비)", doc)
        self.assertIn("천구(遷柩, 영구를 옮김)", doc)
        self.assertIn("상주 이하는 곡하며 걸어서 따른다", doc)
        self.assertIn("신주에 글을 쓴다(題主)", doc)
        self.assertIn("성분(成墳, 봉분을 이룸)", doc)
        self.assertIn("반곡(反哭, 돌아와 곡함)", doc)
        # 권6 전편(우제·졸곡·부·소상·대상·담·길제) 국역 대역 탑재 검증
        self.assertIn("우제(虞祭)", doc)
        self.assertIn("졸곡(卒哭)", doc)
        self.assertIn("소상(小祥)", doc)
        self.assertIn("대상(大祥)", doc)
        self.assertIn("담제(禫)", doc)
        self.assertIn("길제(吉祭)", doc)
        self.assertIn("조천한 신주를 받들어 묘 곁에 묻는다", doc)
        self.assertIn("침실로 돌아간다(復寢)", doc)
        # 원문 한자 루비(독음) 탑재 검증: 문서에는 루비 요소 대신 data-r 만 싣는다
        self.assertNotIn("<ruby>", doc.split("<script>")[0])
        full = rehydrate(doc)
        self.assertIn("<ruby>四<rt>사</rt></ruby><ruby>禮<rt>례</rt></ruby><ruby>便<rt>편</rt></ruby><ruby>覽<rt>람</rt></ruby>", full)
        self.assertIn("<ruby>斬<rt>참</rt></ruby><ruby>衰<rt>최</rt></ruby>", full)
        self.assertIn("<ruby>齊<rt>자</rt></ruby><ruby>衰<rt>최</rt></ruby>", full)
        self.assertIn("<ruby>秫<rt>술</rt></ruby><ruby>灰<rt>회</rt></ruby>", full)
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
        # 권4 다음자
        for hanja, reading in (
            ("括髮免", "괄발문"), ("免布", "문포"), ("首免", "수문"), ("幸免", "행면"),
            ("畏厭溺", "외압닉"), ("所識", "소식"), ("以見其例", "이현기례"),
            ("見小功", "현소공"), ("更著", "갱저"), ("具著喪服", "구저상복"),
            ("云疏食", "운소사"),
            ("竹簁", "죽사"), ("〔馬木〕具", "마목구"),
            # 권6 다음자
            ("復上食", "부상식"), ("啓殯復散", "계빈부산"), ("復三年", "부삼년"),
            ("復入哭", "부입곡"), ("復就", "부취"), ("復書", "복서"), ("逐行", "축항"),
            # 권5 다음자
            ("卒哭則免", "졸곡즉문"), ("廬墓之免", "여묘지면"), ("報虞", "부우"),
            ("不報虞", "불부우"), ("剡上", "염상"), ("畚蕢", "분궤"),
            # 번역 점검에서 확인한 정음
            ("幅巾", "복건"), ("全幅", "전폭"), ("皆袒免", "개단문"),
        ):
            self.assertEqual(hanja_readings.plain_reading_string(hanja), reading, hanja)

    def test_jumps_are_instant_and_clicked_toc_item_is_highlighted(self):
        # 문서가 커서 부드러운 스크롤은 도착까지 1초 넘게 걸린다. 모든 이동은 즉시 이동이어야 한다.
        doc = build_html.generate_html_document(
            {n: f"<!-- p.{n} | 卷三 張1a -->\n\n### 疾病\n\n凡疾病" for n in (132, 210, 300, 368)},
            translations={},
        )
        self.assertNotIn("smooth", doc)
        handler = build_html.JS_SCRIPT.split("'.toc-link, .page-jump-link'")[1].split("// 목차 필터링")[0]
        self.assertIn("link.classList.add('active')", handler)

    def test_offscreen_blocks_defer_layout_except_headings_and_print(self):
        css = build_html.CSS_STYLES
        selector = ".volume-section:not(.guide-section) > :not(h2, h3, header)"
        screen, printed = css.split("@media print")
        self.assertIn(selector + " {\n  content-visibility: auto;", screen)
        self.assertIn(selector + " { content-visibility: visible; }", printed)
        # 어림 크기 때문에 밀릴 수 있어, 모든 본문 이동은 그린 뒤 다시 맞추는 jumpTo 를 거친다
        script = build_html.JS_SCRIPT
        self.assertEqual(script.count("scrollIntoView("), 1)
        # 주변 본문은 몇 프레임 뒤에 그려지므로 여러 프레임에 걸쳐 맞춘다
        self.assertIn("if (frames > 0) requestAnimationFrame(() => jumpTo(el, frames - 1));", script)
        self.assertEqual(script.count("jumpTo("), 5)  # 정의 1 + 재호출 1 + 사용 3

    def test_scroll_observer_does_not_override_clicked_toc_item(self):
        script = build_html.JS_SCRIPT
        handler = script.split("'.toc-link, .page-jump-link'")[1].split("// 목차 필터링")[0]
        observer = script.split("new IntersectionObserver(")[1]
        self.assertIn("pinTocUntil = performance.now()", handler)
        self.assertIn("if (performance.now() < pinTocUntil) return;", observer)

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
