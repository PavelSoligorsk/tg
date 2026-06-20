import os
import io
import re
import base64
import uuid
import subprocess
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import httpx
import markdown  
from bs4 import BeautifulSoup
from PIL import Image, ImageChops

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в переменных окружения / .env файле!")

app = FastAPI(title="KaTeX Premium Render Bot API")
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

GLOBAL_ASSETS = {"css": "", "js": "", "auto_js": ""}

@app.on_event("startup")
async def load_katex_assets():
    try:
        async with httpx.AsyncClient() as client:
            res_css = await client.get("https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css")
            res_js = await client.get("https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js")
            res_auto = await client.get("https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/contrib/auto-render.min.js")
            
            if all(r.status_code == 200 for r in [res_css, res_js, res_auto]):
                # --- ИСПРАВЛЕНИЕ ЗДЕСЬ ---
                # Меняем локальные пути шрифтов на абсолютные с серверов CDN
                fixed_css = res_css.text.replace('url(fonts/', 'url(https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/fonts/')
                
                GLOBAL_ASSETS["css"] = fixed_css
                GLOBAL_ASSETS["js"] = res_js.text
                GLOBAL_ASSETS["auto_js"] = res_auto.text
                print("УСПЕХ: Локальный кэш KaTeX успешно инициализирован.")
    except Exception as e:
        print(f"ВНИМАНИЕ: Ошибка кэширования ресурсов: {e}")

def get_chromium_path():
    if os.getenv("CHROMIUM_PATH"):
        return os.getenv("CHROMIUM_PATH")
    paths = [
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
        "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
    ]
    for path in paths:
        if os.path.exists(path):
            return path
    return None

CHROMIUM_FLAGS = [
    "--headless", "--no-sandbox", "--disable-setuid-sandbox",
    "--disable-gpu", "--disable-dev-shm-usage", "--disable-software-rasterizer",
    "--hide-scrollbars", "--disable-dbus", "--no-zygote", "--default-background-color=eef2f3",
    "--force-device-scale-factor=2",
    "--virtual-time-budget=1000"  # <--- Добавили вот это
]

class MathMessage(BaseModel):
    chat_id: int | str
    caption: str = ""
    latex: str
    is_quiz: bool = False
    options: list[str] = []
    correct_option_ids: list[int] = [] 
    difficulty: int
    answer: str = ""  # Добавляем поле ответа (для вывода в открытых вопросах)

def extract_and_format_badge(text: str) -> tuple[str, str]:
    match = re.search(r'^(?:<strong>)?([А-Яа-яA-Za-z][-–]?\d+)\.?(?:</strong>)?\s*', text)
    if match:
        badge_text = match.group(1)
        return text[match.end():].strip(), f'<div class="task-badge">{badge_text}</div>'
    return text, ""

