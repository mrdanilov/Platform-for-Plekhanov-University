"""Готовит варианты логотипа для шапки.

Нужен только для ручной перегенерации файлов, в приложение не входит.
Запуск:  python -m pip install pillow && python tools/make_logo.py

Исходник — Pictures\\плеханова_logo.png (536x302, прозрачный фон).
Полный лок-ап содержит две мелкие строки подписи, которые в шапке
нечитаемы, поэтому надпись берётся отдельно и перекрашивается в белый:
тёмно-синие буквы на синей полосе не видны, а белые видны — и логотип
лежит прямо на фоне, без светлой «плашки» под ним.

  plekhanova_emblem.png          — герб (цветной)
  plekhanova_wordmark_white.png  — «РЭУ.РФ» белым, для тёмной полосы
  plekhanova_wordmark.png        — «РЭУ.РФ» тёмно-синим, для светлого фона
  plekhanova_logo.png            — весь лок-ап, для печати и крупных блоков
"""

from pathlib import Path

from PIL import Image

BASE = Path(__file__).resolve().parent.parent / 'schedule' / 'static' / 'schedule' / 'img'

# Границы блоков внутри исходника 536x302. Измерены по профилю непрозрачности:
# герб кончается на x=224, крупное слово «РЭУ.РФ» занимает x 225..508, y 89..148,
# две мелкие строки подписи идут ниже (y 155..200) и в шапке не читаются.
FULL_BOX = (52, 48, 508, 236)       # весь лок-ап
EMBLEM_BOX = (52, 48, 210, 236)     # герб
WORDMARK_BOX = (222, 86, 510, 150)  # «РЭУ.РФ» без подписи и линейки


def trim(image, box, padding=2):
    crop = image.crop(box)
    alpha = crop.getchannel('A')
    content = alpha.getbbox() or (0, 0, crop.width, crop.height)
    left, top, right, bottom = content
    left = max(0, left - padding)
    top = max(0, top - padding)
    right = min(crop.width, right + padding)
    bottom = min(crop.height, bottom + padding)
    return crop.crop((left, top, right, bottom))


def white_silhouette(image):
    """Белая версия: цвет заменён на белый, прозрательность сохранена."""
    alpha = image.getchannel('A')
    out = Image.new('RGBA', image.size, (255, 255, 255, 0))
    out.putalpha(alpha)
    return out


def save(image, name):
    path = BASE / name
    image.save(path, 'PNG', optimize=True)
    kb = path.stat().st_size / 1024
    print(f'  {name:32} {image.width}x{image.height}  {kb:.1f} КБ')


def main():
    # всегда перечитываем исходник из папки проекта, а не перезаписываем его
    source = Image.open(BASE / 'plekhanova_logo.png').convert('RGBA')
    print('исходник:', source.size)

    emblem = trim(source, EMBLEM_BOX)
    wordmark = trim(source, WORDMARK_BOX)

    print('сохранено:')
    save(emblem, 'plekhanova_emblem.png')
    save(white_silhouette(wordmark), 'plekhanova_wordmark_white.png')
    save(wordmark, 'plekhanova_wordmark.png')
    save(trim(source, FULL_BOX), 'plekhanova_logo.png')


if __name__ == '__main__':
    main()