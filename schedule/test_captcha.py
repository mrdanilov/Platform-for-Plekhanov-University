"""Генератор проверки: арифметический пример, рисуемый прямо в PNG."""

import random
import struct
import zlib

from django.test import TestCase

from schedule import captcha


def read_png(data):
    """Разобрать PNG обратно: вернуть (width, height, строки пикселей)."""
    assert data[:8] == b'\x89PNG\r\n\x1a\n', 'битая сигнатура'
    pos = 8
    width = height = 0
    idat = b''
    while pos < len(data):
        length = struct.unpack('>I', data[pos:pos + 4])[0]
        tag = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + length]
        crc = struct.unpack('>I', data[pos + 8 + length:pos + 12 + length])[0]
        assert crc == zlib.crc32(tag + chunk) & 0xffffffff, f'CRC сбит в {tag}'
        if tag == b'IHDR':
            width, height, depth, color = struct.unpack('>IIBB', chunk[:10])
            assert depth == 8 and color == 2, 'ожидался 8-битный RGB'
        elif tag == b'IDAT':
            idat += chunk
        pos += 12 + length

    raw = zlib.decompress(idat)
    stride = width * 3 + 1
    rows = []
    for y in range(height):
        line = raw[y * stride + 1:(y + 1) * stride]
        assert raw[y * stride] == 0, 'поддерживается только фильтр None'
        rows.append(line)
    return width, height, rows


class QuestionTests(TestCase):
    def test_operations_and_answers_match(self):
        rng = random.Random(1)
        for _ in range(200):
            question, answer = captcha.new_question(rng)
            left, op, right = question.split()
            a, b = int(left), int(right)
            expected = {'+': a + b, '-': a - b, 'x': a * b}[op]
            self.assertEqual(int(answer), expected,
                             f'{question} -> {answer}, ждали {expected}')

    def test_answer_never_negative_or_zero(self):
        rng = random.Random(2)
        for _ in range(300):
            _question, answer = captcha.new_question(rng)
            self.assertGreaterEqual(int(answer), 2,
                                    '0 и 1 путают, а минус пугает')

    def test_no_subtraction_produces_negative(self):
        rng = random.Random(3)
        for _ in range(300):
            question, _answer = captcha.new_question(rng)
            left, op, right = question.split()
            if op == '-':
                self.assertGreaterEqual(int(left), int(right))

    def test_questions_vary(self):
        rng = random.Random(4)
        questions = {captcha.new_question(rng)[0] for _ in range(50)}
        self.assertGreater(len(questions), 10, 'примеры не должны повторяться')


class RenderTests(TestCase):
    def test_png_is_valid(self):
        data = captcha.render('7 + 8', random.Random(5))
        width, height, _rows = read_png(data)
        self.assertGreater(width, 200)
        self.assertGreater(height, 40)
        self.assertLess(height, 120, 'полоса не должна быть высокой')

    def test_no_pillow_needed(self):
        """PNG собирается стандартной библиотекой — без сторонних пакетов."""
        import sys
        self.assertNotIn('PIL', sys.modules)

    def test_size_stable_regardless_of_distortion(self):
        """Картинка должна занимать предсказуемое место в вёрстке."""
        rng = random.Random(6)
        sizes = {read_png(captcha.render('4 + 4', rng))[:2] for _ in range(15)}
        self.assertEqual(len(sizes), 1, f'размеры скачут: {sizes}')
        self.assertEqual(sizes.pop(), (350, 62))

    def test_text_is_readable(self):
        """Цифры должны читаться: много тёмных пикселей, а не вертикальные полосы.

        Проверяем структуру, а не глазками: у горизонтального штриха цифры
        должно быть много тёмных пикселей в одной строке.
        """
        data = captcha.render('8', random.Random(7))
        _w, _h, rows = read_png(data)
        dark_rows = [
            sum(1 for i in range(0, len(row), 3) if row[i] < 140)
            for row in rows
        ]
        widest = max(dark_rows)
        self.assertGreater(widest, 8,
                           'нет широкой строки — цифра рассыпалась в полосы')

    def test_background_is_light(self):
        _w, _h, rows = read_png(captcha.render('1 + 1', random.Random(8)))
        sample = rows[0]
        self.assertGreater(sample[0], 200, 'фон должен быть светлым')


class SessionFlowTests(TestCase):
    def setUp(self):
        self.session = self.client.session

    def test_issue_then_verify(self):
        token = captcha.issue(self.session)
        data = self.session[captcha.SESSION_KEY]
        self.assertTrue(captcha.verify(self.session, token, data['answer']))

    def test_answer_tolerates_spaces(self):
        token = captcha.issue(self.session)
        answer = self.session[captcha.SESSION_KEY]['answer']
        self.assertTrue(captcha.verify(self.session, token, f'  {answer} '))

    def test_wrong_answer_fails(self):
        token = captcha.issue(self.session)
        answer = self.session[captcha.SESSION_KEY]['answer']
        wrong = '0' if answer != '0' else '1'
        self.assertFalse(captcha.verify(self.session, token, wrong))

    def test_challenge_consumed_after_verify(self):
        """Вопрос одноразовый: после попытки его больше нет в сессии."""
        token = captcha.issue(self.session)
        answer = self.session[captcha.SESSION_KEY]['answer']
        captcha.verify(self.session, token, answer)
        self.assertNotIn(captcha.SESSION_KEY, self.session)

    def test_same_token_not_accepted_twice(self):
        token = captcha.issue(self.session)
        answer = self.session[captcha.SESSION_KEY]['answer']
        self.assertTrue(captcha.verify(self.session, token, answer))
        self.assertFalse(captcha.verify(self.session, token, answer),
                         'старую картинку нельзя переиспользовать')

    def test_empty_session_gets_new_question(self):
        self.assertNotIn(captcha.SESSION_KEY, self.session)
        token, question = captcha.current(self.session)
        self.assertTrue(token)
        self.assertIn(captcha.SESSION_KEY, self.session)
        self.assertEqual(question, self.session[captcha.SESSION_KEY]['question'])

    def test_verify_without_session_fails(self):
        self.assertFalse(captcha.verify(self.session, 'любой', '7'))