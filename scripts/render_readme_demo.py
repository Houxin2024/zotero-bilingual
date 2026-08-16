from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "docs" / "assets"
GIF_PATH = OUTPUT_DIR / "linked-selection-demo.gif"
POSTER_PATH = OUTPUT_DIR / "linked-selection-demo.png"

WIDTH, HEIGHT = 1200, 675
PURPLE = (103, 80, 196)
PURPLE_DARK = (74, 55, 155)
PURPLE_LIGHT = (239, 236, 255)
INK = (38, 43, 54)
MUTED = (101, 110, 126)


def first_font(*candidates: str) -> str:
    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            return str(path)
    raise FileNotFoundError("No suitable font found")


LATIN_REGULAR = first_font(
    "/mnt/c/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)
LATIN_BOLD = first_font(
    "/mnt/c/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)
CJK_REGULAR = first_font(
    "/mnt/c/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
)
CJK_BOLD = first_font(
    "/mnt/c/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
)

FONT_APP = ImageFont.truetype(LATIN_BOLD, 22)
FONT_TOOLBAR = ImageFont.truetype(LATIN_REGULAR, 15)
FONT_SECTION = ImageFont.truetype(LATIN_BOLD, 17)
FONT_EN = ImageFont.truetype(LATIN_REGULAR, 19)
FONT_ZH = ImageFont.truetype(CJK_REGULAR, 19)
FONT_BADGE = ImageFont.truetype(CJK_BOLD, 16)
FONT_STATUS = ImageFont.truetype(CJK_REGULAR, 15)


PAIRS = [
    (
        "A unified DNA encoder turns raw genomic sequences into continuous representations.",
        "统一的 DNA 编码器将原始基因组序列转换为连续表征。",
    ),
    (
        "The same representation can describe RNA reads, regulatory regions, and genomic fragments.",
        "同一种表征可以描述 RNA 读段、调控区域和基因组片段。",
    ),
    (
        "One click selects a sentence and instantly highlights its aligned translation.",
        "单击一句，即可立即高亮与之对齐的译文。",
    ),
    (
        "Clicking the Chinese sentence works in the reverse direction as well.",
        "单击中文句子时，反向联动同样有效。",
    ),
]


def wrap_words(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, width: int):
    lines, current = [], ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if not current or draw.textlength(trial, font=font) <= width:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def wrap_cjk(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, width: int):
    lines, current = [], ""
    for char in text:
        trial = current + char
        if not current or draw.textlength(trial, font=font) <= width:
            current = trial
        else:
            lines.append(current)
            current = char
    if current:
        lines.append(current)
    return lines


def draw_multiline(draw, xy, lines, font, fill, spacing=7):
    x, y = xy
    bbox = draw.textbbox((x, y), "Ag", font=font)
    line_height = bbox[3] - bbox[1]
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        y += line_height + spacing
    return y


def draw_cursor(draw: ImageDraw.ImageDraw, x: int, y: int, pulse: int):
    radius = 14 + pulse * 5
    alpha_color = (133, 109, 224)
    draw.ellipse((x - radius, y - radius, x + radius, y + radius), outline=alpha_color, width=3)
    draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill=PURPLE_DARK)
    draw.polygon(
        [(x + 7, y + 7), (x + 7, y + 30), (x + 14, y + 23), (x + 21, y + 38),
         (x + 28, y + 35), (x + 20, y + 20), (x + 30, y + 20)],
        fill=(40, 44, 54),
    )


def frame(active_pair=None, source_side=None, pulse=0, show_badge=False):
    image = Image.new("RGB", (WIDTH, HEIGHT), (244, 246, 250))
    draw = ImageDraw.Draw(image)

    # Zotero-like window chrome and toolbar.
    draw.rounded_rectangle((25, 20, WIDTH - 25, HEIGHT - 20), radius=16, fill=(255, 255, 255),
                           outline=(211, 216, 226), width=2)
    draw.rounded_rectangle((25, 20, WIDTH - 25, 72), radius=16, fill=(46, 50, 61))
    draw.rectangle((25, 54, WIDTH - 25, 72), fill=(46, 50, 61))
    draw.ellipse((45, 38, 59, 52), fill=(244, 105, 102))
    draw.ellipse((68, 38, 82, 52), fill=(244, 194, 77))
    draw.ellipse((91, 38, 105, 52), fill=(92, 197, 108))
    draw.text((130, 34), "Zotero  ·  Bilingual PDF", font=FONT_APP, fill=(247, 248, 251))
    draw.text((912, 38), "2 / 12     110%", font=FONT_TOOLBAR, fill=(213, 217, 226))
    draw.rectangle((26, 72, WIDTH - 26, 104), fill=(237, 239, 244))
    draw.text((55, 80), "←  →     −   +      Fit width", font=FONT_TOOLBAR, fill=(78, 84, 98))

    # Paper surface.
    page = (66, 116, WIDTH - 66, HEIGHT - 60)
    draw.rounded_rectangle((page[0] + 6, page[1] + 8, page[2] + 7, page[3] + 10), radius=5,
                           fill=(214, 218, 226))
    draw.rounded_rectangle(page, radius=5, fill=(255, 255, 253), outline=(222, 224, 229))
    split_x = WIDTH // 2
    draw.line((split_x, 138, split_x, HEIGHT - 84), fill=(215, 218, 225), width=2)
    draw.text((102, 140), "ENGLISH ORIGINAL", font=FONT_SECTION, fill=PURPLE_DARK)
    draw.text((638, 139), "中文翻译", font=ImageFont.truetype(CJK_BOLD, 18), fill=PURPLE_DARK)
    draw.line((102, 169, 552, 169), fill=(226, 228, 234), width=1)
    draw.line((638, 169, 1088, 169), fill=(226, 228, 234), width=1)

    y_positions = [191, 280, 369, 458]
    left_x, right_x, text_width = 102, 638, 430
    boxes = {}
    for index, ((english, chinese), y) in enumerate(zip(PAIRS, y_positions)):
        en_lines = wrap_words(draw, english, FONT_EN, text_width)
        zh_lines = wrap_cjk(draw, chinese, FONT_ZH, text_width)
        line_height = 26
        box_height = max(len(en_lines), len(zh_lines)) * line_height + 10
        boxes[(index, "left")] = (left_x - 10, y - 7, left_x + text_width + 10, y + box_height)
        boxes[(index, "right")] = (right_x - 10, y - 7, right_x + text_width + 10, y + box_height)
        if index == active_pair:
            for side in ("left", "right"):
                draw.rounded_rectangle(boxes[(index, side)], radius=8, fill=PURPLE_LIGHT,
                                       outline=(158, 140, 226), width=2)
        draw_multiline(draw, (left_x, y), en_lines, FONT_EN, INK)
        draw_multiline(draw, (right_x, y), zh_lines, FONT_ZH, INK)

    if active_pair is not None and source_side:
        box = boxes[(active_pair, source_side)]
        cursor_x = box[0] + 33 if source_side == "left" else box[2] - 36
        cursor_y = box[1] + 21
        draw_cursor(draw, cursor_x, cursor_y, pulse)

    draw.text((103, 572), "Demo text · no paper content is distributed", font=FONT_TOOLBAR, fill=(151, 157, 169))
    draw.text((1044, 572), "2", font=FONT_TOOLBAR, fill=(151, 157, 169))

    if show_badge:
        badge = (784, 608, 1138, 651)
        draw.rounded_rectangle(badge, radius=12, fill=(246, 244, 255), outline=(168, 153, 229), width=2)
        draw.ellipse((800, 620, 817, 637), fill=(38, 166, 91))
        draw.text((828, 618), "双语句子已同步高亮", font=FONT_BADGE, fill=PURPLE_DARK)
    else:
        draw.text((777, 619), "✓ 句子映射已就绪 · 单击即可联动", font=FONT_STATUS, fill=MUTED)
    return image


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    sequence = [
        (frame(), 850),
        (frame(2, "left", pulse=2, show_badge=True), 220),
        (frame(2, "left", pulse=1, show_badge=True), 1450),
        (frame(), 500),
        (frame(3, "right", pulse=2, show_badge=True), 220),
        (frame(3, "right", pulse=1, show_badge=True), 1450),
        (frame(), 850),
    ]
    frames = [item[0].quantize(colors=128, method=Image.Quantize.MEDIANCUT) for item in sequence]
    durations = [item[1] for item in sequence]
    frames[0].save(
        GIF_PATH,
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        optimize=True,
        disposal=2,
    )
    frame(2, "left", pulse=1, show_badge=True).save(POSTER_PATH, optimize=True)
    print(GIF_PATH)
    print(POSTER_PATH)


if __name__ == "__main__":
    main()
