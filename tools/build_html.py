"""전사된 쪽 파일(transcription/pages/p*.md)을 읽어 『사례편람』(상례편) 통합 HTML 정본 문서를 생성한다.

  python tools/build_html.py
  python tools/build_html.py --out transcription/사례편람.html
"""
import argparse
import html
import re
import sys
from pathlib import Path

import build
import hanja_readings

VOLUME_META = {
    "권3": {"id": "vol3", "short": "卷三", "title": "四禮便覽卷之三", "sub": "喪禮一", "desc": "初終 · 襲 · 小斂 · 大斂"},
    "권4": {"id": "vol4", "short": "卷四", "title": "四禮便覽卷之四", "sub": "喪禮二", "desc": "成服 · 五服 · 朝夕奠 · 弔 · 聞喪 · 奔喪"},
    "권5": {"id": "vol5", "short": "卷五", "title": "四禮便覽卷之五", "sub": "喪禮三", "desc": "治葬 · 遷柩 · 發引 · 及墓 · 題主 · 反哭"},
    "권6": {"id": "vol6", "short": "卷六", "title": "四禮便覽卷之六", "sub": "喪禮四", "desc": "虞祭 · 卒哭 · 祔 · 小祥 · 大祥 · 禫 · 吉祭"},
}

HEADER_DETAIL = re.compile(r"\A\s*<!--\s*p\.(\d+)\s*\|\s*([^>]*)-->")
CHECK_COMMENT = re.compile(r"<!--\s*확인:\s*(.*?)-->", re.S)
PAGE_TOKEN = re.compile(r"\x00PAGE:(\d+):([^\x01]*)\x01")
IDS_SEQ = re.compile(r"[\u2ff0-\u2ffb].{2}")
FORM_START = re.compile(r"^(?:〈[附부]〉\s*)?【[^】]*(?:式|식)】")
COLOPHON = re.compile(r"^四禮便覽卷之[三四五六]$")
TRANS_META = re.compile(r"\A\s*<!--\s*vol:\s*(\S+)\s*\|\s*section:\s*(\S+)\s*-->")


def load_translations(trans_dir):
    """translation/*.md 파일들을 읽어 {(권키, 대항목명): [번역 블록 리스트]} 사전을 돌려준다."""
    result = {}
    if not trans_dir.exists():
        return result
    for p in sorted(trans_dir.glob("*.md")):
        raw = p.read_text(encoding="utf-8")
        m = TRANS_META.match(raw)
        if not m:
            continue
        vol_key, sec_name = m.group(1), m.group(2)
        body = raw[m.end():].strip()
        blocks = [b.strip() for b in re.split(r"\n{2,}", body) if b.strip()]
        result[(vol_key, sec_name)] = blocks
    return result

BRACKET_OPEN = {
    "〈": ("layer-note", "brk"),
    "｛": ("layer-supp", "brk"),
    "〔": ("cartouche-round", "brk-c"),
    "【": ("cartouche-black", "brk-c"),
}
BRACKET_CLOSE = {
    "〉": ("〈", "brk"),
    "｝": ("｛", "brk"),
    "〕": ("〔", "brk-c"),
    "】": ("【", "brk-c"),
}


def parse_page_meta(raw_text):
    """쪽 머리줄에서 (쪽번호, 장차 문자열, 빈면 여부)와 확인 주석 목록을 추출한다."""
    jang = ""
    is_empty = False
    m = HEADER_DETAIL.match(raw_text)
    if m:
        parts = [p.strip() for p in m.group(2).split("|")]
        if parts:
            # 예: "卷三 張1a" -> "張1a"
            tokens = parts[0].split()
            jang = tokens[-1] if len(tokens) >= 2 else parts[0]
        is_empty = any("빈 면" in p for p in parts)
    notes = [" ".join(n.strip().split()) for n in CHECK_COMMENT.findall(raw_text)]
    return jang, is_empty, notes


def strip_page_tokens(text):
    return PAGE_TOKEN.sub("", text).strip()


def assemble_volume_tokens(pages, numbers, page_meta):
    """쪽 번호 순서대로 본문을 합치되 쪽 전환 위치에 PAGE 토큰을 삽입한다."""
    out = ""
    previous = None
    for n in numbers:
        text = build.body_of(pages[n]).replace("[한글 생략]", "")
        jang, is_empty, _ = page_meta[n]
        label = f"{jang}" if jang else ""
        if is_empty and not text:
            token = f"\x00PAGE:{n}:{label}|EMPTY\x01"
        else:
            token = f"\x00PAGE:{n}:{label}\x01"

        if previous is None:
            out = f"{token}\n\n{text}" if text else token
        elif n != previous + 1:
            gap = f"*(p.{previous + 1}–{n - 1} 미전사)*"
            out += f"\n\n{gap}\n\n{token}\n\n{text}" if text else f"\n\n{gap}\n\n{token}"
        elif build.continues(pages[n]) and text:
            if out.rsplit("\n", 1)[-1].startswith(">") and text.startswith(">"):
                text = text[1:].lstrip()
            out += token + text
        else:
            out += f"\n\n{token}\n\n{text}" if text else f"\n\n{token}"
        previous = n
    return out


def render_inline(text, stack, page_notes, current_page_ref, jegu_items=False, with_ruby=True):
    """문단 하나를 인라인 HTML로 렌더링한다.

    문단 경계를 넘어가는 괄호(서식 내 두 줄 주석 등)가 있을 때에도
    문단 끝에서 열린 태그를 모두 닫고 다음 문단 시작에서 다시 열어
    항상 올바른 중첩의 HTML을 보장한다.
    """
    out = []
    reading_map = hanja_readings.compute_readings(text) if with_ruby else {}
    # 이전 문단에서 열린 채 넘어온 괄호 스택 복원
    for opener in stack:
        cls, _ = BRACKET_OPEN[opener]
        out.append(f'<span class="{cls} cont-open">')

    pos = 0
    length = len(text)
    prev_non_space = ""

    while pos < length:
        if text[pos] == "\x00":
            m = PAGE_TOKEN.match(text, pos)
            if m:
                pnum = int(m.group(1))
                jang = m.group(2)
                current_page_ref[0] = pnum
                is_empty = jang.endswith("|EMPTY")
                if is_empty:
                    jang = jang[:-6]
                disp = f"p.{pnum}" + (f" · {jang}" if jang else "")
                note_list = page_notes.get(pnum, [])
                tip = f"PDF p.{pnum}" + (f" ({jang})" if jang else "")
                if note_list:
                    tip += " | 확인 메모: " + " / ".join(note_list)
                extra_cls = " has-note" if note_list else ""
                if is_empty:
                    out.append(
                        f'<span class="page-mark empty-page-mark{extra_cls}" id="p{pnum}" '
                        f'data-page="{pnum}" title="{html.escape(tip)}">{html.escape(disp)} (빈 면)</span>'
                    )
                else:
                    out.append(
                        f'<span class="page-mark{extra_cls}" id="p{pnum}" '
                        f'data-page="{pnum}" title="{html.escape(tip)}">{html.escape(disp)}</span>'
                    )
                pos = m.end()
                continue

        if text.startswith("[?]", pos):
            pnum = current_page_ref[0]
            notes = page_notes.get(pnum, [])
            tip = f"p.{pnum} 판독 확인 필요"
            if notes:
                tip += ": " + " / ".join(notes)
            out.append(
                f'<span class="uncertain-mark" data-page="{pnum}" '
                f'title="{html.escape(tip)}">[?]</span>'
            )
            pos += 3
            prev_non_space = "?"
            continue

        m_ids = IDS_SEQ.match(text, pos)
        if m_ids:
            seq = m_ids.group(0)
            pnum = current_page_ref[0]
            notes = [n for n in page_notes.get(pnum, []) if seq in n]
            tip = f"조합자 표기({seq})"
            if notes:
                tip += ": " + " / ".join(notes)
            if pos in reading_map:
                _, r_syl = reading_map[pos]
                out.append(
                    f'<ruby class="ids-glyph" title="{html.escape(tip)}">'
                    f'{html.escape(seq)}<rt>{r_syl}</rt></ruby>'
                )
            else:
                out.append(f'<span class="ids-glyph" title="{html.escape(tip)}">{html.escape(seq)}</span>')
            pos = m_ids.end()
            prev_non_space = seq[-1]
            continue

        ch = text[pos]
        if ch in BRACKET_OPEN:
            if jegu_items and ch == "【" and prev_non_space in ("〉", "｝"):
                out.append('<span class="jegu-sep"></span>')
            cls, brk_cls = BRACKET_OPEN[ch]
            stack.append(ch)
            out.append(f'<span class="{cls}"><span class="{brk_cls}">{ch}</span>')
            prev_non_space = ch
        elif ch in BRACKET_CLOSE:
            expected, brk_cls = BRACKET_CLOSE[ch]
            if stack and stack[-1] == expected:
                stack.pop()
                out.append(f'<span class="{brk_cls}">{ch}</span></span>')
            else:
                out.append(html.escape(ch))
            prev_non_space = ch
        elif ch == "○":
            out.append('<span class="circle-mark">○</span>')
            prev_non_space = ch
        elif ch == "\u3000":
            out.append('<span class="fw-space">&emsp;</span>')
        elif ch == "□":
            out.append('<span class="unreadable-mark" title="판독 불가">□</span>')
            prev_non_space = ch
        else:
            if pos in reading_map:
                _, r_syl = reading_map[pos]
                out.append(f"<ruby>{html.escape(ch)}<rt>{r_syl}</rt></ruby>")
            else:
                out.append(html.escape(ch))
            if not ch.isspace():
                prev_non_space = ch
        pos += 1

    # 문단 끝에서 아직 닫히지 않은 태그는 모두 닫아 유효한 HTML 유지
    for _ in reversed(stack):
        out.append("</span>")

    return "".join(out)


RUBY_TOKEN = re.compile(r"<ruby>([^<]*)<rt>([^<]*)</rt></ruby>|<rt>[^<]*</rt>|<[^>]*>|[^<]+")


