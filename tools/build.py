"""쪽별 전사를 검사하고 권별 문서로 합친다.

  python tools/build.py            진행 중 검사와 합본 (미전사 쪽은 허용)
  python tools/build.py --final    모든 쪽이 있어야 통과

산출: transcription/권3.md, 권4.md, 권5.md, 권6.md, 확인필요.md
"""
import argparse
import re
import sys
from pathlib import Path

VOLUMES = {
    "권3": {"title": "卷之三", "body": (132, 193), "figures": (126, 131)},
    "권4": {"title": "卷之四", "body": (210, 289), "figures": (194, 209)},
    "권5": {"title": "卷之五", "body": (300, 365), "figures": (294, 299)},
    "권6": {"title": "卷之六", "body": (368, 471), "figures": (366, 367)},
}
PAIRS = {"〈": "〉", "｛": "｝", "〔": "〕", "【": "】"}
CLOSERS = {v: k for k, v in PAIRS.items()}
COMMENT = re.compile(r"<!--.*?-->", re.S)
HEADER = re.compile(r"\A\s*<!--\s*p\.(\d+)\b([^>]*)-->")
HEADING = re.compile(r"^(#{2,3}) +(.+)$", re.M)


def body_of(text):
    """주석(쪽 머리줄, 확인 메모)을 뺀 본문."""
    return COMMENT.sub("", text).strip()


def continues(text):
    m = HEADER.match(text)
    return bool(m and "이어짐" in m.group(2))


def runs(numbers):
    """연속한 쪽 번호 묶음."""
    group = []
    for n in numbers:
        if group and n != group[-1] + 1:
            yield group
            group = []
        group.append(n)
    if group:
        yield group


def check(pages, start, end, final=False):
    """pages {쪽 번호: 파일 내용} 중 start~end 를 검사해 오류 목록을 돌려준다."""
    errors = []
    present = [n for n in range(start, end + 1) if n in pages]
    if final:
        errors += [f"p.{n}: 전사 파일 없음" for n in range(start, end + 1) if n not in pages]

    for n in present:
        m = HEADER.match(pages[n])
        if not m or int(m.group(1)) != n:
            errors.append(f"p.{n}: 머리줄의 쪽 번호가 파일과 다름")
        text = body_of(pages[n]).replace("[한글 생략]", "")
        found = re.findall(r"[ㄱ-ㆎ가-힣]+", text)
        if found:
            errors.append(f"p.{n}: 본문에 한글 {found[:3]}")

    for group in runs(present):
        # 앞뒤 쪽이 아직 없으면 거기서 열리거나 닫히는 괄호는 판단하지 않는다.
        open_before = not final and group[0] != start
        open_after = not final and group[-1] != end
        stack = []
        for n in group:
            for ch in body_of(pages[n]):
                if ch in PAIRS:
                    stack.append((ch, n))
                elif ch in CLOSERS:
                    if stack and stack[-1][0] == CLOSERS[ch]:
                        stack.pop()
                    elif stack:
                        errors.append(f"p.{n}: {ch} 가 {stack[-1][0]}(p.{stack[-1][1]}) 안에서 닫힘")
                    elif not open_before:
                        errors.append(f"p.{n}: 여는 괄호 없이 {ch}")
        if not open_after:
            errors += [f"p.{n}: {ch} 가 닫히지 않음" for ch, n in stack]
    return errors


def assemble(title, pages, numbers):
    """numbers 순서의 쪽들을 목차가 있는 문서 하나로 합친다."""
    contents, out, previous = [], "", None
    for n in numbers:
        text = body_of(pages[n])
        contents += [f"{'  ' * (len(h) - 2)}- {name} (p.{n})" for h, name in HEADING.findall(text)]
        mark = f"<!-- p.{n} -->"
        if previous is None:
            out = f"{mark}\n\n{text}"
        elif n != previous + 1:
            out += f"\n\n*(p.{previous + 1}–{n - 1} 미전사)*\n\n{mark}\n\n{text}"
        elif continues(pages[n]):
            if out.rsplit("\n", 1)[-1].startswith(">") and text.startswith(">"):
                text = text[1:].lstrip()
            out += mark + text
        else:
            out += f"\n\n{mark}\n\n{text}"
        previous = n
    return f"# 四禮便覽{title}\n\n## 목차\n\n" + "\n".join(contents) + f"\n\n---\n\n{out}\n"


def locate(pages):
    """[?] 와 □ 가 있는 곳을 (쪽, 권, 편, 앞뒤 문맥) 목록으로 돌려준다.

    편은 그 자리 앞의 마지막 ## 제목이며, 권의 도(圖)나 본문이 새로 시작하면 비운다.
    """
    found, section, current = [], "", None
    for n in sorted(pages):
        volume, start = next(
            ((name, first) for name, vol in VOLUMES.items()
             for first, last in (vol["figures"], vol["body"]) if first <= n <= last),
            ("", None))
        if start != current:
            section, current = "", start
        text = body_of(pages[n])
        headings = [(m.start(), m.group(2)) for m in HEADING.finditer(text) if m.group(1) == "##"]
        for m in re.finditer(r"\[\?\]|□", text):
            before = [name for position, name in headings if position < m.start()]
            context = text[max(0, m.start() - 8):m.end() + 6].replace("\n", " ")
            found.append((n, volume, before[-1] if before else section, context))
        if headings:
            section = headings[-1][1]
    return found


def uncertain(pages):
    """[?] 와 □ 가 있는 곳을 (쪽, 앞뒤 문맥) 목록으로 돌려준다."""
    return [(n, context) for n, _, _, context in locate(pages)]


def report(pages):
    """확인필요.md 내용."""
    found = locate(pages)
    lines = "\n".join(f"| {n} | {v} | {s} | {c.replace('|', '/')} |" for n, v, s, c in found)
    return f"# 확인 필요 ({len(found)}곳)\n\n| 쪽 | 권 | 편 | 문맥 |\n|---|---|---|---|\n{lines}\n"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--final", action="store_true", help="모든 본문 쪽이 있어야 통과")
    args = ap.parse_args()

    root = Path(__file__).resolve().parent.parent / "transcription"
    pages = {int(p.stem[1:]): p.read_text(encoding="utf-8") for p in sorted((root / "pages").glob("p*.md"))}

    failed = False
    for name, vol in VOLUMES.items():
        total, done, errors = 0, [], []
        for start, end in (vol["figures"], vol["body"]):
            errors += check(pages, start, end, final=args.final)
            total += end - start + 1
            done += [n for n in range(start, end + 1) if n in pages]
        print(f"{name}: {len(done)}/{total}쪽 전사, 오류 {len(errors)}건")
        for e in errors:
            print("  " + e)
        if errors:
            failed = True
        elif done:
            (root / f"{name}.md").write_text(assemble(vol["title"], pages, done), encoding="utf-8")

    found = uncertain(pages)
    (root / "확인필요.md").write_text(report(pages), encoding="utf-8")
    unread = sum(body_of(t).count("□") for t in pages.values())
    print(f"판독 불가 □ {unread}곳, 불확실 [?] {len(found) - unread}곳")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
