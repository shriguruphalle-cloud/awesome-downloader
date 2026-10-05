"""Contact sheets of the QA stills (out/stills -> out/sheet-*.png)."""
import glob
import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "out")
ORDER = ["hook", "logo", "why", "tour", "videoFetch", "videoOptions", "queue", "downloads", "images", "torrent",
         "browserHome", "browserSite", "bookmarks", "private", "history", "settings", "themes", "unique",
         "performance", "openSource", "quickStart", "outro"]
W, H = 640, 360

for g in range(0, len(ORDER), 6):
    grp = ORDER[g:g + 6]
    sheet = Image.new("RGB", (3 * W, len(grp) * H))
    d = ImageDraw.Draw(sheet)
    for r, sid in enumerate(grp):
        for c, part in enumerate(["1-start", "2-mid", "3-end"]):
            p = os.path.join(OUT, "stills", "%s-%s.png" % (sid, part))
            if os.path.exists(p):
                sheet.paste(Image.open(p).convert("RGB").resize((W, H)), (c * W, r * H))
        d.text((4, r * H + 4), sid, fill=(255, 255, 0))
    sheet.save(os.path.join(OUT, "sheet-%d.png" % (g // 6 + 1)))

extra = sorted(glob.glob(os.path.join(OUT, "stills", "*s.png")))
if extra:
    sheet = Image.new("RGB", (3 * W, ((len(extra) + 2) // 3) * H))
    d = ImageDraw.Draw(sheet)
    for i, p in enumerate(extra):
        x, y = (i % 3) * W, (i // 3) * H
        sheet.paste(Image.open(p).convert("RGB").resize((W, H)), (x, y))
        d.text((x + 4, y + 4), os.path.basename(p), fill=(255, 255, 0))
    sheet.save(os.path.join(OUT, "sheet-moments.png"))
print("sheets written")