def defer_ruby(unit_html):
    """본문 한 덩어리의 한자음 루비를 걷어 내고, 독음을 덩어리의 data-r 속성에 모은다.

    data-r 의 n번째 글자는 덩어리 본문(rt 제외) n번째 글자의 독음이고 '.' 은 독음 없음이다.
    브라우저는 화면 근처의 덩어리에만 루비를 다시 만든다(hydrate).
    """
    out, readings = [], []
    for m in RUBY_TOKEN.finditer(unit_html):
        token, base, reading = m.group(0), m.group(1), m.group(2)
        if base is not None and len(html.unescape(base)) == 1 and len(reading) == 1:
            out.append(base)
            readings.append(reading)
            continue
        out.append(token)
        if base is not None:  # 한 글자씩이 아닌 루비는 그대로 둔다
            readings.append("." * len(html.unescape(base)))
        elif not token.startswith("<"):
            readings.append("." * len(html.unescape(token)))
    data = "".join(readings).rstrip(".")
    result = "".join(out)
    if not data:
        return result
    end = result.index(">")
    return f'{result[:end]} data-r="{data}"{result[end:]}'


def plain_heading_text(raw):
    """목차 표시용으로 머리말에서 쪽 토큰을 제거하고 공백을 정리한다."""
    s = strip_page_tokens(raw)
    return s


def build_volume_html(vol_key, pages, page_meta, page_notes, translations=None):
    """권 하나의 본문 HTML과 목차 항목 리스트를 생성한다."""
    translations = translations or {}
    vol = build.VOLUMES[vol_key]
    meta = VOLUME_META[vol_key]
    start, end = vol["body"]
    numbers = [n for n in range(start, end + 1) if n in pages]
    assembled = assemble_volume_tokens(pages, numbers, page_meta)

    raw_blocks = [b.strip() for b in re.split(r"\n{2,}", assembled) if b.strip()]

    # 단독 PAGE 토큰 블록은 바로 다음 내용 블록 앞에 붙인다(빈 면 토큰은 독립 유지)
    blocks = []
    pending_tokens = ""
    for b in raw_blocks:
        stripped = strip_page_tokens(b)
        if not stripped and "|EMPTY\x01" not in b:
            pending_tokens += b
        else:
            blocks.append(pending_tokens + b)
            pending_tokens = ""
    if pending_tokens:
        if blocks:
            blocks[-1] += pending_tokens
        else:
            blocks.append(pending_tokens)

    toc = []  # [{"level": 2|3, "id": ..., "title": ..., "page": ..., "translated": bool}]
    html_parts = []
    stack = []
    ko_stack = []
    current_page_ref = [numbers[0] if numbers else start]

    html_parts.append(
        f'<section class="volume-section" id="{meta["id"]}" data-vol="{vol_key}">'
    )

    idx = 0
    sec_counter = 0
    n_blocks = len(blocks)
    ko_queue = []
    sec_has_ko = False
    sec_name = ""

    def pop_ko(context=""):
        if ko_queue:
            return ko_queue.pop(0)
        if sec_has_ko:
            raise ValueError(f"{vol_key} ({sec_name}): 번역 블록이 부족함 ({context[:30]})")
        return None

    def update_page_peek(raw_str):
        m = PAGE_TOKEN.search(raw_str)
        if m:
            return int(m.group(1))
        return current_page_ref[0]

    while idx < n_blocks:
        raw = blocks[idx]
        clean = strip_page_tokens(raw)

        # 빈 면 단독 블록
        if not clean:
            inner = render_inline(raw, stack, page_notes, current_page_ref)
            html_parts.append(f'<div class="empty-page-block">{inner}</div>')
            idx += 1
            continue

        # 권두 표제 (# 四禮便覽卷之三 + 喪禮一)
        if clean.startswith("# "):
            title_raw = re.sub(r"^#\s+", "", raw, count=1)
            m_lead = re.match(r"^((?:\x00PAGE:[^\x01]+\x01)+)\s*#\s+(.*)$", raw, re.S)
            if m_lead:
                title_raw = m_lead.group(1) + m_lead.group(2)
            title_html = render_inline(title_raw, stack, page_notes, current_page_ref)
            sub_html = ""
            if idx + 1 < n_blocks and re.match(r"^喪禮[一二三四]$", strip_page_tokens(blocks[idx + 1])):
                sub_html = render_inline(blocks[idx + 1], stack, page_notes, current_page_ref)
                idx += 1
            html_parts.append(
                f'<header class="vol-header">'
                f'<div class="vol-kicker">{meta["short"]} · {meta["desc"]}</div>'
                f'<h1 class="vol-title">{title_html}</h1>'
                + (f'<div class="vol-subtitle">{sub_html}</div>' if sub_html else "")
                + f"</header>"
            )
            idx += 1
            continue

        # 대항목 (## ...)
        if clean.startswith("## "):
            if ko_queue:
                raise ValueError(f"{vol_key}: 이전 대항목 번역 블록이 {len(ko_queue)}개 남음: {ko_queue[0][:30]}")
            sec_counter += 1
            sec_id = f'{meta["id"]}-s{sec_counter}'
            m_lead = re.match(r"^((?:\x00PAGE:[^\x01]+\x01)*)\s*##\s+(.*)$", raw, re.S)
            h_raw = (m_lead.group(1) + m_lead.group(2)) if m_lead else raw[3:]
            p_at = update_page_peek(raw)
            h_html = render_inline(h_raw, stack, page_notes, current_page_ref)
            toc_title = re.sub(r"^##\s+", "", clean)
            sec_name = toc_title

            ko_queue = list(translations.get((vol_key, toc_title), []))
            sec_has_ko = bool(ko_queue)
            ko_raw = pop_ko(clean)
            ko_html = ""
            if ko_raw:
                ko_clean = re.sub(r"^##\s+", "", ko_raw)
                ko_html = f'<span class="ko-heading">{render_inline(ko_clean, ko_stack, {}, [p_at], with_ruby=False)}</span>'

            toc.append({"level": 2, "id": sec_id, "title": toc_title, "page": p_at, "translated": bool(ko_raw)})
            b_cls = " has-ko" if ko_html else ""
            html_parts.append(
                f'<h2 class="sec-h2{b_cls}" id="{sec_id}"><span class="ori-heading">{h_html}</span>{ko_html}</h2>'
            )
            idx += 1
            continue

        # 세부 절차 조목 (### ...)
        if clean.startswith("### "):
            sec_counter += 1
            sec_id = f'{meta["id"]}-s{sec_counter}'
            m_lead = re.match(r"^((?:\x00PAGE:[^\x01]+\x01)*)\s*###\s+(.*)$", raw, re.S)
            h_raw = (m_lead.group(1) + m_lead.group(2)) if m_lead else raw[4:]
            p_at = update_page_peek(raw)
            h_html = render_inline(h_raw, stack, page_notes, current_page_ref)
            toc_title = re.sub(r"^###\s+", "", clean)
            is_supp_h3 = toc_title.startswith("｛")
            h3_cls = "sec-h3 supp-heading" if is_supp_h3 else "sec-h3"

            ko_raw = pop_ko()
            ko_html = ""
            if ko_raw:
                ko_clean = re.sub(r"^###\s+", "", ko_raw)
                ko_html = f'<span class="ko-heading">{render_inline(ko_clean, ko_stack, {}, [p_at], with_ruby=False)}</span>'
                h3_cls += " has-ko"

            toc.append({"level": 3, "id": sec_id, "title": toc_title, "page": p_at, "translated": bool(ko_raw)})
            html_parts.append(
                f'<h3 class="{h3_cls}" id="{sec_id}"><span class="ori-heading">{h_html}</span>{ko_html}</h3>'
            )
            idx += 1
            continue

        # 권말 미제 (四禮便覽卷之三 등)
        if COLOPHON.match(clean):
            inner = render_inline(raw, stack, page_notes, current_page_ref)
            html_parts.append(f'<p class="vol-colophon">{inner}</p>')
            idx += 1
            continue

        # 선유설·안설 (> ...)
        if clean.startswith(">"):
            ref_paras = []
            while idx < n_blocks and strip_page_tokens(blocks[idx]).startswith(">"):
                b_raw = blocks[idx]
                ko_b_raw = pop_ko(strip_page_tokens(b_raw))
                sub_paras = re.split(r"\n>\s*\n", b_raw)
                ko_sub_paras = re.split(r"\n>\s*\n", ko_b_raw) if ko_b_raw else []
                if ko_b_raw and len(ko_sub_paras) != len(sub_paras):
                    raise ValueError(
                        f"{vol_key} ({sec_name}): 선유설·안설 세부 문단 수 불일치 "
                        f"(원문 {len(sub_paras)} != 번역 {len(ko_sub_paras)})"
                    )
                for s_i, sp in enumerate(sub_paras):
                    sp_clean = re.sub(r"^((?:\x00PAGE:[^\x01]+\x01)*)\s*>\s?", r"\1", sp.strip())
                    sp_clean = re.sub(r"\n>\s?", "", sp_clean)
                    is_an = "〔按〕" in strip_page_tokens(sp_clean)[:12]
                    p_cls = "ref-para an-para" if is_an else "ref-para"
                    ori_sp = render_inline(sp_clean, stack, page_notes, current_page_ref)
                    if s_i < len(ko_sub_paras):
                        ko_sp_clean = re.sub(r"^>\s?", "", ko_sub_paras[s_i].strip())
                        ko_sp_clean = re.sub(r"\n>\s?", "", ko_sp_clean)
                        ko_sp = render_inline(ko_sp_clean, ko_stack, {}, current_page_ref, with_ruby=False)
                        ref_paras.append(
                            f'<div class="{p_cls} has-ko">'
                            f'<p class="ori-subpara">{ori_sp}</p>'
                            f'<p class="ko-subpara">{ko_sp}</p>'
                            f'</div>'
                        )
                    else:
                        ref_paras.append(f'<p class="{p_cls}">{ori_sp}</p>')
                idx += 1
            html_parts.append('<div class="ref-box">' + "".join(ref_paras) + "</div>")
            continue

        # 제구 블록 (【諸具】...)
        if clean.startswith("【諸具】"):
            head_html = render_inline(raw, stack, page_notes, current_page_ref)
            ko_head_raw = pop_ko()
            if ko_head_raw:
                ko_head = render_inline(ko_head_raw, ko_stack, {}, current_page_ref, with_ruby=False)
                jegu_parts = [
                    f'<div class="jegu-head has-ko"><span class="ori-span">{head_html}</span>'
                    f'<span class="ko-span">{ko_head}</span></div>'
                ]
            else:
                jegu_parts = [f'<div class="jegu-head">{head_html}</div>']
            idx += 1
            while idx < n_blocks:
                nxt_clean = strip_page_tokens(blocks[idx])
                if (
                    nxt_clean.startswith("#")
                    or nxt_clean.startswith(">")
                    or nxt_clean.startswith("【諸具】")
                    or FORM_START.match(nxt_clean)
                    or COLOPHON.match(nxt_clean)
                ):
                    break
                if nxt_clean.startswith("【") or (nxt_clean.startswith("〈") and nxt_clean.endswith("〉")) or stack:
                    item_html = render_inline(
                        blocks[idx], stack, page_notes, current_page_ref, jegu_items=True
                    )
                    ko_item_raw = pop_ko()
                    if ko_item_raw:
                        ko_item_html = render_inline(
                            ko_item_raw, ko_stack, {}, current_page_ref, jegu_items=True, with_ruby=False
                        )
                        jegu_parts.append(
                            f'<div class="jegu-body has-ko">'
                            f'<div class="ori-block">{item_html}</div>'
                            f'<div class="ko-block">{ko_item_html}</div>'
                            f'</div>'
                        )
                    else:
                        jegu_parts.append(f'<div class="jegu-body">{item_html}</div>')
                    idx += 1
                else:
                    break
            html_parts.append('<div class="jegu-box">' + "".join(jegu_parts) + "</div>")
            continue

        # 서식 블록 (【...式】...)
        if FORM_START.match(clean):
            head_html = render_inline(raw, stack, page_notes, current_page_ref)
            ko_head_raw = pop_ko()
            if ko_head_raw:
                ko_head = render_inline(ko_head_raw, ko_stack, {}, current_page_ref, with_ruby=False)
                head_div = (
                    f'<div class="form-head has-ko"><span class="ori-span">{head_html}</span>'
                    f'<span class="ko-span">{ko_head}</span></div>'
                )
            else:
                head_div = f'<div class="form-head">{head_html}</div>'
            form_parts = [head_div, '<div class="form-body">']
            idx += 1
            while idx < n_blocks:
                nxt_clean = strip_page_tokens(blocks[idx])
                if (
                    nxt_clean.startswith("#")
                    or nxt_clean.startswith(">")
                    or nxt_clean.startswith("【諸具】")
                    or FORM_START.match(nxt_clean)
                    or COLOPHON.match(nxt_clean)
                ):
                    break
                line_html = render_inline(blocks[idx], stack, page_notes, current_page_ref)
                ko_line_raw = pop_ko()
                if ko_line_raw:
                    ko_line_html = render_inline(ko_line_raw, ko_stack, {}, current_page_ref, with_ruby=False)
                    form_parts.append(
                        f'<div class="form-line-pair has-ko">'
                        f'<p class="form-line ori-block">{line_html}</p>'
                        f'<p class="form-line ko-block">{ko_line_html}</p>'
                        f'</div>'
                    )
                else:
                    form_parts.append(f'<p class="form-line">{line_html}</p>')
                idx += 1
            form_parts.append("</div>")
            html_parts.append('<div class="form-box">' + "".join(form_parts) + "</div>")
            continue

        # 단독 두 줄 세주 문단
        if clean.startswith("〈") and not clean.startswith("〈附〉"):
            inner = render_inline(raw, stack, page_notes, current_page_ref)
            ko_raw = pop_ko()
            if ko_raw:
                ko_inner = render_inline(ko_raw, ko_stack, {}, current_page_ref, with_ruby=False)
                html_parts.append(
                    f'<div class="para-pair standalone-note-para has-ko">'
                    f'<p class="body-para ori-block">{inner}</p>'
                    f'<p class="body-para ko-block">{ko_inner}</p>'
                    f'</div>'
                )
            else:
                html_parts.append(f'<p class="body-para standalone-note-para">{inner}</p>')
            idx += 1
            continue

        # 일반 본문 문단 (家禮 本註 + 인라인 증보·세주)
        inner = render_inline(raw, stack, page_notes, current_page_ref)
        ko_raw = pop_ko()
        if ko_raw:
            ko_inner = render_inline(ko_raw, ko_stack, {}, current_page_ref, with_ruby=False)
            html_parts.append(
                f'<div class="para-pair has-ko">'
                f'<p class="body-para ori-block">{inner}</p>'
                f'<p class="body-para ko-block">{ko_inner}</p>'
                f'</div>'
            )
        else:
            html_parts.append(f'<p class="body-para">{inner}</p>')
        idx += 1

    if ko_queue:
        raise ValueError(f"{vol_key} ({sec_name}): 마지막 대항목 번역 블록이 {len(ko_queue)}개 남음: {ko_queue[0][:30]}")
    if stack:
        raise ValueError(f"{vol_key}: 원문 괄호 미닫힘: {stack}")
    if ko_stack:
        raise ValueError(f"{vol_key}: 번역 괄호 미닫힘: {ko_stack}")

    html_parts.append("</section>")
    return "\n".join(defer_ruby(part) for part in html_parts), toc


