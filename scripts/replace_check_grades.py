from pathlib import Path
p = Path('main.py')
s = p.read_text(encoding='utf-8', errors='replace')
start = s.find('\nasync def check_grades(')
if start == -1:
    print('start not found')
else:
    # find the 'finally:' that closes this function
    fin = s.find('\n    finally:\n        # Попробуем освободить память после интенсивной работы', start)
    if fin == -1:
        print('end not found')
    else:
        # find the end of that finally block (pass)
        pass_idx = s.find('\n', fin+1)
        # we'll include up to the 'pass' line after finally
        # find the line containing 'pass' after fin
        pass_line = s.find('\n            pass', fin)
        if pass_line == -1:
            end = fin
        else:
            # include until after 'pass' line
            end = s.find('\n', pass_line+1)
            if end == -1:
                end = len(s)

        new_func = '''
async def check_grades(context: ContextTypes.DEFAULT_TYPE):
    """Проверяет новые оценки для VIP-пользователей через новости и отправляет уведомления"""
    print("[VIP JOB] Checking grades from news")
    if 'GRADES_LOCK' in globals() and GRADES_LOCK is not None and GRADES_LOCK.locked():
        print("[VIP JOB] Grades job still running, skipping this round")
        return

    async with GRADES_LOCK:
        try:
            conn = get_db_connection()
            c = conn.cursor()
            c.execute('SELECT user_id, expires_at FROM vip_users WHERE expires_at > ?', (now_kyiv().isoformat(),))
            users = c.fetchall()
            conn.close()

            if not users:
                print("[VIP JOB] No active VIP users found")
                return

            print(f"[VIP JOB] Found {len(users)} active VIP users")

            for user in users:
                user_id = user[0]
                try:
                    session = get_session(user_id)
                    if not session:
                        continue

                    # Проверяем настройки уведомлений
                    notif_enabled = get_vip_setting(user_id, 'grade_notifications', '1') == '1'
                    if not notif_enabled:
                        print(f"[VIP JOB] User {user_id} has grade notifications disabled; skipping")
                        continue

                    # Получаем новости с оценками
                    from bs4 import BeautifulSoup
                    login_url = "https://nz.ua/login"
                    web_scraper = get_scraper()
                    try:
                        login_page = web_scraper.get(login_url)
                        login_html = login_page.text
                        try:
                            login_page.close()
                        except Exception:
                            pass
                    except Exception as e:
                        print(f"[VIP JOB] Error fetching login page for user {user_id}: {e}")
                        continue

                    login_soup = BeautifulSoup(login_html, "html.parser")
                    csrf = None
                    meta_csrf = login_soup.find('meta', attrs={'name': 'csrf-token'})
                    if meta_csrf:
                        csrf = meta_csrf.get('content')
                    hidden_csrf = login_soup.find('input', {'name': '_csrf'})
                    if hidden_csrf and hidden_csrf.get('value'):
                        csrf = hidden_csrf.get('value')

                    if not csrf:
                        print(f"[VIP JOB] Warning: Could not find CSRF token for user {user_id}")
                        continue

                    login_action = login_soup.find('form')
                    login_action = login_action.get('action') if login_action else login_url

                    payload = {
                        '_csrf': csrf,
                        'username': session.get('username'),
                        'password': session.get('password')
                    }

                    try:
                        news_resp = web_scraper.post(login_action, data=payload)
                        news_html = news_resp.text
                        try:
                            news_resp.close()
                        except Exception:
                            pass
                    except Exception as e:
                        print(f"[VIP JOB] Error fetching news for user {user_id}: {e}")
                        continue

                    # Простая логика парсинга новостей на предмет оценок
                    from bs4 import BeautifulSoup as BS
                    news_soup = BS(news_html, "html.parser")
                    news_items = news_soup.select('.news-item, .nz-news, .post, .article')
                    new_grades = []
                    for item_el in news_items:
                        try:
                            title = item_el.get_text(separator=' ', strip=True)
                            if 'оцін' in title or 'оцен' in title or 'grade' in title.lower():
                                teacher = item_el.select_one('.teacher, .author')
                                teacher_text = teacher.get_text(strip=True) if teacher else ''
                                date_el = item_el.select_one('.date, time')
                                date_text = date_el.get('datetime') if date_el and date_el.get('datetime') else (date_el.get_text(strip=True) if date_el else '')
                                import re
                                m = re.search(r"(\\d|[0-9]+)\\s*[-—:]?\\s*(оцін|оцен|grade)", title, re.IGNORECASE)
                                grade_value = m.group(1) if m else ''
                                subject = ''
                                new_grades.append({'teacher': teacher_text, 'date': date_text, 'grade': grade_value, 'subject': subject, 'type': '', 'is_changed': False, 'grade_key': f"{teacher_text}_{date_text}_{grade_value}"})
                        except Exception:
                            continue

                    if new_grades:
                        grade_dict = {}
                        for it in new_grades:
                            k = it.get('grade_key')
                            if k not in grade_dict:
                                grade_dict[k] = it
                            else:
                                if it.get('date', '') > grade_dict[k].get('date', ''):
                                    grade_dict[k] = it

                        unique_grades = list(grade_dict.values())
                        text_lines = ["📬 *Нові оцінки:*"]
                        for item in unique_grades[:10]:
                            teacher_name = item.get('teacher', '')
                            short_name = teacher_name
                            date_str = item.get('date', '')
                            grade = item.get('grade', '')
                            subject = item.get('subject', '')
                            formatted_type = format_grade_type(item.get('type', ''))
                            safe = lambda s: str(s).replace('*', '\\\\*').replace('_', '\\\\_') if s else s
                            text_lines.append(f"• {safe(short_name)} - {safe(date_str)}, поставила *{safe(grade)}* з _{safe(subject)}_, {safe(formatted_type)}")

                        try:
                            await context.bot.send_message(chat_id=user_id, text="\\n".join(text_lines), parse_mode=ParseMode.MARKDOWN)
                            print(f"[VIP JOB] Sent {len(unique_grades)} grade notifications to {user_id}")
                        except Exception as e:
                            print(f"[VIP JOB] Could not send grades to {user_id}: {e}")
                            continue

                        try:
                            conn = get_db_connection()
                            c = conn.cursor()
                            for it in unique_grades:
                                news_id = f"{it.get('grade_key')}_{it.get('date', '')}"
                                c.execute('INSERT OR IGNORE INTO last_news (news_id, title, content) VALUES (?, ?, ?)', (news_id, it.get('subject', ''), str(it)))
                            conn.commit()
                            conn.close()
                        except Exception as db_error:
                            print(f"[VIP JOB] Warning: Could not save grade notifications to DB for user {user_id}: {db_error}")
                    else:
                        print(f"[VIP JOB] No new grades for user {user_id}")

                except Exception as e:
                    print(f"[VIP JOB] Error checking news for user {user_id}: {e}")
                    import traceback
                    traceback.print_exc()
                    continue
        except Exception as e:
            print(f"[VIP JOB] Error in grades job: {e}")
            import traceback
            traceback.print_exc()
        finally:
            try:
                gc.collect()
            except Exception:
                pass'''

        new_s = s[:start+1] + new_func + s[end:]
        p.write_text(new_s, encoding='utf-8')
        print('Replaced check_grades function')
