from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.parse
import subprocess
import os
import json
import pandas as pd
import tempfile
from models.model import EssayEvaluator

class EssayHandler(BaseHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        # Инициализируем evaluator отдельно, чтобы избежать проблем с многопоточностью
        self._evaluator = None
        super().__init__(*args, **kwargs)
    
    @property
    def evaluator(self):
        if self._evaluator is None:
            self._evaluator = EssayEvaluator()
        return self._evaluator
    
    def send_error_to_start(self, error_message):
        """Перенаправляет на стартовую страницу с сообщением об ошибке"""
        # Читаем start.html
        with open('templates/start.html', 'r', encoding='utf-8') as f:
            html_content = f.read()
    
        # Добавляем блок с ошибкой перед формой
        error_html = f"""
        <div style="background: #ffebee; color: #c62828; padding: 15px; border-radius: 5px; border-left: 4px solid #c62828; margin-bottom: 20px;">
            <strong>⚠️ Ошибка:</strong> {error_message}
        </div>
        """
    
        # Вставляем ошибку после заголовка h1
        html_content = html_content.replace(
            '<h1>Автоматическая проверка Эссе</h1>',
            f'<h1>Автоматическая проверка Эссе</h1>{error_html}'
        )
    
        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(html_content.encode())

    # -----------------------------
    # Обработка GET-запросов
    # -----------------------------
    def do_GET(self):
        try:
            if self.path == '/':
                self.serve_static_file('templates/start.html')
            elif self.path == '/result':
                self.serve_static_file('templates/result.html')
            elif self.path == '/health':
                # Добавляем health check endpoint
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok"}).encode())
            elif self.path.startswith('/static/'):
                file_path = self.path.split('?')[0][1:]
                self.serve_static_file(file_path)
            else:
                self.send_error(404, "File not found")
        except Exception as e:
            print(f"Error in GET: {str(e)}")
            self.send_error(500, "Internal server error")

    # -----------------------------
    # Обработка POST-запросов
    # -----------------------------
    def do_POST(self):
        if self.path == '/evaluate':
            try:
                content_type = self.headers.get('Content-Type', '')

                if 'multipart/form-data' in content_type:
                    form_data = self.parse_multipart_form_data()

                    csv_file = form_data.get('csv_file')
                    csv_path = form_data.get('csv_path')

                    if csv_path:
                        csv_path = csv_path.decode().strip()
                        print(f"Пользователь указал путь к CSV: {csv_path}")
                        results = self.process_csv_file(csv_path)
                    elif csv_file:
                        # Проверка что это CSV файл
                        if not csv_file.startswith(b'reference_text_id') and not csv_file.startswith(b'essay_text') and b'.csv' not in str(csv_file[:100]).lower():
                            self.send_error_to_start("Ошибка: загруженный файл не является CSV файлом")
                            return
                        
                        with tempfile.NamedTemporaryFile(delete=False, suffix='.csv') as tmp_file:
                            tmp_file.write(csv_file)
                            tmp_path = tmp_file.name
                        results = self.process_csv_file(tmp_path)
                        os.unlink(tmp_path)
                    else:
                        self.send_error_to_start("Ошибка: не передан CSV файл")
                        return

                    # Перенаправляем на /result
                    self.send_response(303)
                    self.send_header('Location', '/result')
                    self.end_headers()

                else:
                    self.send_error(400, "Неподдерживаемый тип данных")

            except Exception as e:
                print(f"Запрос на обработку ошибки: {str(e)}")
                self.send_error_to_start(f"Ошибка обработки: {str(e)}")

    # -----------------------------
    # Парсинг multipart/form-data
    # -----------------------------
    def parse_multipart_form_data(self):
        content_length = int(self.headers['Content-Length'])
        boundary = self.headers['Content-Type'].split('boundary=')[1]
        data = self.rfile.read(content_length)
        parts = data.split(b'--' + boundary.encode())
        form_data = {}

        for part in parts:
            if b'Content-Disposition: form-data;' in part:
                header, content = part.split(b'\r\n\r\n', 1)
                name_start = header.find(b'name="') + 6
                name_end = header.find(b'"', name_start)
                field_name = header[name_start:name_end].decode()
                if content.endswith(b'\r\n'):
                    content = content[:-2]
                form_data[field_name] = content
        return form_data

    # -----------------------------
    # Обработка CSV файла
    # -----------------------------
    def process_csv_file(self, file_path):
        try:
            print(f"Начинаем обработку CSV файла: {file_path}")
        
            # Проверяем существование файла
            if not os.path.exists(file_path):
                raise Exception("Файл не найден")
            
            # Проверяем размер файла
            file_size = os.path.getsize(file_path)
            if file_size == 0:
                raise Exception("Файл пустой")
        
            # Пробуем прочитать CSV
            try:
                df = pd.read_csv(file_path, encoding='utf-8')
            except UnicodeDecodeError:
                # Пробуем другие кодировки
                try:
                    df = pd.read_csv(file_path, encoding='cp1251')
                except:
                    try:
                        df = pd.read_csv(file_path, encoding='latin1')
                    except:
                        raise Exception("Не удалось прочитать файл. Проверьте кодировку (должна быть UTF-8)")
        
            print(f"Файл прочитан успешно. Найдено {len(df)} строк")
            print(f"Колонки в файле: {list(df.columns)}")

            # Приводим названия столбцов к нижнему регистру
            df.columns = [col.strip().lower() for col in df.columns]

            # Проверяем обязательные колонки
            required_cols = ["essay_text", "task_text"]
            missing = [c for c in required_cols if c not in df.columns]
        
            # Пробуем найти альтернативные названия
            if missing:
                alt_mapping = {
                    "essay_text": ["reference_text", "текст", "text", "сочинение"],
                    "task_text": ["task", "задание", "prompt"]
                }
            
                for missing_col in missing[:]:
                    for alt_name in alt_mapping.get(missing_col, []):
                        if alt_name in df.columns:
                            df.rename(columns={alt_name: missing_col}, inplace=True)
                            missing.remove(missing_col)
                            print(f"Переименована колонка '{alt_name}' в '{missing_col}'")
                            break
        
            if missing:
                raise Exception(
                    f"В CSV файле отсутствуют обязательные колонки: {', '.join(missing)}. "
                    f"Найдены колонки: {', '.join(df.columns)}"
                )

            # Добавляем тип сочинения по умолчанию
            if "essay_type" not in df.columns:
                df["essay_type"] = 2

            # Проверяем инициализацию модели (API ключ)
            try:
                evaluator = self.evaluator
            except Exception as e:
                if "API" in str(e) or "credential" in str(e).lower() or "GIGACHAT" in str(e).upper():
                    raise Exception("Ошибка API ключа GigaChat. Проверьте настройки окружения.")
                else:
                    raise Exception(f"Ошибка инициализации модели: {str(e)}")

            results = []
            for idx, row in df.iterrows():
                try:
                    essay_text = str(row["essay_text"]).strip()
                    task_text = str(row["task_text"]).strip()
                    essay_type = int(row["essay_type"])
                
                    if not essay_text or essay_text == "nan":
                        raise Exception("Текст эссе пустой")
                
                    result = self.evaluator.evaluate_single_essay(essay_text, essay_type, task_text)
                    result.update({
                        "essay_id": idx + 1,
                        "essay_type": essay_type,
                        "task_text": task_text,
                        "essay_text": essay_text,
                        "total_score": result["H1"] + result["H2"] + result["H3"] + result["H4"]
                    })
                
                    results.append(result)
                    print(f"✅ Обработано сочинение {idx + 1}/{len(df)}")
                
                except Exception as e:
                    print(f"⚠️ Ошибка при обработке строки {idx + 1}: {str(e)}")
                    results.append({
                        "essay_id": idx + 1,
                        "essay_type": essay_type,
                        "task_text": task_text,
                        "essay_text": essay_text,
                        "H1": 0, "H1_explanation": f"Ошибка: {str(e)}",
                        "H2": 0, "H2_explanation": f"Ошибка: {str(e)}", 
                        "H3": 0, "H3_explanation": f"Ошибка: {str(e)}",
                        "H4": 0, "H4_explanation": f"Ошибка: {str(e)}",
                        "total_score": 0
                    })

            # Сохраняем результаты
            results_file_path = "static/temp_results.json"
            os.makedirs(os.path.dirname(results_file_path), exist_ok=True)
            with open(results_file_path, "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=2)

            print(f"✅ Обработка завершена. Результаты сохранены в {results_file_path}")
            return results

        except Exception as e:
            print(f"❌ Ошибка при обработке CSV файла: {str(e)}")
            raise Exception(f"Ошибка обработки файла: {str(e)}")

    # -----------------------------
    # Отдача статических файлов
    # -----------------------------
    def serve_static_file(self, file_path):
        try:
            with open(file_path, 'rb') as file:
                content = file.read()
            self.send_response(200)
            if file_path.endswith('.html'):
                self.send_header('Content-type', 'text/html; charset=utf-8')
            elif file_path.endswith('.json'):
                self.send_header('Content-type', 'application/json')
            else:
                self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(content)
        except FileNotFoundError:
            self.send_error(404, f"File {file_path} not found")

    def log_message(self, format, *args):
        return  # без спама в консоли


# -----------------------------
# Запуск HTTP-сервера
# -----------------------------
def run_server():
    # Создаем необходимые директории
    os.makedirs('templates', exist_ok=True)
    os.makedirs('static', exist_ok=True)
    
    # Получаем порт из переменной окружения или используем 8000 по умолчанию
    port = int(os.environ.get('PORT', 8000))
    host = os.environ.get('HOST', '0.0.0.0')
    
    server = HTTPServer((host, port), EssayHandler)
    print(f"🚀 Сервер запущен на http://{host}:{port}")
    print(f"✅ Health check доступен по http://{host}:{port}/health")
    server.serve_forever()


if __name__ == '__main__':
    # ensure_requirements_updated()
    run_server()