CSS_STYLES = """
:root {
  --bg-app: #f4efe4;
  --bg-sidebar: #ebe3d3;
  --bg-paper: #fcfaf5;
  --bg-subtle: #f5efe2;
  --bg-form: #f8f4ea;
  --bg-jegu: #f6f2e8;
  --bg-ref: #f4efe4;
  --ink-main: #1d1a16;
  --ink-note: #4e463c;
  --ink-supp: #1f3f54;
  --ink-muted: #7b7060;
  --accent: #8c2d19;
  --accent-soft: #f2e1dc;
  --border: #dcd2c0;
  --border-strong: #bfb199;
  --cartouche-bg: #24201b;
  --cartouche-fg: #fbf8f1;
  --round-bg: #efe7d6;
  --round-border: #9c8c73;
  --round-fg: #4b3d2a;
  --mark-bg: #ffe380;
  --font-Scale: 1;
}

body[data-theme="white"] {
  --bg-app: #f3f4f6;
  --bg-sidebar: #e9ecef;
  --bg-paper: #ffffff;
  --bg-subtle: #f8f9fa;
  --bg-form: #f8fafc;
  --bg-jegu: #f7f8fa;
  --bg-ref: #f4f6f8;
  --ink-main: #111827;
  --ink-note: #374151;
  --ink-supp: #1d4ed8;
  --ink-muted: #6b7280;
  --accent: #991b1b;
  --accent-soft: #fee2e2;
  --border: #e5e7eb;
  --border-strong: #cbd5e1;
  --cartouche-bg: #1f2937;
  --cartouche-fg: #f9fafb;
  --round-bg: #eef2f6;
  --round-border: #94a3b8;
  --round-fg: #334155;
}

body[data-theme="dark"] {
  --bg-app: #141413;
  --bg-sidebar: #1c1b19;
  --bg-paper: #21201d;
  --bg-subtle: #292723;
  --bg-form: #262420;
  --bg-jegu: #25231f;
  --bg-ref: #262420;
  --ink-main: #eae4d8;
  --ink-note: #bfb6a6;
  --ink-supp: #93c5fd;
  --ink-muted: #8e8577;
  --accent: #e06d53;
  --accent-soft: #3b2621;
  --border: #36332d;
  --border-strong: #4d483f;
  --cartouche-bg: #e5dec9;
  --cartouche-fg: #1b1916;
  --round-bg: #302d27;
  --round-border: #6e6658;
  --round-fg: #dfd7c7;
  --mark-bg: #854d0e;
}

* { box-sizing: border-box; }

body {
  margin: 0;
  background: var(--bg-app);
  color: var(--ink-main);
  font-family: "Noto Serif CJK KR", "Noto Serif KR", "Source Han Serif K", "KoPubWorldBatang", "NanumMyeongjo", "Batang", "PMingLiU-ExtB", "MingLiU-ExtB", "SimSun-ExtB", serif;
  font-size: calc(18px * var(--font-scale, 1));
  line-height: 1.92;
  word-break: keep-all;
  overflow-wrap: anywhere;
}

/* 상단 고정 헤더 */
.topbar {
  position: sticky;
  top: 0;
  z-index: 100;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 0.6rem 1rem;
  padding: 0.55rem 1.1rem;
  background: var(--bg-sidebar);
  border-bottom: 1px solid var(--border-strong);
  font-family: "Pretendard", "Malgun Gothic", "Apple SD Gothic Neo", sans-serif;
  font-size: 13.5px;
  line-height: 1.4;
}

.brand-group {
  display: flex;
  align-items: center;
  gap: 0.65rem;
}

.menu-btn {
  display: none;
  background: var(--bg-paper);
  border: 1px solid var(--border-strong);
  color: var(--ink-main);
  border-radius: 5px;
  padding: 0.3rem 0.55rem;
  cursor: pointer;
  font-size: 14px;
}

.brand-seal {
  background: var(--accent);
  color: #fff8f5;
  font-family: "Noto Serif KR", "Batang", serif;
  font-weight: 700;
  padding: 0.2rem 0.5rem;
  border-radius: 3px;
  letter-spacing: 0.06em;
  font-size: 14px;
}

.brand-title {
  font-weight: 700;
  font-size: 15px;
  color: var(--ink-main);
}

.brand-sub {
  color: var(--ink-muted);
  font-size: 12.5px;
}

.vol-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 0.28rem;
}

.vol-tab {
  border: 1px solid var(--border-strong);
  background: var(--bg-paper);
  color: var(--ink-main);
  padding: 0.28rem 0.65rem;
  border-radius: 5px;
  cursor: pointer;
  font-size: 13px;
  font-weight: 500;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
}

.vol-tab:hover { border-color: var(--accent); }

.vol-tab.active {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}

.toolbar-controls {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.4rem;
}

.search-wrap {
  display: flex;
  align-items: center;
  background: var(--bg-paper);
  border: 1px solid var(--border-strong);
  border-radius: 5px;
  padding: 0.15rem 0.45rem;
  gap: 0.3rem;
}

.search-wrap input {
  border: none;
  background: transparent;
  color: var(--ink-main);
  font-size: 13px;
  width: 155px;
  outline: none;
}

.search-count {
  color: var(--ink-muted);
  font-size: 11.5px;
  white-space: nowrap;
}

.icon-btn, .opt-btn {
  border: 1px solid var(--border-strong);
  background: var(--bg-paper);
  color: var(--ink-main);
  border-radius: 4px;
  padding: 0.24rem 0.5rem;
  font-size: 12px;
  cursor: pointer;
  white-space: nowrap;
}

.icon-btn:hover, .opt-btn:hover { border-color: var(--accent); }

.opt-btn.active {
  background: var(--accent-soft);
  border-color: var(--accent);
  color: var(--accent);
  font-weight: 600;
}

/* 전체 레이아웃 */
.layout {
  display: grid;
  grid-template-columns: 300px minmax(0, 1fr);
  min-height: calc(100vh - 52px);
}

/* 좌측 목차 사이드바 */
.sidebar {
  position: sticky;
  top: 52px;
  height: calc(100vh - 52px);
  overflow-y: auto;
  background: var(--bg-sidebar);
  border-right: 1px solid var(--border);
  padding: 0.9rem 0.85rem 2rem;
  font-family: "Pretendard", "Malgun Gothic", sans-serif;
  font-size: 13.5px;
}

.toc-filter {
  width: 100%;
  padding: 0.38rem 0.55rem;
  margin-bottom: 0.7rem;
  border: 1px solid var(--border-strong);
  border-radius: 5px;
  background: var(--bg-paper);
  color: var(--ink-main);
  font-size: 12.5px;
}

.toc-vol-group { margin-bottom: 0.85rem; }

.toc-vol-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  background: var(--bg-subtle);
  border: 1px solid var(--border);
  color: var(--ink-main);
  padding: 0.42rem 0.6rem;
  border-radius: 5px;
  font-weight: 700;
  font-size: 13.5px;
  cursor: pointer;
  text-align: left;
}

.toc-vol-list {
  list-style: none;
  margin: 0.35rem 0 0;
  padding: 0 0 0 0.25rem;
}

.toc-vol-group.collapsed .toc-vol-list { display: none; }

.toc-item { margin: 0.12rem 0; }

.toc-item.lvl-2 > a {
  font-weight: 700;
  color: var(--ink-main);
  margin-top: 0.35rem;
  font-family: "Noto Serif KR", "Batang", serif;
  font-size: 14px;
}

.toc-item.lvl-3 > a {
  padding-left: 0.85rem;
  color: var(--ink-note);
  font-family: "Noto Serif KR", "Batang", serif;
  font-size: 13px;
}

.toc-link {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 0.4rem;
  text-decoration: none;
  padding: 0.2rem 0.45rem;
  border-radius: 4px;
  line-height: 1.45;
}

.toc-link:hover { background: var(--bg-paper); }

.toc-link.active {
  background: var(--accent-soft);
  color: var(--accent) !important;
  font-weight: 700;
}

.toc-pnum {
  font-family: "Pretendard", sans-serif;
  font-size: 11px;
  color: var(--ink-muted);
  flex-shrink: 0;
}

/* 본문 열람 영역 */
.reader-main {
  padding: 1.8rem 1.4rem 5rem;
}

.paper-sheet {
  max-width: 880px;
  margin: 0 auto;
  background: var(--bg-paper);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 2.6rem 3rem;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.035);
}

.volume-section {
  padding-bottom: 3rem;
  margin-bottom: 3rem;
  border-bottom: 2px double var(--border-strong);
}

.volume-section:last-of-type { border-bottom: none; }
.volume-section.hidden-vol { display: none; }

/* 화면 밖 본문의 배치를 미뤄 열기와 권 전환을 빠르게 한다. 제목은 이동 대상이라 제외한다. */
.volume-section:not(.guide-section) > :not(h2, h3, header) {
  content-visibility: auto;
  contain-intrinsic-size: auto 150px;
}

.vol-header {
  text-align: center;
  padding: 1.2rem 1rem 1.6rem;
  margin-bottom: 2rem;
  border-top: 3px double var(--border-strong);
  border-bottom: 3px double var(--border-strong);
  background: var(--bg-subtle);
}

.vol-kicker {
  font-family: "Pretendard", "Malgun Gothic", sans-serif;
  font-size: 0.72em;
  color: var(--accent);
  letter-spacing: 0.08em;
  font-weight: 600;
  margin-bottom: 0.35rem;
}

.vol-title {
  margin: 0;
  font-size: 1.75em;
  letter-spacing: 0.12em;
  font-weight: 700;
}

.vol-subtitle {
  margin-top: 0.35rem;
  font-size: 1.15em;
  letter-spacing: 0.25em;
  color: var(--ink-note);
}

/* 대항목 · 절차 조목 제목 */
.sec-h2 {
  margin: 2.6rem 0 1.1rem;
  padding: 0.45rem 0.85rem;
  background: var(--bg-subtle);
  border-left: 5px solid var(--accent);
  font-size: 1.38em;
  letter-spacing: 0.08em;
  line-height: 1.5;
}

.sec-h3 {
  margin: 2rem 0 0.75rem;
  padding-bottom: 0.3rem;
  border-bottom: 1px solid var(--border);
  font-size: 1.16em;
  font-weight: 700;
  line-height: 1.6;
}

.sec-h3::before {
  content: "▪";
  color: var(--accent);
  margin-right: 0.45rem;
  font-size: 0.85em;
}

/* 문단 및 층위 스타일 */
.body-para {
  margin: 0.75rem 0;
  text-align: justify;
}

.standalone-note-para {
  padding-left: 0.8rem;
}

/* 보충·변통문 ｛ ｝ */
.layer-supp {
  color: var(--ink-supp);
  font-size: 0.95em;
  border-bottom: 1px dashed rgba(31, 63, 84, 0.32);
}

/* 두 줄 세주 〈 〉 */
.layer-note {
  color: var(--ink-note);
  font-size: 0.87em;
  line-height: 1.8;
}

/* 선유설 · 저자 안설 (> 〈 〉) */
.ref-box {
  margin: 0.95rem 0 1.15rem 0.9rem;
  padding: 0.75rem 1.05rem;
  background: var(--bg-ref);
  border-left: 3px solid var(--border-strong);
  border-radius: 0 4px 4px 0;
}

.ref-para {
  margin: 0.45rem 0;
  text-align: justify;
}

.ref-para:first-child { margin-top: 0; }
.ref-para:last-child { margin-bottom: 0; }

.ref-para.an-para {
  border-top: 1px dotted var(--border-strong);
  padding-top: 0.5rem;
}

.ref-para.an-para:first-child {
  border-top: none;
  padding-top: 0;
}

/* 판각 테두리 기호: 【 흑지백자 】 / 〔 둥근 출전 테두리 〕 */
.cartouche-black {
  display: inline-block;
  background: var(--cartouche-bg);
  color: var(--cartouche-fg);
  padding: 0.04em 0.42em;
  margin: 0 0.14em 0 0.06em;
  border-radius: 3px;
  font-weight: 600;
  font-size: 0.92em;
  line-height: 1.45;
  vertical-align: baseline;
}

.cartouche-round {
  display: inline-block;
  background: var(--round-bg);
  color: var(--round-fg);
  border: 1px solid var(--round-border);
  padding: 0.01em 0.44em;
  margin: 0 0.12em;
  border-radius: 999px;
  font-size: 0.88em;
  line-height: 1.42;
  vertical-align: baseline;
}

/* 【 〕 안의 원문 괄호 문자는 기본적으로 시각 배지로 대체하고 토글 시 표시 */
.brk-c { display: none; }
body.show-brackets .brk-c { display: inline; opacity: 0.65; }

.brk { opacity: 0.55; font-weight: 400; }
body.hide-brackets .brk { display: none; }

/* 단락 구분 ○ */
.circle-mark {
  color: var(--accent);
  margin: 0 0.32em;
  font-weight: 700;
}

/* 제구(諸具) 카드 */
.jegu-box {
  margin: 1rem 0 1.2rem;
  padding: 0.8rem 1.1rem;
  background: var(--bg-jegu);
  border: 1px solid var(--border);
  border-radius: 5px;
}

.jegu-head {
  margin-bottom: 0.4rem;
  padding-bottom: 0.3rem;
  border-bottom: 1px dashed var(--border-strong);
  font-weight: 600;
}

.jegu-body {
  margin-top: 0.35rem;
  line-height: 1.88;
}

.jegu-sep { display: inline; }
body.jegu-split .jegu-sep {
  display: block;
  height: 0.32rem;
}

/* 서식(祝文式 · 告辭式 · 狀式 등) 카드 */
.form-box {
  margin: 1.05rem 0 1.3rem;
  background: var(--bg-form);
  border: 1px solid var(--border-strong);
  border-radius: 5px;
  overflow: hidden;
}

.form-head {
  padding: 0.5rem 1rem;
  background: var(--bg-subtle);
  border-bottom: 1px solid var(--border);
  font-weight: 600;
}

.form-body {
  padding: 0.85rem 1.25rem;
}

.form-line {
  margin: 0.32rem 0;
  padding-left: 1em;
  text-indent: -1em;
  line-height: 1.85;
}

/* 쪽 번호 · 장차 배지 */
.page-mark {
  display: inline-block;
  font-family: "Pretendard", "Malgun Gothic", sans-serif;
  font-size: 11px;
  line-height: 1.3;
  color: var(--ink-muted);
  background: var(--bg-subtle);
  border: 1px solid var(--border);
  border-radius: 3px;
  padding: 0.05rem 0.35rem;
  margin: 0 0.28rem;
  vertical-align: middle;
  user-select: none;
  cursor: help;
}

.page-mark.has-note {
  border-color: var(--accent);
  color: var(--accent);
}

.page-mark.flash-target {
  background: var(--accent) !important;
  color: #fff !important;
  transform: scale(1.12);
  transition: transform 0.25s;
}

body.hide-pages .page-mark { display: none; }

.empty-page-block {
  margin: 0.8rem 0;
  text-align: center;
}

/* 확인 표시 [?], 조합자, 공백 */
.uncertain-mark {
  display: inline-block;
  font-family: "Pretendard", sans-serif;
  font-size: 0.72em;
  font-weight: 700;
  color: var(--accent);
  background: var(--accent-soft);
  border: 1px solid var(--accent);
  border-radius: 3px;
  padding: 0 0.25em;
  margin: 0 0.1em;
  cursor: help;
  vertical-align: super;
}

.ids-glyph {
  border-bottom: 1px dotted var(--accent);
  cursor: help;
}

.fw-space { display: inline-block; min-width: 1em; }

.vol-colophon {
  margin-top: 2.2rem;
  text-align: right;
  color: var(--ink-muted);
  font-size: 0.95em;
  letter-spacing: 0.15em;
}

/* 본문만 읽기 모드 (세주·선유설·제구 접기) */
body.core-only .layer-note,
body.core-only .ref-box,
body.core-only .jegu-box,
body.core-only .standalone-note-para {
  display: none;
}

/* 원문·번역 대역 스타일 및 보기 모드 */
.ko-heading {
  display: block;
  font-family: "Pretendard", "Malgun Gothic", "Apple SD Gothic Neo", sans-serif;
  font-size: 0.78em;
  font-weight: 600;
  color: var(--ink-note);
  margin-top: 0.18rem;
  letter-spacing: -0.01em;
}

.para-pair {
  margin: 0.85rem 0 1.15rem;
}

.para-pair > .ori-block {
  margin: 0 0 0.35rem;
}

.ko-block, .ko-subpara {
  margin: 0.3rem 0 0;
  padding: 0.55rem 0.85rem;
  background: var(--bg-subtle);
  border-left: 3px solid var(--accent);
  border-radius: 0 4px 4px 0;
  font-family: "Pretendard", "Malgun Gothic", "Apple SD Gothic Neo", sans-serif;
  font-size: 0.88em;
  line-height: 1.78;
  color: var(--ink-main);
  text-align: justify;
}

.ori-subpara { margin: 0; }
.ref-para.has-ko { margin: 0.55rem 0; }
.ref-para.has-ko .ko-subpara {
  background: var(--bg-paper);
  margin-top: 0.35rem;
}

.jegu-head.has-ko, .form-head.has-ko {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 0.5rem;
}

.jegu-head .ko-span, .form-head .ko-span {
  font-family: "Pretendard", "Malgun Gothic", sans-serif;
  font-size: 0.86em;
  font-weight: 500;
  color: var(--ink-note);
}

.jegu-body.has-ko > .ko-block {
  margin-top: 0.5rem;
}

.form-line-pair {
  margin: 0.4rem 0;
}

.form-line-pair > .ko-block {
  padding: 0.32rem 0.7rem 0.32rem 1.4rem;
  margin: 0.18rem 0 0.45rem;
}

.toc-ko-badge {
  display: inline-block;
  font-family: "Pretendard", sans-serif;
  font-size: 10px;
  font-weight: 700;
  color: var(--accent);
  background: var(--accent-soft);
  border-radius: 3px;
  padding: 0 0.28rem;
  margin-left: 0.3rem;
  vertical-align: middle;
}

/* 번역 보기 모드: 원문만(ori-only) / 번역만(ko-only, 번역 있는 블록에 한함) */
body[data-trans="ori"] .ko-heading,
body[data-trans="ori"] .ko-block,
body[data-trans="ori"] .ko-subpara,
body[data-trans="ori"] .ko-span {
  display: none !important;
}

body[data-trans="ko"] .has-ko > .ori-heading,
body[data-trans="ko"] .has-ko > .ori-block,
body[data-trans="ko"] .has-ko > .ori-subpara,
body[data-trans="ko"] .has-ko > .ori-span {
  display: none !important;
}

body[data-trans="ko"] .has-ko > .ko-heading {
  font-size: 0.95em;
  color: var(--ink-main);
  margin-top: 0;
}

/* 한자 독음(루비) 스타일 및 보기 모드 전환 */
ruby {
  ruby-position: over;
  ruby-align: center;
}

rt {
  font-family: "Pretendard", "Malgun Gothic", "Apple SD Gothic Neo", sans-serif;
  font-size: 0.5em;
  font-weight: 500;
  color: var(--accent);
  line-height: 1.05;
  letter-spacing: 0;
  user-select: none;
  -webkit-user-select: none;
}

.layer-supp rt {
  color: var(--ink-supp);
}

.layer-note rt {
  font-size: 0.56em;
  color: var(--ink-muted);
}

.cartouche-black rt {
  color: #ffd9cc;
  font-size: 0.52em;
}

body[data-theme="dark"] .cartouche-black rt {
  color: #7a2413;
}

.cartouche-round rt {
  color: var(--accent);
  font-size: 0.52em;
}

body:not([data-ruby="off"]) .body-para:not(.ko-block),
body:not([data-ruby="off"]) .ori-subpara,
body:not([data-ruby="off"]) p.ref-para,
body:not([data-ruby="off"]) .jegu-body > .ori-block,
body:not([data-ruby="off"]) .jegu-body:not(.has-ko),
body:not([data-ruby="off"]) .form-line.ori-block,
body:not([data-ruby="off"]) .form-body > p.form-line {
  line-height: 2.3;
}

body:not([data-ruby="off"]) .sec-h2,
body:not([data-ruby="off"]) .sec-h3 {
  line-height: 1.85;
}

body[data-ruby="off"] rt {
  display: none !important;
}

body[data-ruby="hover"] rt {
  visibility: hidden;
  opacity: 0;
  transition: opacity 0.12s ease;
}

body[data-ruby="hover"] ruby:hover rt,
body[data-ruby="hover"] .sec-h2:hover rt,
body[data-ruby="hover"] .sec-h3:hover rt,
body[data-ruby="hover"] p:hover rt,
body[data-ruby="hover"] .jegu-box:hover rt,
body[data-ruby="hover"] .form-box:hover rt {
  visibility: visible;
  opacity: 1;
}

/* 검색 하이라이트 */
mark.search-hit {
  background: var(--mark-bg);
  color: inherit;
  padding: 0 1px;
  border-radius: 2px;
}

mark.search-hit.current-hit {
  outline: 2px solid var(--accent);
}

/* 범례 · 교감 섹션 */
.guide-section {
  font-family: "Pretendard", "Malgun Gothic", sans-serif;
  font-size: 0.88em;
  line-height: 1.7;
}

.guide-section h2 {
  font-family: "Noto Serif KR", serif;
}

.guide-table {
  width: 100%;
  border-collapse: collapse;
  margin: 1rem 0 1.8rem;
  font-size: 0.95em;
}

.guide-table th, .guide-table td {
  border: 1px solid var(--border);
  padding: 0.55rem 0.75rem;
  text-align: left;
  vertical-align: top;
}

.guide-table th {
  background: var(--bg-subtle);
  font-weight: 700;
}

.hanja-sample {
  font-family: "Noto Serif CJK KR", "Noto Serif KR", "Batang", serif;
  font-size: 1.08em;
}

.page-jump-link {
  color: var(--accent);
  font-weight: 600;
  text-decoration: none;
}

.page-jump-link:hover { text-decoration: underline; }

@media (max-width: 960px) {
  .menu-btn { display: inline-block; }
  .layout { grid-template-columns: 1fr; }
  .sidebar {
    position: fixed;
    left: 0;
    top: 52px;
    width: 290px;
    z-index: 95;
    transform: translateX(-100%);
    transition: transform 0.2s ease;
    box-shadow: 4px 0 16px rgba(0, 0, 0, 0.15);
  }
  .sidebar.open { transform: translateX(0); }
  .paper-sheet { padding: 1.5rem 1.2rem; }
}

@media print {
  .topbar, .sidebar { display: none !important; }
  .layout { display: block; }
  .reader-main { padding: 0; }
  .paper-sheet {
    max-width: 100%;
    border: none;
    box-shadow: none;
    padding: 0;
  }
  .volume-section.hidden-vol { display: block !important; }
  .volume-section:not(.guide-section) > :not(h2, h3, header) { content-visibility: visible; }
}
"""

