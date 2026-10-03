"""Простая капча: арифметический пример, нарисованный прямо в PNG.

Зачем своя, а не reCAPTCHA: reCAPTCHA требует ключи сайта и обращается к
Google при каждой проверке. Здесь ничего внешнего нет — картинка рисуется
на сервере средствами стандартной библиотеки, Pillow не нужен.

Как устроено:
  * вопрос и правильный ответ живут в сессии, клиент знает только картинку;
  * картинка одноразовая: после проверки вопрос удаляется, при ошибке
    выдаётся новый, иначе один и тот же пример можно подбирать перебором;
  * ответ сравнивается на сервере, на клиенте ничего не проверяется.
"""

import math
import random
import struct
import zlib
from uuid import uuid4

# Шрифт 5x7 — побитово, по одной строке на ряд. Этого хватает для цифр
# и знаков операций, а картинка всё равно проходит через искажение.
GLYPHS = {
    '0': ['01110', '10001', '10011', '10101', '11001', '10001', '01110'],
    '1': ['00100', '01100', '00100', '00100', '00100', '00100', '01110'],
    '2': ['01110', '10001', '00001', '00010', '00100', '01000', '11111'],
    '3': ['11111', '00010', '00100', '00010', '00001', '10001', '01110'],
    '4': ['00010', '00110', '01010', '10010', '11111', '00010', '00010'],
    '5': ['11111', '10000', '11110', '00001', '00001', '10001', '01110'],
    '6': ['00110', '01000', '10000', '11110', '10001', '10001', '01110'],
    '7': ['11111', '00001', '00010', '00100', '01000', '01000', '01000'],
    '8': ['01110', '10001', '10001', '01110', '10001', '10001', '01110'],
    '9': ['01110', '10001', '10001', '01111', '00001', '00010', '01100'],
    '+': ['00000', '00100', '00100', '11111', '00100', '00100', '00000'],
    '-': ['00000', '00000', '00000', '11111', '00000', '00000', '00000'],
    'x': ['00000', '10001', '01010', '00100', '01010', '10001', '00000'],
    '=': ['00000', '00000', '11111', '00000', '11111', '00000', '00000'],
}

SCALE = 6
GLYPH_W, GLYPH_H = 5, 7
GAP = 6
PADDING = 10

SESSION_KEY = 'uni_captcha'

# цвета: чернила и бумага. Тёмное на светлом — контраст выше 7:1
INK = (24, 38, 56)
PAPER = (238, 243, 249)
INKS = [(24, 38, 56), (30, 74, 140), (122, 32, 40), (18, 92, 66)]


def _blank(width, height, colour):
    row = bytes(colour) * width
    return [bytearray(row) for _ in range(height)]


def _png(width, height, rows):
    """Собрать PNG из строк RGB. Только zlib и struct — без внешних библиотек."""
    raw = b''.join(b'\x00' + bytes(row) for row in rows)

    def chunk(tag, data):
        body = tag + data
        return (struct.pack('>I', len(data)) + body
                + struct.pack('>I', zlib.crc32(body) & 0xffffffff))

    header = struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', header)
            + chunk(b'IDAT', zlib.compress(raw, 9))
            + chunk(b'IEND', b''))


def new_question(rng=None):
    """Новый пример. Возвращает (текст для картинки, строковый ответ).

    Ответ всегда не меньше 2: ноль с буквой O путают, а единица легко
    угадывается. Перебираем именно операнды, а не правим ответ — иначе
    на картинке было бы «2 − 2», а принимался бы 1.
    """
    rng = rng or random
    op = rng.choice(('+', '-', 'x'))
    while True:
        a = rng.randint(2, 9)
        b = rng.randint(2, 9)
        if op == '-':
            a, b = max(a, b), min(a, b)   # чтобы ответ не был отрицательным
        if op == '+':
            answer = a + b
        elif op == '-':
            answer = a - b
        else:
            answer = a * b
        if answer >= 2:
            return f'{a} {op} {b}', str(answer)


def render(question, rng=None):
    """PNG с примером.

    Искажение намеренно слабое: человек должен прочитать пример с первого
    раза, а автоматический разбор — нет. Поэтому ряды смещаются меньше
    высоты одного штриха, а не «рвут» цифры на полосы.
    """
    rng = rng or random
    text_w = sum((GLYPH_W + GAP) * SCALE for _ in question)
    width = text_w + PADDING * 2
    height = GLYPH_H * SCALE + PADDING * 2
    rows = _blank(width, height, PAPER)

    # лёгкий наклон строк: амплитуда меньше высоты штриха, иначе
    # горизонтальные черты цифр разъезжаются и пример становится нечитаемым
    phase = rng.uniform(0, 2 * math.pi)
    amplitude = rng.uniform(0.3, 1.1)
    wave_len = rng.uniform(18, 30)
    for y in range(height):
        shift = int(round(amplitude * math.sin(2 * math.pi * y / wave_len + phase)))
        if shift:
            rows[y] = rows[y][-shift * 3:] + rows[y][:-shift * 3]

    # разброс знаков по вертикали и свой цвет у каждого — как в настоящих
    # капчах, но без потери читаемости
    x = PADDING
    for char in question:
        if char == ' ':
            x += (GLYPH_W + GAP) * SCALE
            continue
        glyph = GLYPHS.get(char)
        if glyph is None:
            x += (GLYPH_W + GAP) * SCALE
            continue
        colour = random.choice(INKS)
        dy = rng.randint(-2, 2)
        for gy, line in enumerate(glyph):
            for gx, bit in enumerate(line):
                if bit != '1':
                    continue
                for dsy in range(SCALE):
                    py = PADDING + gy * SCALE + dsy + dy
                    for dsx in range(SCALE):
                        px = x + gx * SCALE + dsx
                        if 0 <= py < height and 0 <= px < width:
                            rows[py][px * 3:px * 3 + 3] = bytes(colour)
        x += (GLYPH_W + GAP) * SCALE

    # фоновая текстура: блёклые точки и тонкие линии, цифры не затирают
    for _ in range(width * height // 90):
        px = rng.randrange(width)
        py = rng.randrange(height)
        rows[py][px * 3:px * 3 + 3] = bytes((222, 231, 241))
    for _ in range(rng.randint(1, 2)):
        y0 = rng.randrange(height)
        rows[y0] = bytearray(bytes((203, 218, 234)) * width)

    return _png(width, height, rows)


# ------------------------------------------------------------- работа с сессией

def issue(session, rng=None):
    """Выдать новый вопрос и положить ответ в сессию. Возвращает id."""
    question, answer = new_question(rng)
    token = uuid4().hex[:12]
    session[SESSION_KEY] = {'token': token, 'question': question, 'answer': answer}
    session.modified = True
    return token


def current(session):
    """Текущий вопрос, иначе новый. Возвращает (token, question)."""
    data = session.get(SESSION_KEY)
    if not data or 'question' not in data:
        token = issue(session)
        data = session[SESSION_KEY]
    return data['token'], data['question']


def verify(session, token, answer):
    """Проверить ответ. Вопрос одноразовый — удаляется в любом случае.

    Возвращает True только если токен совпал и ответ верен.
    """
    data = session.get(SESSION_KEY) or {}
    expected = data.get('answer')
    given = (answer or '').strip().replace(' ', '').replace('−', '-')
    good = bool(data) and token == data.get('token') and expected == given
    session.pop(SESSION_KEY, None)
    session.modified = True
    return good