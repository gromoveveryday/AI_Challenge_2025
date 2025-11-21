from http.server import HTTPServer, BaseHTTPRequestHandler
import os
import json
import pandas as pd
import tempfile
from models.model import EssayEvaluator

class EssayHandler(BaseHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        self._evaluator = None
        super().__init__(*args, **kwargs)
    
    @property
    def evaluator(self):
        if self._evaluator is None:
            self._evaluator = EssayEvaluator()
        return self._evaluator
    
    def send_error_to_start(self, error_message):
        """Перенаправляет на стартовую страницу с сообщением об ошибке"""
        with open('templates/start.html', 'r', encoding='utf-8') as f:
            html_content = f.read()
    
        error_html = f"""
        <div style="background: #ffebee; color: #c62828; padding: 15px; border-radius: 5px; border-left: 4px solid #c62828; margin-bottom: 20px;">
            <strong>⚠️ Ошибка:</strong> {error_message}
        </div>
        """
    
        html_content = html_content.replace(
            '<h1>Автоматическая проверка Эссе</h1>',
            f'<h1>Автоматическая проверка Эссе</h1>{error_html}'
        )
    
        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(html_content.encode())

    def send_json_response(self, data, status=200):
        """Отправляет JSON ответ"""
        self.send_response(status)
        self.send_header('Content-type', 'application/json; charset=utf-8')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def parse_json_body(self):
        """Парсит JSON тело запроса"""
        content_length = int(self.headers.get('Content-Length', 0))
        if content_length == 0:
            return None
        
        body = self.rfile.read(content_length)
        try:
            return json.loads(body.decode('utf-8'))
        except json.JSONDecodeError:
            return None

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
                # Health check endpoint
                self.send_json_response({"status": "ok", "service": "essay_evaluator"})
            
            # API endpoints
            elif self.path == '/api/health':
                self.send_json_response({"status": "healthy", "version": "1.0"})
            elif self.path == '/api/docs':
                self.serve_api_documentation()
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
        try:
            # API endpoints
            if self.path == '/api/evaluate':
                self.handle_api_evaluate()
            elif self.path == '/api/batch-evaluate':
                self.handle_api_batch_evaluate()
            elif self.path == '/evaluate':
                self.handle_web_evaluate()
            else:
                self.send_error(404, "Endpoint not found")
                
        except Exception as e:
            print(f"Error in POST: {str(e)}")
            self.send_json_response({"error": str(e)}, 500)

    def handle_web_evaluate(self):
        """Обработка веб-формы (оригинальный функционал)"""
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

            self.send_response(303)
            self.send_header('Location', '/result')
            self.end_headers()
        else:
            self.send_error(400, "Неподдерживаемый тип данных")

    def handle_api_evaluate(self):
        """API endpoint для оценки одного эссе"""
        data = self.parse_json_body()
        
        if not data:
            self.send_json_response({"error": "Invalid JSON"}, 400)
            return

        required_fields = ["essay_text", "task_text"]
        missing_fields = [field for field in required_fields if field not in data]
        
        if missing_fields:
            self.send_json_response({
                "error": f"Missing required fields: {', '.join(missing_fields)}"
            }, 400)
            return

        try:
            essay_text = data["essay_text"]
            task_text = data["task_text"]
            essay_type = data.get("essay_type", 2)

            if not essay_text.strip():
                self.send_json_response({"error": "Essay text cannot be empty"}, 400)
                return

            # Оценка эссе
            result = self.evaluator.evaluate_single_essay(essay_text, essay_type, task_text)
            
            # Добавляем дополнительную информацию
            result.update({
                "essay_type": essay_type,
                "task_text": task_text,
                "total_score": result["H1"] + result["H2"] + result["H3"] + result["H4"],
                "status": "success"
            })

            self.send_json_response(result)
            print(f"✅ Оценено эссе через API (тип: {essay_type})")

        except Exception as e:
            print(f"❌ Ошибка оценки через API: {str(e)}")
            self.send_json_response({
                "error": f"Evaluation failed: {str(e)}",
                "status": "error"
            }, 500)

    def handle_api_batch_evaluate(self):
        """API endpoint для пакетной оценки эссе"""
        data = self.parse_json_body()
        
        if not data:
            self.send_json_response({"error": "Invalid JSON"}, 400)
            return

        if "essays" not in data:
            self.send_json_response({"error": "Missing 'essays' array"}, 400)
            return

        essays = data["essays"]
        if not isinstance(essays, list):
            self.send_json_response({"error": "'essays' must be an array"}, 400)
            return

        if len(essays) > 100:  # Лимит на пакетную обработку
            self.send_json_response({"error": "Too many essays in batch (max 100)"}, 400)
            return

        results = []
        for i, essay_data in enumerate(essays):
            try:
                required_fields = ["essay_text", "task_text"]
                missing_fields = [field for field in required_fields if field not in essay_data]
                
                if missing_fields:
                    results.append({
                        "id": i,
                        "status": "error",
                        "error": f"Missing fields: {', '.join(missing_fields)}"
                    })
                    continue

                essay_text = essay_data["essay_text"]
                task_text = essay_data["task_text"]
                essay_type = essay_data.get("essay_type", 2)

                if not essay_text.strip():
                    results.append({
                        "id": i,
                        "status": "error", 
                        "error": "Essay text cannot be empty"
                    })
                    continue

                # Оценка эссе
                result = self.evaluator.evaluate_single_essay(essay_text, essay_type, task_text)
                result.update({
                    "id": i,
                    "essay_type": essay_type,
                    "total_score": result["H1"] + result["H2"] + result["H3"] + result["H4"],
                    "status": "success"
                })
                results.append(result)
                print(f"✅ Оценено эссе {i+1}/{len(essays)} через API")

            except Exception as e:
                print(f"❌ Ошибка оценки эссе {i+1}: {str(e)}")
                results.append({
                    "id": i,
                    "status": "error",
                    "error": str(e)
                })

        self.send_json_response({
            "results": results,
            "total_processed": len(results),
            "successful": len([r for r in results if r.get("status") == "success"]),
            "failed": len([r for r in results if r.get("status") == "error"])
        })

    def serve_api_documentation(self):
        """Отдает документацию по API"""
        docs = {
            "service": "Essay Evaluator API",
            "version": "1.0",
            "endpoints": {
                "GET /api/health": {
                    "description": "Health check",
                    "response": {"status": "healthy"}
                },
                "POST /api/evaluate": {
                    "description": "Evaluate single essay",
                    "request": {
                        "essay_text": "string (required)",
                        "task_text": "string (required)", 
                        "essay_type": "integer (optional, default: 2)"
                    },
                    "response": {
                        "H1": "integer score",
                        "H1_explanation": "string",
                        "H2": "integer score",
                        "H2_explanation": "string",
                        "H3": "integer score", 
                        "H3_explanation": "string",
                        "H4": "integer score",
                        "H4_explanation": "string",
                        "total_score": "integer",
                        "status": "success"
                    }
                },
                "POST /api/batch-evaluate": {
                    "description": "Evaluate multiple essays",
                    "request": {
                        "essays": [
                            {
                                "essay_text": "string",
                                "task_text": "string",
                                "essay_type": "integer"
                            }
                        ]
                    },
                    "response": {
                        "results": "array of evaluation results",
                        "total_processed": "integer",
                        "successful": "integer",
                        "failed": "integer"
                    }
                }
            }
        }
        
        self.send_json_response(docs)

    # -----------------------------
    # Остальные методы без изменений
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

    def process_csv_file(self, file_path):
        try:
            print(f"Начинаем обработку CSV файла: {file_path}")
        
            if not os.path.exists(file_path):
                raise Exception("Файл не найден")
            
            file_size = os.path.getsize(file_path)
            if file_size == 0:
                raise Exception("Файл пустой")
        
            try:
                df = pd.read_csv(file_path, encoding='utf-8')
            except UnicodeDecodeError:
                try:
                    df = pd.read_csv(file_path, encoding='cp1251')
                except:
                    try:
                        df = pd.read_csv(file_path, encoding='latin1')
                    except:
                        raise Exception("Не удалось прочитать файл. Проверьте кодировку (должна быть UTF-8)")
        
            print(f"Файл прочитан успешно. Найдено {len(df)} строк")
            print(f"Колонки в файле: {list(df.columns)}")

            df.columns = [col.strip().lower() for col in df.columns]

            required_cols = ["essay_text", "task_text"]
            missing = [c for c in required_cols if c not in df.columns]
        
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

            if "essay_type" not in df.columns:
                df["essay_type"] = 2

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

            results_file_path = "static/temp_results.json"
            os.makedirs(os.path.dirname(results_file_path), exist_ok=True)
            with open(results_file_path, "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=2)

            print(f"✅ Обработка завершена. Результаты сохранены в {results_file_path}")
            return results

        except Exception as e:
            print(f"❌ Ошибка при обработке CSV файла: {str(e)}")
            raise Exception(f"Ошибка обработки файла: {str(e)}")

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
        return


def run_server():
    os.makedirs('templates', exist_ok=True)
    os.makedirs('static', exist_ok=True)
    
    port = int(os.environ.get('PORT', 8000))
    host = os.environ.get('HOST', '0.0.0.0')
    
    server = HTTPServer((host, port), EssayHandler)
    print(f"🚀 Сервер запущен на http://{host}:{port}")
    print(f"📚 API endpoints:")
    print(f"   GET  http://{host}:{port}/api/health - Health check")
    print(f"   GET  http://{host}:{port}/api/docs - API documentation") 
    print(f"   POST http://{host}:{port}/api/evaluate - Evaluate single essay")
    print(f"   POST http://{host}:{port}/api/batch-evaluate - Evaluate multiple essays")
    print(f"🌐 Web interface: http://{host}:{port}/")
    server.serve_forever()


if __name__ == '__main__':
    run_server()