JS_SCRIPT = r"""
(function() {
  const body = document.body;
  const volTabs = document.querySelectorAll('.vol-tab');
  const volSections = document.querySelectorAll('.volume-section');
  const sidebar = document.getElementById('sidebar');
  const menuBtn = document.getElementById('menuBtn');
  const tocFilter = document.getElementById('tocFilter');
  const searchInput = document.getElementById('searchInput');
  const searchCount = document.getElementById('searchCount');
  const prevHitBtn = document.getElementById('prevHitBtn');
  const nextHitBtn = document.getElementById('nextHitBtn');

  // 모바일 메뉴 토글
  if (menuBtn) {
    menuBtn.addEventListener('click', () => sidebar.classList.toggle('open'));
  }

  // 권별 탭 전환
  function setActiveVol(targetId) {
    volTabs.forEach(btn => btn.classList.toggle('active', btn.dataset.target === targetId));
    volSections.forEach(sec => {
      if (targetId === 'all') {
        sec.classList.toggle('hidden-vol', sec.id === 'guide');
      } else {
        sec.classList.toggle('hidden-vol', sec.id !== targetId);
      }
    });
    document.querySelectorAll('.toc-vol-group').forEach(grp => {
      if (targetId === 'all' || targetId === 'guide') {
        grp.style.display = '';
      } else {
        grp.style.display = (grp.dataset.volId === targetId) ? '' : 'none';
        if (grp.dataset.volId === targetId) grp.classList.remove('collapsed');
      }
    });
  }

  volTabs.forEach(btn => {
    btn.addEventListener('click', () => {
      setActiveVol(btn.dataset.target);
      window.scrollTo({ top: 0, behavior: 'instant' });
    });
  });

  // 목차 아코디언
  document.querySelectorAll('.toc-vol-head').forEach(head => {
    head.addEventListener('click', () => {
      head.parentElement.classList.toggle('collapsed');
    });
  });

  // 목차를 누른 직후에는 스크롤 감시가 강조 항목을 바꾸지 않게 한다
  let pinTocUntil = 0;

  // 화면 밖 본문은 크기를 어림해 두므로, 주변이 실제 크기로 그려지는 몇 프레임 동안 위치를 다시 맞춘다
  function jumpTo(el, frames = 6) {
    el.scrollIntoView({ behavior: 'instant', block: 'center' });
    if (frames > 0) requestAnimationFrame(() => jumpTo(el, frames - 1));
  }

  // 목차 링크 클릭 시 숨겨진 권이면 자동 표시
  function revealElementSection(el) {
    const sec = el.closest('.volume-section');
    if (sec && sec.classList.contains('hidden-vol')) {
      setActiveVol(sec.id === 'guide' ? 'guide' : 'all');
    }
  }

  document.querySelectorAll('.toc-link, .page-jump-link').forEach(link => {
    link.addEventListener('click', (e) => {
      const href = link.getAttribute('href');
      if (href && href.startsWith('#')) {
        const target = document.getElementById(href.slice(1));
        if (target) {
          e.preventDefault();
          revealElementSection(target);
          if (target.classList.contains('page-mark')) {
            body.classList.remove('hide-pages');
            document.getElementById('btnPages')?.classList.add('active');
          }
          jumpTo(target);
          // 즉시 이동하면 중간 제목을 지나가지 않으므로 누른 목차 항목을 직접 강조한다
          if (link.classList.contains('toc-link')) {
            document.querySelectorAll('.toc-link.active').forEach(el => el.classList.remove('active'));
            link.classList.add('active');
            pinTocUntil = performance.now() + 1000;
          }
          if (target.classList.contains('page-mark')) {
            target.classList.add('flash-target');
            setTimeout(() => target.classList.remove('flash-target'), 1400);
          }
          sidebar.classList.remove('open');
        }
      }
    });
  });

  // 목차 필터링
  tocFilter.addEventListener('input', () => {
    const q = tocFilter.value.trim().toLowerCase();
    document.querySelectorAll('.toc-vol-group').forEach(grp => {
      let anyVisible = false;
      grp.querySelectorAll('.toc-item').forEach(item => {
        const match = !q || item.textContent.toLowerCase().includes(q);
        item.style.display = match ? '' : 'none';
        if (match) anyVisible = true;
      });
      if (q && anyVisible) grp.classList.remove('collapsed');
    });
  });

  // 보기 옵션 버튼들
  function bindToggle(btnId, bodyClass, invertActive) {
    const btn = document.getElementById(btnId);
    if (!btn) return;
    btn.addEventListener('click', () => {
      body.classList.toggle(bodyClass);
      const has = body.classList.contains(bodyClass);
      btn.classList.toggle('active', invertActive ? !has : has);
    });
  }

  bindToggle('btnCoreOnly', 'core-only', false);
  bindToggle('btnJeguSplit', 'jegu-split', false);
  bindToggle('btnPages', 'hide-pages', true);

  // 한자음 루비는 화면 근처의 본문에만 만든다. 5만 개를 모두 두면 권을 바꿀 때 몇 초씩 걸린다.
  const rubyUnits = document.querySelectorAll('.volume-section [data-r]');
  const nearUnits = new Set();

  function hydrate(unit) {
    if (unit.dataset.hy) return;
    unit.dataset.hy = '1';
    const readings = unit.dataset.r;
    const walker = document.createTreeWalker(unit, NodeFilter.SHOW_TEXT, {
      acceptNode: n => n.parentElement.closest('rt') ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT
    });
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    let at = 0;
    nodes.forEach(node => {
      const frag = document.createDocumentFragment();
      let plain = '', changed = false;
      for (const ch of node.nodeValue) {
        const reading = readings[at++];
        if (!reading || reading === '.') { plain += ch; continue; }
        if (plain) { frag.append(plain); plain = ''; }
        const ruby = document.createElement('ruby');
        ruby.className = 'lz';
        const rt = document.createElement('rt');
        rt.textContent = reading;
        ruby.append(ch, rt);
        frag.append(ruby);
        changed = true;
      }
      if (!changed) return;
      if (plain) frag.append(plain);
      node.replaceWith(frag);
    });
  }

  function dehydrate(unit) {
    if (!unit.dataset.hy) return;
    delete unit.dataset.hy;
    unit.querySelectorAll('ruby.lz').forEach(rb => {
      rb.querySelector('rt').remove();
      rb.replaceWith(...rb.childNodes);
    });
    unit.normalize();
  }

  // 한자음이 꺼져 있지 않으면 화면 근처 덩어리만 루비를 갖게 맞춘다
  function syncRuby() {
    const on = body.dataset.ruby !== 'off';
    rubyUnits.forEach(u => (on && nearUnits.has(u)) ? hydrate(u) : dehydrate(u));
  }

  // 한자 독음(루비) 보기 모드 전환 (켜짐 -> 마우스 -> 꺼짐)
  const rubyModes = ['on', 'hover', 'off'];
  const rubyLabels = { on: '한자음: 켜짐', hover: '한자음: 마우스', off: '한자음: 꺼짐' };
  let rubyIdx = Math.max(0, rubyModes.indexOf(localStorage.getItem('sarye_ruby') || 'on'));
  const btnRuby = document.getElementById('btnRuby');
  function applyRubyMode() {
    const m = rubyModes[rubyIdx];
    body.dataset.ruby = m;
    if (btnRuby) {
      btnRuby.textContent = rubyLabels[m];
      btnRuby.classList.toggle('active', m !== 'off');
    }
    localStorage.setItem('sarye_ruby', m);
    syncRuby();
  }
  applyRubyMode();
  btnRuby?.addEventListener('click', () => {
    rubyIdx = (rubyIdx + 1) % rubyModes.length;
    applyRubyMode();
  });

  // 번역 보기 모드 전환 (대역 -> 번역만 -> 원문만)
  const transModes = ['both', 'ko', 'ori'];
  const transLabels = { both: '번역: 대역', ko: '번역: 한글만', ori: '번역: 원문만' };
  let transIdx = Math.max(0, transModes.indexOf(localStorage.getItem('sarye_trans') || 'both'));
  const btnTrans = document.getElementById('btnTrans');
  function applyTransMode() {
    const m = transModes[transIdx];
    body.dataset.trans = m;
    if (btnTrans) {
      btnTrans.textContent = transLabels[m];
      btnTrans.classList.toggle('active', m !== 'ori');
    }
    localStorage.setItem('sarye_trans', m);
  }
  applyTransMode();
  btnTrans?.addEventListener('click', () => {
    transIdx = (transIdx + 1) % transModes.length;
    applyTransMode();
  });

  const btnBrackets = document.getElementById('btnBrackets');
  btnBrackets?.addEventListener('click', () => {
    const showing = body.classList.toggle('show-brackets');
    body.classList.toggle('hide-brackets', !showing);
    btnBrackets.classList.toggle('active', showing);
  });

  // 글자 크기 조절
  let scale = parseFloat(localStorage.getItem('sarye_scale') || '1');
  function applyScale() {
    document.documentElement.style.setProperty('--font-scale', scale.toFixed(2));
    localStorage.setItem('sarye_scale', scale.toFixed(2));
  }
  applyScale();

  document.getElementById('fontSmaller')?.addEventListener('click', () => {
    scale = Math.max(0.8, scale - 0.08);
    applyScale();
  });
  document.getElementById('fontReset')?.addEventListener('click', () => {
    scale = 1.0;
    applyScale();
  });
  document.getElementById('fontLarger')?.addEventListener('click', () => {
    scale = Math.min(1.45, scale + 0.08);
    applyScale();
  });

  // 테마 전환
  const themes = ['hanji', 'white', 'dark'];
  const themeNames = { hanji: '한지', white: '백지', dark: '야간' };
  let themeIdx = Math.max(0, themes.indexOf(localStorage.getItem('sarye_theme') || 'hanji'));
  const btnTheme = document.getElementById('btnTheme');
  function applyTheme() {
    const t = themes[themeIdx];
    body.dataset.theme = t;
    if (btnTheme) btnTheme.textContent = '배경: ' + themeNames[t];
    localStorage.setItem('sarye_theme', t);
  }
  applyTheme();
  btnTheme?.addEventListener('click', () => {
    themeIdx = (themeIdx + 1) % themes.length;
    applyTheme();
  });

  // 본문 검색 및 쪽 번호 바로가기
  let hits = [];
  let hitIdx = -1;

  function clearHighlights() {
    document.querySelectorAll('mark.search-hit').forEach(m => {
      const parent = m.parentNode;
      m.replaceWith(...m.childNodes);
      parent.normalize();
    });
    hits = [];
    hitIdx = -1;
    searchCount.textContent = '';
  }

  function wrapRangeInTextNode(textNode, startOffset, endOffset) {
    let target = textNode;
    if (startOffset > 0) target = target.splitText(startOffset);
    if (endOffset - startOffset < target.nodeValue.length) {
      target.splitText(endOffset - startOffset);
    }
    const mark = document.createElement('mark');
    mark.className = 'search-hit';
    target.parentNode.replaceChild(mark, target);
    mark.appendChild(target);
    return mark;
  }

  function highlightInStream(entries, query) {
    if (!entries.length) return;
    const joined = entries.map(e => e.text).join('');
    if (!joined.includes(query)) return;
    const matches = [];
    let pos = joined.indexOf(query);
    while (pos !== -1) {
      matches.push([pos, pos + query.length]);
      pos = joined.indexOf(query, pos + query.length);
    }
    // 뒤에서부터 감싸야 앞쪽 오프셋이 변하지 않는다
    for (let m = matches.length - 1; m >= 0; m--) {
      const [mStart, mEnd] = matches[m];
      let firstMark = null;
      for (let i = entries.length - 1; i >= 0; i--) {
        const e = entries[i];
        if (e.end <= mStart || e.start >= mEnd) continue;
        const s = Math.max(0, mStart - e.start);
        const en = Math.min(e.text.length, mEnd - e.start);
        const targetNode = e.markNode || e.node;
        const mark = wrapRangeInTextNode(targetNode, s, en);
        firstMark = mark;
      }
      if (firstMark) hits.unshift(firstMark);
    }
  }

  function highlightTextNodes(root, query) {
    const blocks = root.querySelectorAll(
      '.vol-title, .vol-subtitle, .ori-heading, .ko-heading, .body-para, .ori-subpara, .ko-subpara, p.ref-para, .jegu-head > span, .jegu-head:not(.has-ko), .jegu-body > div, .jegu-body:not(.has-ko), .form-head > span, .form-head:not(.has-ko), .form-line, .vol-colophon'
    );
    const isHangulQuery = /^[\uac00-\ud7a3]+$/.test(query);

    blocks.forEach(blk => {
      const baseEntries = [];
      let baseOffset = 0;
      const walker = document.createTreeWalker(blk, NodeFilter.SHOW_TEXT, {
        acceptNode(node) {
          if (!node.nodeValue) return NodeFilter.FILTER_REJECT;
          const p = node.parentElement;
          if (!p || p.closest('.page-mark, rt, script, style, .guide-section')) return NodeFilter.FILTER_REJECT;
          return NodeFilter.FILTER_ACCEPT;
        }
      });
      while (walker.nextNode()) {
        const n = walker.currentNode;
        const t = n.nodeValue;
        baseEntries.push({ node: n, text: t, start: baseOffset, end: baseOffset + t.length });
        baseOffset += t.length;
      }
      const prevHits = hits.length;
      const savedHits = hits;
      hits = [];
      highlightInStream(baseEntries, query);
      // 한글 검색어가 원문 한자 블록에서 직접 매칭되지 않았으면 루비 독음(rt) 연속열에서도 검색
      if (isHangulQuery && hits.length === 0 && !blk.classList.contains('ko-block') && !blk.classList.contains('ko-subpara') && !blk.classList.contains('ko-heading') && !blk.classList.contains('ko-span')) {
        // 아직 루비를 만들지 않은 덩어리는 모아 둔 독음에 검색어가 있을 때만 만든다
        const unit = blk.closest('[data-r]');
        if (unit && (unit.dataset.r.replaceAll('.', '').includes(query) || unit.querySelector('.ids-glyph'))) hydrate(unit);
        const rtEntries = [];
        let rtOffset = 0;
        blk.querySelectorAll('ruby').forEach(rb => {
          const rt = rb.querySelector('rt');
          const baseNode = Array.from(rb.childNodes).find(c => c.nodeType === Node.TEXT_NODE);
          if (rt && baseNode && rt.textContent) {
            const t = rt.textContent;
            rtEntries.push({ node: rt.firstChild, markNode: baseNode, text: t, start: rtOffset, end: rtOffset + t.length });
            rtOffset += t.length;
          }
        });
        if (rtEntries.length) {
          const joinedRt = rtEntries.map(e => e.text).join('');
          let pos = joinedRt.indexOf(query);
          const rtMatches = [];
          while (pos !== -1) {
            rtMatches.push([pos, pos + query.length]);
            pos = joinedRt.indexOf(query, pos + query.length);
          }
          for (let m = rtMatches.length - 1; m >= 0; m--) {
            const [mStart, mEnd] = rtMatches[m];
            let firstMark = null;
            for (let i = rtEntries.length - 1; i >= 0; i--) {
              const e = rtEntries[i];
              if (e.end <= mStart || e.start >= mEnd) continue;
              const mark = wrapRangeInTextNode(e.markNode, 0, e.markNode.nodeValue.length);
              firstMark = mark;
            }
            if (firstMark) hits.unshift(firstMark);
          }
        }
      }
      savedHits.push(...hits);
      hits = savedHits;
    });
  }

  function focusHit(index) {
    if (!hits.length) return;
    hits.forEach(h => h.classList.remove('current-hit'));
    hitIdx = (index + hits.length) % hits.length;
    const cur = hits[hitIdx];
    cur.classList.add('current-hit');
    revealElementSection(cur);
    jumpTo(cur);
    searchCount.textContent = (hitIdx + 1) + ' / ' + hits.length;
  }

  function runSearch() {
    const q = searchInput.value.trim();
    clearHighlights();
    if (!q) return;

    // 쪽 번호 점프 (예: 180, p180, p.412)
    const pm = q.match(/^p?\.?(\d{3,4})$/i);
    if (pm) {
      const target = document.getElementById('p' + parseInt(pm[1], 10));
      if (target) {
        body.classList.remove('hide-pages');
        document.getElementById('btnPages')?.classList.add('active');
        revealElementSection(target);
        jumpTo(target);
        target.classList.add('flash-target');
        setTimeout(() => target.classList.remove('flash-target'), 1500);
        searchCount.textContent = 'p.' + pm[1] + ' 이동';
      } else {
        searchCount.textContent = '쪽 없음';
      }
      return;
    }

    document.querySelectorAll('.volume-section:not(.guide-section)').forEach(sec => {
      highlightTextNodes(sec, q);
    });
    syncRuby();
    if (hits.length > 0) {
      focusHit(0);
    } else {
      searchCount.textContent = '0건';
    }
  }

  let debounceTimer = null;
  searchInput?.addEventListener('input', () => {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(runSearch, 220);
  });

  searchInput?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      if (!hits.length) runSearch();
      else focusHit(e.shiftKey ? hitIdx - 1 : hitIdx + 1);
    }
  });

  prevHitBtn?.addEventListener('click', () => focusHit(hitIdx - 1));
  nextHitBtn?.addEventListener('click', () => focusHit(hitIdx + 1));

  // 스크롤 위치에 따른 목차 활성화
  const tocMap = new Map();
  document.querySelectorAll('.toc-link').forEach(a => {
    const id = a.getAttribute('href')?.slice(1);
    if (id) tocMap.set(id, a);
  });

  const observer = new IntersectionObserver((entries) => {
    if (performance.now() < pinTocUntil) return;
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        const link = tocMap.get(entry.target.id);
        if (link) {
          document.querySelectorAll('.toc-link.active').forEach(el => el.classList.remove('active'));
          link.classList.add('active');
        }
      }
    });
  }, { rootMargin: '-12% 0px -78% 0px' });

  document.querySelectorAll('.sec-h2, .sec-h3').forEach(h => observer.observe(h));

  const rubyObserver = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      const unit = entry.target;
      if (entry.isIntersecting) nearUnits.add(unit); else nearUnits.delete(unit);
      if (entry.isIntersecting && body.dataset.ruby !== 'off') hydrate(unit); else dehydrate(unit);
    });
  }, { rootMargin: '1500px 0px' });
  rubyUnits.forEach(u => rubyObserver.observe(u));

  // 인쇄할 때는 모든 본문에 루비를 만들고, 끝나면 화면 근처만 남긴다
  window.addEventListener('beforeprint', () => { if (body.dataset.ruby !== 'off') rubyUnits.forEach(hydrate); });
  window.addEventListener('afterprint', syncRuby);
})();
"""