async def convert_to_katex_html(raw_text: str, options: list[str]) -> tuple[str, bool]:
    raw_text, badge_html = extract_and_format_badge(raw_text)
    raw_text = raw_text.replace('\\n', '\n')

    # --- ЗАЩИТА LATEX ---
    latex_blocks = []
    def placeholder_repl(match):
        latex_blocks.append(match.group(0))
        # Используем безопасный текстовый маркер вместо HTML-комментариев
        return f"@@LATEX_BLOCK_{len(latex_blocks)-1}@@"
    
    protected_text = re.sub(r'\$\$.*?\$\$', placeholder_repl, raw_text, flags=re.DOTALL)
    protected_text = re.sub(r'\$.*?\$', placeholder_repl, protected_text)

    # Отбивка таблиц
    lines = protected_text.split('\n')
    processed_lines = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith('|'):
            if i > 0 and processed_lines[-1].strip() != '' and not processed_lines[-1].strip().startswith('|'):
                processed_lines.append('')
        elif i > 0 and lines[i-1].strip().startswith('|') and stripped != '':
            processed_lines.append('')
        processed_lines.append(line)

    protected_text = '\n'.join(processed_lines)

    # Парсим Markdown
    html_content = markdown.markdown(protected_text, extensions=['tables'])
    
    # ВОЗВРАЩАЕМ ФОРМУЛЫ ОБРАТНО
    for idx, block in enumerate(latex_blocks):
        html_content = html_content.replace(f"@@LATEX_BLOCK_{idx}@@", block)

    has_image = "img" in html_content or "<table" in html_content or len(options) > 0

    soup = BeautifulSoup(html_content, "html.parser")
    
    async with httpx.AsyncClient() as client:
        for img in soup.find_all("img"):
            src = img.get("src", "")
            if src.startswith("http"):
                try:
                    resp = await client.get(src, timeout=12.0)
                    if resp.status_code == 200:
                        encoded = base64.b64encode(resp.content).decode('utf-8')
                        mime = "image/png" if "png" in src.lower() else "image/jpeg"
                        img["src"] = f"data:{mime};base64,{encoded}"
                        img["class"] = "task-rendered-img"
                except Exception as e:
                    print(f"Ошибка скачивания картинки {src}: {e}")

    text_content = soup.decode_contents()
    
    options_html = ""
    if options:
        options_html = '<div class="options-grid">'
        markers = ["А", "Б", "В", "Г", "Д", "Е", "Ж", "З", "И", "К"]
        for idx, opt in enumerate(options):
            marker = markers[idx] if idx < len(markers) else f"{idx + 1}"
            
            # ТАКАЯ ЖЕ ЗАЩИТА ДЛЯ ВАРИАНТОВ ОТВЕТОВ
            opt_latex_blocks = []
            def opt_repl(m):
                opt_latex_blocks.append(m.group(0))
                return f"@@OPT_BLOCK_{len(opt_latex_blocks)-1}@@"
            
            p_opt = re.sub(r'\$\$.*?\$\$', opt_repl, opt, flags=re.DOTALL)
            p_opt = re.sub(r'\$.*?\$', opt_repl, p_opt)
            
            opt_html = markdown.markdown(p_opt)
            opt_html = re.sub(r'^<p>|</p>$', '', opt_html).strip()
            
            # ВОЗВРАЩАЕМ ФОРМУЛЫ В ВАРИАНТЫ
            for o_idx, o_block in enumerate(opt_latex_blocks):
                opt_html = opt_html.replace(f"@@OPT_BLOCK_{o_idx}@@", o_block)
            
            options_html += f"""
            <div class="option-item">
                <span class="option-marker">{marker}</span>
                <span class="option-text">{opt_html}</span>
            </div>
            """
        options_html += '</div>'

    css_include = f"<style>{GLOBAL_ASSETS['css']}</style>" if GLOBAL_ASSETS['css'] else '<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css">'
    js_include = f"<script>{GLOBAL_ASSETS['js']}</script>" if GLOBAL_ASSETS['js'] else '<script src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js"></script>'
    auto_render_include = f"<script>{GLOBAL_ASSETS['auto_js']}</script>" if GLOBAL_ASSETS['auto_js'] else '<script src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/contrib/auto-render.min.js"></script>'

    html_template = f"""
    <!DOCTYPE html>
    <html style="background-color: #f1f5f9; margin: 0; padding: 0; box-sizing: border-box;">
    <head>
        <meta charset="utf-8">
        {css_include} {js_include} {auto_render_include}
        <style>
            *, *:before, *:after {{ box-sizing: inherit; }}
            
            /* Главный холст: приятный софт-фон, убрали лишние зазоры */
            body {{ 
                margin: 0; 
                padding: 35px; 
                display: flex; 
                justify-content: center; 
                align-items: flex-start; 
                background-color: #f1f5f9; 
                width: 740px; 
            }}
            
            /* Белая карточка: сделали тень глубже, а углы чуть более стильными */
            .card {{ 
                font-family: 'Inter', system-ui, -apple-system, sans-serif; 
                background-color: #ffffff; 
                padding: 36px; 
                border-radius: 20px; 
                box-shadow: 0 12px 30px rgba(15, 23, 42, 0.06); 
                width: 100%; 
                max-width: 670px; 
                display: flex; 
                flex-direction: column; 
                gap: 28px; 
                border: 1px solid rgba(226, 232, 240, 0.8); 
                word-wrap: break-word; 
            }}
            
            /* Бейдж: добавили скругление, современный градиент и легкое свечение */
            .task-badge {{ 
                align-self: flex-start; 
                background: linear-gradient(135deg, #3b82f6, #1d4ed8); 
                color: #ffffff; 
                font-weight: 700; 
                font-size: 15px; 
                letter-spacing: 0.5px;
                padding: 6px 16px; 
                border-radius: 10px; 
                text-transform: uppercase; 
                box-shadow: 0 4px 12px rgba(37, 99, 235, 0.2);
            }}
            
            /* Контейнер текста: глубокий Slate-цвет, идеальный размер для чтения */
            .text-container {{ 
                font-size: 22px; 
                line-height: 1.7; 
                color: #0f172a; 
                font-weight: 400; 
            }}
            
            /* Таблицы: сделали полностью плоскими и чистыми, убрали "грязные" контуры */
            table {{ 
                width: 100%; 
                border-collapse: collapse; 
                margin: 24px 0; 
                font-size: 19px; 
                background-color: #f8fafc; 
                border-radius: 14px; 
                overflow: hidden; 
                border: 1px solid #e2e8f0; 
                table-layout: fixed; 
            }}
            th, td {{ 
                padding: 16px; 
                vertical-align: middle; 
                text-align: center; 
                border: 1px solid #e2e8f0; 
                word-wrap: break-word; 
            }}
            th {{ background-color: #f1f5f9; color: #0f172a; font-weight: 700; }}
            td {{ background-color: #ffffff; color: #334155; }}
            
            /* Рендеринг картинок внутри задачи */
            .task-rendered-img {{ 
                display: block; 
                max-width: 100%; 
                max-height: 420px; 
                width: auto; 
                height: auto; 
                border-radius: 12px; 
                margin: 16px auto; 
                object-fit: contain; 
            }}
            td .task-rendered-img {{ max-height: 140px; margin: 4px auto; border-radius: 6px; }}
            td p {{ margin: 0; }}
            
            /* Сетка вариантов: аккуратный разделитель */
            .options-grid {{ 
                display: flex; 
                flex-direction: column; 
                gap: 14px; 
                margin-top: 4px; 
                border-top: 2px dashed #f1f5f9; 
                padding-top: 24px; 
            }}
            
            /* Элемент ответа: сделали фон белым, добавили легкую рамку — выглядит премиально */
            .option-item {{ 
                display: flex; 
                align-items: center; 
                gap: 16px; 
                padding: 14px 20px; 
                background-color: #ffffff; 
                border: 1px solid #e2e8f0; 
                border-radius: 12px; 
                font-size: 20px; 
                color: #334155; 
                box-shadow: 0 2px 4px rgba(0, 0, 0, 0.01);
            }}
            
            /* Маркеры (А, Б, В...): стильный контрастный кружок */
            .option-marker {{ 
                display: flex; 
                align-items: center; 
                justify-content: center; 
                width: 36px; 
                height: 36px; 
                background-color: #f1f5f9; 
                color: #2563eb; 
                font-weight: 700; 
                border-radius: 50%; 
                font-size: 16px; 
                flex-shrink: 0; 
            }}
            .option-text p {{ margin: 0; }}
            
            /* НАСТРОЙКА KATEX: формулы чуть крупнее, четче цвет + фикс вертикальных линий */
            .katex {{ 
                font-size: 1.08em; 
                color: #020617; 
            }}
            /* Убираем любые косяки со смещением скобок в системах уравнений */
            .katex .brace {{
                font-family: KaTeX_Main, 'Courier New', monospace !important;
            }}
        </style>
    </head>
    <body>
        <div class="card" id="math-root">
            {badge_html}
            <div class="text-container" id="math-content">{text_content}</div>
            {options_html}
        </div>
        <script>
            renderMathInElement(document.getElementById("math-root"), {{
                delimiters: [
                    {{left: "$$", right: "$$", display: true}},
                    {{left: "$", right: "$", display: false}}
                ],
                throwOnError : false
            }});
        </script>
    </body>
    </html>
    """

    return html_template, has_image

