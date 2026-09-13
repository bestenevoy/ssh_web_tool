"""生成 SSH Web Tool 的桌面图标和程序图标"""

import os

from PIL import Image, ImageDraw, ImageFont


def create_icon():
    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 背景圆角矩形
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=48, fill=(10, 14, 26, 255))

    # 终端窗口外框
    draw.rounded_rectangle([32, 56, 224, 200], radius=12, fill=(13, 19, 32, 255), outline=(233, 69, 96, 255), width=3)

    # 顶部栏
    draw.rounded_rectangle([32, 56, 224, 84], radius=12, fill=(26, 35, 50, 255))
    draw.rectangle([32, 72, 224, 84], fill=(26, 35, 50, 255))

    # 三个圆点（窗口按钮）
    draw.ellipse([47, 65, 57, 75], fill=(233, 69, 96, 255))
    draw.ellipse([63, 65, 73, 75], fill=(255, 193, 7, 255))
    draw.ellipse([79, 65, 89, 75], fill=(76, 175, 80, 255))

    # 字体
    font_large = None
    font_small = None
    for path in ["consola.ttf", "C:/Windows/Fonts/consola.ttf", "C:/Windows/Fonts/cour.ttf"]:
        try:
            font_large = ImageFont.truetype(path, 28)
            font_small = ImageFont.truetype(path, 16)
            break
        except Exception:
            continue
    if font_large is None:
        font_large = ImageFont.load_default()
        font_small = ImageFont.load_default()

    # > 提示符
    draw.text((52, 100), ">", fill=(76, 175, 80, 255), font=font_large)
    draw.text((80, 100), "_", fill=(200, 208, 224, 255), font=font_large)

    # 底部线
    draw.line([52, 160, 204, 160], fill=(42, 48, 64, 255), width=2)
    draw.text((52, 168), "ssh user@host", fill=(136, 146, 176, 255), font=font_small)

    # 闪电装饰
    lightning = [(200, 32), (210, 48), (202, 48), (212, 64), (200, 48), (208, 48)]
    draw.polygon(lightning, fill=(233, 69, 96, 200))

    # 保存 ICO（多尺寸）
    icon_sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    out_dir = os.path.join(os.path.dirname(__file__), "..", "static")
    img.save(os.path.join(out_dir, "app_icon.ico"), format="ICO", sizes=icon_sizes)
    img.save(os.path.join(out_dir, "app_icon.png"), format="PNG")

    print("Icon files generated:")
    print("  static/app_icon.ico")
    print("  static/app_icon.png")


if __name__ == "__main__":
    create_icon()