def build_guide_section(pages, page_notes):
    """범례(凡例) 및 교감·확인 필요 목록 섹션 HTML을 만든다."""
    uncertain_rows = []
    for n, context in build.uncertain(pages):
        notes = " / ".join(page_notes.get(n, []))
        ctx_html = html.escape(context).replace(
            "[?]", '<span class="uncertain-mark">[?]</span>'
        )
        uncertain_rows.append(
            f'<tr>'
            f'<td><a class="page-jump-link" href="#p{n}">p.{n}</a></td>'
            f'<td class="hanja-sample">{ctx_html}</td>'
            f'<td>{html.escape(notes)}</td>'
            f'</tr>'
        )

    other_note_rows = []
    uncertain_pages = {n for n, _ in build.uncertain(pages)}
    for n in sorted(page_notes):
        for note in page_notes[n]:
            if n in uncertain_pages and len(page_notes[n]) == 1:
                continue
            other_note_rows.append(
                f'<tr>'
                f'<td><a class="page-jump-link" href="#p{n}">p.{n}</a></td>'
                f'<td>{html.escape(note)}</td>'
                f'</tr>'
            )

    return f"""
<section class="volume-section guide-section hidden-vol" id="guide" data-vol="guide">
  <header class="vol-header">
    <div class="vol-kicker">凡例 · 校勘記</div>
    <h1 class="vol-title">전사 범례 및 교감 일람</h1>
    <div class="vol-subtitle">이재(李縡) 편 『사례편람(四禮便覽)』 상례(권3~권6) 본문 312쪽</div>
  </header>

  <h2 class="sec-h2">1. 판본 및 수록 범위</h2>
  <p>저본은 『사례편람(四禮便覽)』 영인 PDF이며, 권3·권4는 한국학중앙연구원 장서각 소장본(K1-55)을 대조본으로 삼아 판독을 보완하였다. 도식(圖式) 30쪽을 제외한 상례 본문 전체 312쪽을 수록하였다.</p>
  <table class="guide-table">
    <thead>
      <tr><th>권차</th><th>편명</th><th>내용</th><th>본문 쪽수 (PDF)</th></tr>
    </thead>
    <tbody>
      <tr><td>卷之三</td><td>喪禮一</td><td>初終 · 襲 · 小斂 · 大斂</td><td>p.132–193 (62쪽)</td></tr>
      <tr><td>卷之四</td><td>喪禮二</td><td>成服 · 五服 · 朝夕奠 · 弔 · 聞喪 · 奔喪</td><td>p.210–289 (80쪽)</td></tr>
      <tr><td>卷之五</td><td>喪禮三</td><td>治葬 · 遷柩 · 發引 · 及墓 · 題主 · 成墳 · 反哭</td><td>p.300–365 (66쪽)</td></tr>
      <tr><td>卷之六</td><td>喪禮四</td><td>虞祭 · 卒哭 · 祔 · 小祥 · 大祥 · 禫 · 吉祭 · 復寢</td><td>p.368–471 (104쪽)</td></tr>
    </tbody>
  </table>

  <h2 class="sec-h2">2. 원전 판면 층위(凡例) 표시 기준</h2>
  <table class="guide-table">
    <thead>
      <tr><th>원전 판면 형태</th><th>의미 (저본 범례 p.9–12)</th><th>문서 내 표현</th></tr>
    </thead>
    <tbody>
      <tr><td>맨 위에서 시작하는 큰 글자</td><td>『주자가례(家禮)』 정문(正文) 조목</td><td>절차 표제 (<strong>▪ 조목 제목</strong>)</td></tr>
      <tr><td>한 칸 낮춘 큰 글자</td><td>『주자가례』 본주(本註)</td><td>기본 크기 먹색 본문</td></tr>
      <tr><td>본문 사이 한 줄 작은 글자</td><td>타서(他書)에서 보충·변경한 구절</td><td><span class="hanja-sample"><span class="layer-supp">청회색 보충 구절(점선 밑줄)</span></span></td></tr>
      <tr><td>두 줄 작은 글자 (쌍행 세주)</td><td>세주(細註, 주석 및 서식 작성법)</td><td><span class="hanja-sample"><span class="layer-note">작은 크기 담묵색 세주</span></span></td></tr>
      <tr><td>두 칸 낮춘 두 줄 작은 글자</td><td>선유 참고설 및 저자 이재의 안설(按說)</td><td>좌측 테두리 들여쓰기 상자 (<span class="cartouche-round">按</span> 등)</td></tr>
      <tr><td>둥근 테두리 안의 글자</td><td>출전(책 이름, 註, 疏, 按) · 품계</td><td><span class="hanja-sample"><span class="cartouche-round">備要</span> <span class="cartouche-round">士喪禮</span></span> (둥근 캡슐 배지)</td></tr>
      <tr><td>검은 바탕의 흰 글자 (흑지백자)</td><td>제구(諸具) · 서식 표제 · 물품 이름</td><td><span class="hanja-sample"><span class="cartouche-black">諸具</span> <span class="cartouche-black">祝文式</span></span> (흑백 반전 배지)</td></tr>
    </tbody>
  </table>

  <h2 class="sec-h2">3. 자형 확인 필요 목록 ({len(uncertain_rows)}곳)</h2>
  <p>원전 인쇄 상태나 자형상 문맥과 대조 확인이 필요한 곳은 원문 글자를 임의로 고치지 않고 지면 자형대로 적은 뒤 <span class="uncertain-mark">[?]</span>를 붙였다. 쪽 번호를 누르면 본문 해당 위치로 이동한다.</p>
  <table class="guide-table">
    <thead>
      <tr><th>쪽</th><th>본문 문맥</th><th>판독 · 대조 메모</th></tr>
    </thead>
    <tbody>
      {"".join(uncertain_rows)}
    </tbody>
  </table>

  <h2 class="sec-h2">4. 기타 자형 · 판독 참고 메모</h2>
  <table class="guide-table">
    <thead>
      <tr><th>쪽</th><th>메모</th></tr>
    </thead>
    <tbody>
      {"".join(other_note_rows)}
    </tbody>
  </table>
</section>
"""