def autocrop_image(img_path: str) -> bytes:
    img = Image.open(img_path).convert("RGB")
    bg = Image.new(img.mode, img.size, (238, 242, 243))
    diff = ImageChops.difference(img, bg)
    diff = ImageChops.add(diff, diff, 2.0, -100)
    bbox = diff.getbbox()
    img_cropped = img.crop((max(0, bbox[0]-20), max(0, bbox[1]-20), min(img.size[0], bbox[2]+20), min(img.size[1], bbox[3]+20))) if bbox else img
    output = io.BytesIO()
    img_cropped.save(output, format="PNG")
    return output.getvalue()

@app.post("/send_math")
async def send_math(msg: MathMessage):
    unique_id = uuid.uuid4().hex
    html_file = f"/tmp/task_{unique_id}.html"
    img_file = f"/tmp/task_{unique_id}.png"

    try:
        html_code, has_image = await convert_to_katex_html(msg.latex, msg.options)
        
        with open(html_file, "w", encoding="utf-8") as f:
            f.write(html_code)
            
        canvas_width = 740
        canvas_height = 3200 if has_image else 1200
        browser_exec = get_chromium_path()
        if not browser_exec: raise RuntimeError("Chromium не найден!")

        cmd = [browser_exec, *CHROMIUM_FLAGS, f"--window-size={canvas_width},{canvas_height}", f"--screenshot={img_file}", html_file]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        
        if result.returncode != 0 or not os.path.exists(img_file):
            raise RuntimeError(f"Сбой Chromium: {result.stderr}")
            
        img_bytes = autocrop_image(img_file)
        
        # --- МОДИФИКАЦИЯ CAPTION (Вывод ответов) ---
        final_caption = msg.caption
        markers = ["А", "Б", "В", "Г", "Д", "Е", "Ж", "З", "И", "К"]
        
        if not msg.is_quiz:
            # Для открытых вопросов пишем ответ снизу
            if msg.answer:
                final_caption += f"\n\n🔑 **Правильный ответ:** {msg.answer}"
        else:
            # Для закрытых вопросов с множественным выбором (более 1 ответа)
            if len(msg.correct_option_ids) > 1:
                correct_letters = [markers[i] for i in msg.correct_option_ids if i < len(markers)]
                final_caption += f"\n\n✅ **Правильные варианты:** {', '.join(correct_letters)}"

        async with httpx.AsyncClient() as client:
            files = {"photo": ("task.png", img_bytes, "image/png")}
            photo_resp = await client.post(
                f"{TELEGRAM_API}/sendPhoto", 
                data={"chat_id": msg.chat_id, "caption": final_caption, "parse_mode": "Markdown"}, 
                files=files, 
                timeout=30.0
            )
            if photo_resp.status_code != 200: raise HTTPException(status_code=400, detail=photo_resp.text)
            photo_res_data = photo_resp.json()

            # --- ОТПРАВКА ОПРОСА ---
            if msg.is_quiz and msg.options:
                if 2 <= len(msg.options) <= 10:
                    clean_options = [opt[:100] for opt in msg.options] 
                    is_multiple = len(msg.correct_option_ids) > 1
                    poll_options = [f"Вариант {markers[i]}" for i in range(len(clean_options))]
                    
                    if is_multiple:
                        quiz_data = {
                            "chat_id": msg.chat_id,
                            "question": "Выберите правильные ответы (их несколько) 👇",
                            "options": poll_options,
                            "type": "regular",
                            "allows_multiple_answers": True,
                            "is_anonymous": True
                        }
                    else:
                        single_id = msg.correct_option_ids[0] if msg.correct_option_ids else 0
                        if single_id >= len(clean_options):
                            single_id = 0
                            
                        quiz_data = {
                            "chat_id": msg.chat_id,
                            "question": "Выберите правильный ответ 👇",
                            "options": poll_options,
                            "type": "quiz",
                            "correct_option_id": single_id,
                            "is_anonymous": True
                        }
                    
                    quiz_resp = await client.post(f"{TELEGRAM_API}/sendPoll", json=quiz_data, timeout=20.0)
                    if quiz_resp.status_code == 200:
                        photo_res_data["attached_quiz"] = quiz_resp.json()
                    else:
                        print(f"DEBUG Ошибка sendPoll API: {quiz_resp.text}")
                else:
                    print(f"ВНИМАНИЕ: Опрос пропущен. Количество вариантов ({len(msg.options)}) вне лимитов Telegram (2-10).")

            return {"status": "success", "telegram_response": photo_res_data}

    finally:
        if os.path.exists(html_file): os.remove(html_file)
        if os.path.exists(img_file): os.remove(img_file)

