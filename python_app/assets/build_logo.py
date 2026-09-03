from PIL import Image, ImageDraw
from pathlib import Path

out = Path(__file__).parent
size = 1024
image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
d = ImageDraw.Draw(image)

def s(v):
    return int(v * size / 256)

d.rounded_rectangle((0, 0, size, size), radius=s(54), fill="#1f4f9d")
d.rounded_rectangle((s(45), s(43), s(195), s(213)), radius=s(16), fill="#ffffff")
d.polygon([(s(153), s(43)), (s(195), s(85)), (s(153), s(85))], fill="#dce9ff")
for y, x2 in ((109, 153), (133, 125), (157, 111)):
    d.line((s(75), s(y), s(x2), s(y)), fill="#9eb8e8", width=s(10))
d.ellipse((s(120), s(123), s(206), s(209)), fill="#21b6a8")
d.line((s(143), s(166), s(157), s(180), s(186), s(148)), fill="#ffffff", width=s(11), joint="curve")
d.line((s(185), s(112), s(218), s(112), s(218), s(145)), fill="#ffcf66", width=s(9), joint="curve")
d.line((s(218), s(112), s(179), s(151)), fill="#ffcf66", width=s(9))
image.save(out / "touleme-logo.png")
image.save(out / "touleme-logo.ico", sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