def generate_html_document(pages, translations=None):
    """pages {쪽번호: 원문텍스트} 전체로부터 완성된 단일 HTML 문자열을 생성한다."""
    if translations is None:
        trans_dir = Path(__file__).resolve().parent.parent / "translation"
        translations = load_translations(trans_dir)

    page_meta = {}
    page_notes = {}
    for n, raw in pages.items():
        jang, is_empty, notes = parse_page_meta(raw)
        page_meta[n] = (jang, is_empty, notes)
        if notes:
            page_notes[n] = notes

    vol_sections_html = []
    sidebar_groups_html = []

    for vol_key in ("권3", "권4", "권5", "권6"):
        meta = VOLUME_META[vol_key]
        sec_html, toc = build_volume_html(vol_key, pages, page_meta, page_notes, translations=translations)
        vol_sections_html.append(sec_html)

        toc_items = []
        for item in toc:
            lvl = item["level"]
            esc_title = html.escape(item["title"])
            toc_reading = html.escape(hanja_readings.plain_reading_string(item["title"]))
            ko_badge = '<span class="toc-ko-badge">역</span>' if item.get("translated") and lvl == 2 else ""
            toc_items.append(
                f'<li class="toc-item lvl-{lvl}">'
                f'<a class="toc-link" href="#{item["id"]}" title="{toc_reading}">'
                f'<span>{esc_title}{ko_badge}</span>'
                f'<span class="toc-pnum">p.{item["page"]}</span>'
                f'</a></li>'
            )
        sidebar_groups_html.append(
            f'<div class="toc-vol-group" data-vol-id="{meta["id"]}">'
            f'<button type="button" class="toc-vol-head">'
            f'<span>{meta["short"]} ({meta["sub"]})</span>'
            f'<span>▾</span>'
            f'</button>'
            f'<ul class="toc-vol-list">{"".join(toc_items)}</ul>'
            f'</div>'
        )

    guide_html = build_guide_section(pages, page_notes)

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>四禮便覽 (사례편람 상례편 권3~권6)</title>
  <style>{CSS_STYLES}</style>
