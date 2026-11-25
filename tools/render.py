"""PDF 쪽을 판독용 이미지로 만든다.

  python tools/render.py sheet <pdf> <쪽 목록> <출력폴더>    여러 쪽을 축소해 한 장에 배열
  python tools/render.py page  <pdf> <쪽 목록> <출력폴더>    쪽 전체 (긴 변 1600px)
  python tools/render.py slice <pdf> <쪽 목록> <출력폴더>    세로 조각 (오른쪽부터 _1, _2, ...)

쪽 목록 예: 124-130  또는  124,200,291   (PDF 쪽 번호, 1부터)
"""
import argparse
import io
import os
import sys

import pymupdf
from PIL import Image, ImageDraw

Image.MAX_IMAGE_PIXELS = None


def parse_pages(spec):
    pages = []
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-")
            pages.extend(range(int(a), int(b) + 1))
        else:
            pages.append(int(part))
    return pages


def load_page(doc, number, long_side):
    """쪽 하나를 긴 변이 long_side 픽셀인 회색조 이미지로 렌더링한다."""
    page = doc[number - 1]
    zoom = long_side / max(page.rect.width, page.rect.height)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csGRAY)
    return Image.open(io.BytesIO(pix.tobytes("png"))).convert("L")


def make_sheet(doc, pages, outdir, stem, cols=6, cell=420, per_sheet=24):
    for start in range(0, len(pages), per_sheet):
        group = pages[start:start + per_sheet]
        thumbs = [load_page(doc, n, cell) for n in group]
        w = max(t.width for t in thumbs)
        h = max(t.height for t in thumbs) + 24
        rows = (len(thumbs) + cols - 1) // cols
        sheet = Image.new("L", (w * cols, h * rows), 128)
        draw = ImageDraw.Draw(sheet)
        for i, (n, t) in enumerate(zip(group, thumbs)):
            x, y = (i % cols) * w, (i // cols) * h
            draw.rectangle([x, y, x + w - 3, y + 22], fill=255)
            draw.text((x + 6, y + 4), f"p.{n}", fill=0, font_size=16)
            sheet.paste(t, (x, y + 24))
        path = os.path.join(outdir, f"{stem}_sheet_{group[0]:04d}-{group[-1]:04d}.png")
        sheet.save(path)
        print(path)


def make_pages(doc, pages, outdir, stem, long_side):
    for n in pages:
        path = os.path.join(outdir, f"{stem}_p{n:04d}.png")
        load_page(doc, n, long_side).save(path)
        print(path)


def make_slices(doc, pages, outdir, stem, count, overlap, long_side, box):
    for n in pages:
        img = load_page(doc, n, long_side)
        left, top, right, bottom = box
        img = img.crop((int(img.width * left), int(img.height * top),
                        int(img.width * right), int(img.height * bottom)))
        step = img.width / count
        pad = step * overlap
        for i in range(count):
            x1 = img.width - i * step
            x0 = x1 - step
            part = img.crop((int(max(0, x0 - pad)), 0, int(min(img.width, x1 + pad)), img.height))
            path = os.path.join(outdir, f"{stem}_p{n:04d}_{i + 1}.png")
            part.save(path)
            print(path)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["sheet", "page", "slice"])
    ap.add_argument("pdf")
    ap.add_argument("pages")
    ap.add_argument("outdir")
    ap.add_argument("--stem", default="b", help="출력 파일 이름 머리글")
    ap.add_argument("--long", type=int, default=None, help="긴 변 픽셀 수")
    ap.add_argument("--count", type=int, default=2, help="조각 수")
    ap.add_argument("--overlap", type=float, default=0.06, help="조각 겹침 비율")
    ap.add_argument("--box", default="0,0,1,1", help="자를 영역: 왼,위,오른,아래 (0~1)")
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    doc = pymupdf.open(args.pdf)
    pages = parse_pages(args.pages)
    bad = [n for n in pages if not 1 <= n <= doc.page_count]
    if bad:
        sys.exit(f"쪽 번호가 범위(1~{doc.page_count})를 벗어남: {bad}")

    if args.mode == "sheet":
        make_sheet(doc, pages, args.outdir, args.stem)
    elif args.mode == "page":
        make_pages(doc, pages, args.outdir, args.stem, args.long or 1600)
    else:
        box = tuple(float(v) for v in args.box.split(","))
        make_slices(doc, pages, args.outdir, args.stem, args.count, args.overlap,
                    args.long or 2600, box)


if __name__ == "__main__":
    main()
