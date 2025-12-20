from main import parse_grades_from_html, parse_news_from_html


def test_parse_grades_from_html_simple():
    html = """
    Виписка оцінок
    Оберіть діапазон дат:
    2025-08-21
    по
    2025-12-31

    1	Англійська мова	6, 7, 6, Н, 8
    2	Інформатика	9, 10, Н
    """
    sd, ed, subs = parse_grades_from_html(html)
    assert sd == '2025-08-21'
    assert ed == '2025-12-31'
    assert 'Англійська мова' in subs
    assert subs['Англійська мова'][0].startswith('6')
    assert 'Інформатика' in subs
    assert len(subs['Інформатика']) == 3


def test_parse_news_from_html_simple():
    html = """
    Мої новини
    Іванов Іван Іванович 19 грудня о 10:06 Ви отримали оцінку 7 з предмету: Німецька мова, Семестрова
    """
    items = parse_news_from_html(html)
    assert isinstance(items, list)
    assert len(items) >= 1
    found = False
    for it in items:
        if it.get('subject') and 'Німецька' in it.get('subject'):
            found = True
            assert it.get('grade') in ('7','7')
    assert found