# === В ЭТОМ СЕРВИСЕ (рендеринг) ===
from fastapi.responses import Response
class ReportRequest(BaseModel):
    test: dict
    user: dict
    result: dict
    userAnswers: dict
    stats: dict
    drawings: dict = {}  # чертежи (base64 или URL)


@app.post("/render-report")
async def render_report(data: ReportRequest):
    """
    Принимает данные отчёта и возвращает готовый PDF.
    Использует Chromium/Puppeteer для рендеринга HTML → PDF.
    """
    
    # 1. Генерируем HTML по шаблону
    html_content = generate_report_html(data)
    
    # 2. Сохраняем во временный файл
    unique_id = uuid.uuid4().hex
    html_file = f"/tmp/report_{unique_id}.html"
    pdf_file = f"/tmp/report_{unique_id}.pdf"
    
    try:
        with open(html_file, "w", encoding="utf-8") as f:
            f.write(html_content)
        
        # 3. Рендерим через Chromium
        browser_exec = get_chromium_path()
        if not browser_exec:
            raise RuntimeError("Chromium не найден!")
        
        cmd = [
            browser_exec,
            "--headless",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--print-to-pdf=" + pdf_file,
            "--no-pdf-header-footer",
            html_file
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        
        if result.returncode != 0 or not os.path.exists(pdf_file):
            raise RuntimeError(f"Ошибка Chromium: {result.stderr}")
        
        # 4. Читаем PDF и возвращаем
        with open(pdf_file, "rb") as f:
            pdf_bytes = f.read()
        
        return Response(
            content=pdf_bytes,
            media_type="application/pdf"
        )
        
    finally:
        # Чистим временные файлы
        if os.path.exists(html_file):
            os.remove(html_file)
        if os.path.exists(pdf_file):
            os.remove(pdf_file)


def generate_report_html(data: ReportRequest) -> str:
    """
    Генерирует HTML для красивого отчёта.
    Использует те же стили, что и в TestReport.jsx.
    """
    
    test = data.test
    user = data.user
    result = data.result
    user_answers = data.userAnswers
    stats = data.stats
    drawings = data.drawings
    
    # Подсчёт статистики
    total = stats.get("total", len(test.get("tasks", [])))
    correct = stats.get("correct", 0)
    score_percentage = round((correct / total * 100), 1) if total > 0 else 0
    
    # Генерация карточек заданий
    tasks_html = ""
    for idx, task in enumerate(test.get("tasks", [])):
        answer = user_answers.get(str(task["id"]))
        drawing = drawings.get(str(task["id"]))
        
        is_unanswered = not answer
        is_correct = check_answer(task, answer)
        
        # Варианты ответов
        options_html = ""
        if not task.get("is_open_answer") and task.get("options"):
            options_html = '<div class="options-list">'
            for opt_idx, opt in enumerate(task["options"]):
                options_html += f'<div class="option">{opt_idx + 1}. {opt}</div>'
            options_html += '</div>'
        
        tasks_html += f"""
        <div class="task-card">
            <div class="task-header {'correct' if is_correct else 'incorrect' if not is_unanswered else 'unanswered'}">
                <span>Задание {idx + 1}</span>
                <span>{'✅ Верно' if is_correct else '❌ Неверно' if not is_unanswered else '⚠️ Нет ответа'}</span>
            </div>
            <div class="task-body">
                <div class="task-content">{task.get("content", "")}</div>
                {options_html}
                
                <div class="answers-grid">
                    <div class="answer-box user-answer">
                        <div class="answer-label">Ваш ответ:</div>
                        <div>{format_answer(task, answer) or '—'}</div>
                    </div>
                    <div class="answer-box correct-answer">
                        <div class="answer-label">Правильный ответ:</div>
                        <div>{format_correct_answer(task)}</div>
                    </div>
                </div>
                
                {f'<div class="drawing"><img src="{drawing}" /></div>' if drawing else ''}
                
                {f'<div class="ai-solution"><h4>🤖 Разбор от ИИ:</h4><div>{task.get("ai_solution", "")}</div></div>' if task.get("ai_solution") else ''}
            </div>
        </div>
        """
    
    # Полный HTML
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{ 
                font-family: 'Inter', system-ui, sans-serif; 
                padding: 40px; 
                background: white;
                color: #0f172a;
            }}
            
            .header {{ 
                border-bottom: 4px solid #0f172a; 
                padding-bottom: 30px; 
                margin-bottom: 30px;
                display: flex;
                justify-content: space-between;
                align-items: flex-end;
            }}
            .header h1 {{ font-size: 36px; font-weight: 900; }}
            .header .score {{ 
                font-size: 48px; 
                font-weight: 900; 
                color: #2563eb;
            }}
            
            .stats-grid {{
                display: grid;
                grid-template-columns: repeat(3, 1fr);
                gap: 16px;
                background: #f8fafc;
                padding: 24px;
                border-radius: 24px;
                margin-bottom: 40px;
            }}
            .stat-item {{
                text-align: center;
                padding: 16px;
                border-right: 1px solid #e2e8f0;
            }}
            .stat-item:last-child {{ border-right: none; }}
            .stat-value {{ font-size: 28px; font-weight: 900; }}
            .stat-label {{ font-size: 10px; font-weight: 700; text-transform: uppercase; color: #94a3b8; margin-top: 4px; }}
            
            .task-card {{
                border: 1px solid #e2e8f0;
                border-radius: 24px;
                overflow: hidden;
                margin-bottom: 24px;
            }}
            .task-header {{
                padding: 20px 24px;
                display: flex;
                justify-content: space-between;
                font-weight: 700;
                font-size: 14px;
                text-transform: uppercase;
            }}
            .task-header.correct {{ background: #f0fdf4; }}
            .task-header.incorrect {{ background: #fef2f2; }}
            .task-header.unanswered {{ background: #f8fafc; }}
            
            .task-body {{ padding: 32px; }}
            .task-content {{ font-size: 16px; line-height: 1.7; margin-bottom: 20px; }}
            
            .options-list {{ margin: 16px 0; }}
            .option {{ padding: 8px 0; font-size: 15px; }}
            
            .answers-grid {{
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 16px;
                margin-top: 24px;
            }}
            .answer-box {{
                padding: 20px;
                border-radius: 16px;
                border: 1px solid #e2e8f0;
            }}
            .answer-box.user-answer {{ background: #f8fafc; }}
            .answer-box.correct-answer {{ background: #eff6ff; border-color: #bfdbfe; }}
            .answer-label {{
                font-size: 10px;
                font-weight: 700;
                text-transform: uppercase;
                color: #94a3b8;
                margin-bottom: 8px;
            }}
            
            .drawing img {{
                max-width: 450px;
                max-height: 320px;
                margin: 24px auto;
                display: block;
                border-radius: 12px;
                border: 1px solid #e2e8f0;
            }}
            
            .ai-solution {{
                margin-top: 24px;
                padding: 24px;
                background: #f5f3ff;
                border: 1px solid #ddd6fe;
                border-radius: 16px;
            }}
            .ai-solution h4 {{ font-size: 12px; font-weight: 700; text-transform: uppercase; color: #7c3aed; margin-bottom: 12px; }}
            
            .footer {{
                text-align: center;
                color: #94a3b8;
                font-size: 12px;
                font-weight: 700;
                text-transform: uppercase;
                padding-top: 32px;
                border-top: 1px solid #e2e8f0;
                margin-top: 40px;
            }}
        </style>
    </head>
    <body>
        <div class="header">
            <div>
                <h1>{test.get("title", "Результаты теста")}</h1>
                <p style="color: #64748b; margin-top: 8px;">
                    {user.get("first_name", "")} {user.get("last_name", "")} • {result.get("completed_at", "")}
                </p>
            </div>
            <div class="score">{score_percentage}%</div>
        </div>
        
        <div class="stats-grid">
            <div class="stat-item">
                <div class="stat-value" style="color: #16a34a;">{correct}</div>
                <div class="stat-label">Правильных</div>
            </div>
            <div class="stat-item">
                <div class="stat-value" style="color: #dc2626;">{total - correct - (stats.get('unanswered', 0))}</div>
                <div class="stat-label">Ошибок</div>
            </div>
            <div class="stat-item">
                <div class="stat-value" style="color: #64748b;">{stats.get('unanswered', 0)}</div>
                <div class="stat-label">Пропущено</div>
            </div>
        </div>
        
        {tasks_html}
        
        <div class="footer">
            Отчёт сгенерирован автоматически • testers-production-46d5.up.railway.app
        </div>
    </body>
    </html>
    """
    
    return html


def check_answer(task: dict, user_answer) -> bool:
    """Проверка правильности ответа"""
    if not user_answer:
        return False
    
    if task.get("is_open_answer"):
        return str(user_answer).strip().lower() == str(task.get("answer", "")).strip().lower()
    else:
        correct = task.get("answer", [])
        if isinstance(correct, str):
            correct = [correct]
        user = user_answer if isinstance(user_answer, list) else [user_answer]
        return sorted(correct) == sorted(user)


def format_answer(task: dict, user_answer) -> str:
    """Форматирование ответа пользователя"""
    if not user_answer:
        return "—"
    
    if task.get("is_open_answer"):
        return str(user_answer)
    else:
        answers = user_answer if isinstance(user_answer, list) else [user_answer]
        return ", ".join(str(a) for a in answers)


def format_correct_answer(task: dict) -> str:
    """Форматирование правильного ответа"""
    answer = task.get("answer", "")
    
    if task.get("is_open_answer"):
        return str(answer)
    else:
        answers = answer if isinstance(answer, list) else [answer]
        if task.get("options"):
            return ", ".join(f"{a}. {task['options'][int(a)-1]}" for a in answers if a.isdigit() and int(a) <= len(task["options"]))
        return ", ".join(str(a) for a in answers)