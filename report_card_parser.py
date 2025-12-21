from bs4 import BeautifulSoup


def parse_report_card(html: str) -> list[dict]:
    """Парсить табель успішності з HTML сторінки NZ.UA"""
    soup = BeautifulSoup(html, 'html.parser')
    
    h2 = soup.find('h2', string=lambda t: t and 'Табель' in t)
    if not h2:
        return []
    
    table = h2.find_next('table')
    if not table:
        return []
    
    skip_texts = {
        'iнваріантна складова',
        'інваріантна складова', 
        'варіативна складова',
        'кількість пропущених навчальних днів',
        'з них через хворобу',
        'підпис класного керівника',
        'підпис батьків',
        'рішення педагогічної ради',
        'предмети',
        'бали',
    }
    
    results = []
    rows = table.find_all('tr')
    
    for row in rows:
        cells = row.find_all(['th', 'td'])
        if len(cells) < 2:
            continue
        
        subject = cells[0].get_text(strip=True)
        
        if not subject:
            continue
        
        subject_lower = subject.lower()
        if any(skip in subject_lower for skip in skip_texts):
            continue
        
        # Пропускаем заголовки колонок
        if subject_lower in ('1 семестр', '2 семестр', 'річні', 'підсумкові'):
            continue
        
        semester_1 = cells[1].get_text(strip=True)
        
        # Если оценки нет — пишем "немає"
        if not semester_1 or not semester_1.isdigit():
            semester_1 = "немає"
        
        results.append({
            'subject': subject,
            'semester_1': semester_1
        })
    
    return results