</head>
<body class="jegu-split hide-brackets" data-theme="hanji" data-ruby="on">
  <header class="topbar">
    <div class="brand-group">
      <button type="button" class="menu-btn" id="menuBtn" aria-label="목차 열기">☰ 목차</button>
      <span class="brand-seal">四禮便覽</span>
      <span class="brand-title">사례편람 상례편</span>
      <span class="brand-sub">卷三~卷六 원문 정본</span>
    </div>

    <nav class="vol-tabs" aria-label="권 선택">
      <button type="button" class="vol-tab active" data-target="all">전체</button>
      <button type="button" class="vol-tab" data-target="vol3">卷三 喪禮一</button>
      <button type="button" class="vol-tab" data-target="vol4">卷四 喪禮二</button>
      <button type="button" class="vol-tab" data-target="vol5">卷五 喪禮三</button>
      <button type="button" class="vol-tab" data-target="vol6">卷六 喪禮四</button>
      <button type="button" class="vol-tab" data-target="guide">범례·교감</button>
    </nav>

    <div class="toolbar-controls">
      <div class="search-wrap" title="한자 검색어 또는 쪽 번호(예: 180, p.412) 입력 후 Enter">
        <input type="search" id="searchInput" placeholder="본문 검색 / 쪽 번호(p.180)">
        <span class="search-count" id="searchCount"></span>
        <button type="button" class="icon-btn" id="prevHitBtn" title="이전 검색 결과">▲</button>
        <button type="button" class="icon-btn" id="nextHitBtn" title="다음 검색 결과">▼</button>
      </div>
      <button type="button" class="opt-btn active" id="btnRuby" title="한자 독음(음) 표시 전환 (켜짐 / 마우스 / 꺼짐)">한자음: 켜짐</button>
      <button type="button" class="opt-btn active" id="btnTrans" title="한글 번역 보기 전환 (대역 / 한글만 / 원문만)">번역: 대역</button>
      <button type="button" class="opt-btn" id="btnCoreOnly" title="세주·선유설·제구를 접고 가례 본문과 보충문만 읽습니다">본문만</button>
      <button type="button" class="opt-btn active" id="btnJeguSplit" title="제구(諸具) 물품을 항목별로 줄바꿈해 표시합니다">제구 줄바꿈</button>
      <button type="button" class="opt-btn" id="btnBrackets" title="원문 전사 괄호 기호(〈〉｛｝) 표시 전환">괄호 기호</button>
      <button type="button" class="opt-btn active" id="btnPages" title="PDF 쪽 번호 및 장차 배지 표시 전환">쪽 번호</button>
      <button type="button" class="icon-btn" id="fontSmaller" title="글자 작게">A-</button>
      <button type="button" class="icon-btn" id="fontReset" title="기본 크기">A</button>
      <button type="button" class="icon-btn" id="fontLarger" title="글자 크게">A+</button>
      <button type="button" class="opt-btn" id="btnTheme" title="배경 테마 전환">배경: 한지</button>
    </div>
  </header>

  <div class="layout">
    <aside class="sidebar" id="sidebar">
      <input type="search" class="toc-filter" id="tocFilter" placeholder="목차 항목 필터...">
      {"".join(sidebar_groups_html)}
    </aside>

    <main class="reader-main">
      <article class="paper-sheet">
        {"".join(vol_sections_html)}
        {guide_html}
      </article>
    </main>
  </div>

  <script>{JS_SCRIPT}</script>
</body>
</html>
"""


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="사례편람 전사본 HTML 문서 생성기")
    default_out = Path(__file__).resolve().parent.parent / "transcription" / "사례편람.html"
    ap.add_argument("--out", type=Path, default=default_out, help="출력 HTML 파일 경로")
    args = ap.parse_args()

    root = Path(__file__).resolve().parent.parent / "transcription"
    pages = {
        int(p.stem[1:]): p.read_text(encoding="utf-8")
        for p in sorted((root / "pages").glob("p*.md"))
    }

    errors = []
    for vol in build.VOLUMES.values():
        start, end = vol["body"]
        errors += build.check(pages, start, end, final=True)
    if errors:
        for e in errors:
            print("오류:", e, file=sys.stderr)
        sys.exit(1)

    doc_html = generate_html_document(pages)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(doc_html, encoding="utf-8")
    print(f"HTML 생성 완료: {args.out} ({len(doc_html):,}자, 본문 {len(pages)}쪽)")


if __name__ == "__main__":
    main()
