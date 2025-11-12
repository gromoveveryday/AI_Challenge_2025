# model.py
import json
import re
import os
import pandas as pd
from typing import Dict, Any
from pydantic import BaseModel, Field
from langchain.chains import LLMChain
from langchain.prompts import ChatPromptTemplate
from langchain.output_parsers import PydanticOutputParser
from langchain_community.llms import GigaChat
import nltk
from nltk.tokenize import word_tokenize

nltk.download('punkt')
nltk.download('punkt_tab')

api_key = os.environ.get("GIGACHAT_CREDENTIALS")

def count_words_oge(text):
    """
    Подсчет слов по правилам ОГЭ:
    - Учитываются последовательности символов без пробелов
    - Инициалы с фамилией считаются одним словом
    - Цифры и другие не-буквенные символы не учитываются
    """
    if not text or not isinstance(text, str):
        return 0
    
    # Предварительная обработка для объединения инициалов с фамилией
    text = re.sub(r'([А-ЯЁ]\.\s*[А-ЯЁ]\.\s*[А-ЯЯёЁ]+)', 'ФИО', text)
    text = re.sub(r'([А-ЯЁ]\.\s*[А-ЯЯёЁ]+)', 'ФИО', text)
    
    # Токенизация с учетом специфики русского языка
    words = word_tokenize(text, language='russian')
    
    # Фильтрация: оставляем только слова, содержащие буквы
    valid_words = []
    for word in words:
        # Убираем знаки препинания по краям
        clean_word = word.strip('.,!?;:"()[]{}«»-–—')
        # Если слово содержит хотя бы одну букву - учитываем
        if clean_word and any(c.isalpha() for c in clean_word):
            valid_words.append(clean_word)
    
    return len(valid_words)

# Определяем структуру вывода с помощью Pydantic
class EssayEvaluation(BaseModel):
    H1: int = Field(description="Балл по критерию K1", ge=0, le=1, default=0)
    H1_explanation: str = Field(description="Обоснование оценки по критерию K1", default="")
    H2: int = Field(description="Балл по критерию K2", ge=0, le=3, default=0)
    H2_explanation: str = Field(description="Обоснование оценки по критерию K2", default="")
    H3: int = Field(description="Балл по критерию K3", ge=0, le=2, default=0)
    H3_explanation: str = Field(description="Обоснование оценки по критерию K3", default="")
    H4: int = Field(description="Балл по критерию K4", ge=0, le=1, default=0)
    H4_explanation: str = Field(description="Обоснование оценки по критерию K4", default="")

class EssayEvaluator:
    def __init__(self):
        """Инициализация модели и промптов"""
        # Настройка GigaChat
        self.llm = GigaChat(
            credentials=api_key,
            verify_ssl_certs=False,
            model='GigaChat-2',
            temperature=0.1
        )
        
        # Создаем парсер вывода
        self.parser = PydanticOutputParser(pydantic_object=EssayEvaluation)
        self.safe_parser = self._create_safe_parser()
        
        # Создаем промпты
        self.prompts = {
            2: self._create_prompt_type2(),
            3: self._create_prompt_type3()
        }
        
        # Создаем цепочки
        self.chains = {
            2: LLMChain(llm=self.llm, prompt=self.prompts[2], output_parser=self.safe_parser),
            3: LLMChain(llm=self.llm, prompt=self.prompts[3], output_parser=self.safe_parser)
        }
    
    def _create_safe_parser(self):
        """Создает безопасный парсер для обработки ошибок"""
        class SafePydanticOutputParser(PydanticOutputParser):
            def parse(self, text: str):
                try:
                    # Пытаемся найти JSON в тексте (модель может добавлять пояснения)
                    json_match = re.search(r'\{.*\}', text, re.DOTALL)
                    if json_match:
                        json_text = json_match.group()
                        data = json.loads(json_text)
                        
                        # Обрабатываем None значения
                        for field in ['H1', 'H2', 'H3', 'H4']:
                            if field in data and data[field] is None:
                                data[field] = 0
                        for field in ['H1_explanation', 'H2_explanation', 'H3_explanation', 'H4_explanation']:
                            if field in data and data[field] is None:
                                data[field] = "Обоснование не предоставлено"
                        
                        return self.pydantic_object.parse_obj(data)
                    else:
                        # Если JSON не найден, создаем объект с значениями по умолчанию
                        return self.pydantic_object.parse_obj({
                            'H1': 0, 'H1_explanation': 'Не удалось извлечь оценку',
                            'H2': 0, 'H2_explanation': 'Не удалось извлечь оценку',
                            'H3': 0, 'H3_explanation': 'Не удалось извлечь оценку',
                            'H4': 0, 'H4_explanation': 'Не удалось извлечь оценку'
                        })
                except Exception as e:
                    print(f"Ошибка парсинга: {str(e)}")
                    return self.pydantic_object.parse_obj({
                        'H1': 0, 'H1_explanation': f'Ошибка парсинга: {str(e)}',
                        'H2': 0, 'H2_explanation': f'Ошибка парсинга: {str(e)}',
                        'H3': 0, 'H3_explanation': f'Ошибка парсинга: {str(e)}',
                        'H4': 0, 'H4_explanation': f'Ошибка парсинга: {str(e)}'
                    })
        
        return SafePydanticOutputParser(pydantic_object=EssayEvaluation)
    
    def _create_prompt_type2(self):
        """Промпт для типа 2 (13.2 - литературно-тематическое сочинение)"""
        prompt_text = """Ты - эксперт по проверке сочинений ОГЭ по русскому языку. 
Оцени сочинение по критериям К1-К4 для задания типа 13.2 (литературно-тематическое сочинение).

КРИТЕРИИ ОЦЕНКИ для типа 2:
К1 (0-1 балл): Понимание смысла фрагмента текста - верное объяснение содержания фрагмента. 1 балл: Ученик дал ясное и корректное объяснение смысла фрагмента. 0 баллов: Объяснение отсутствует или неверно.
К2 (0-3 балла): Приведение двух примеров-иллюстраций из опорного текста, иллюстрирующих понимание фрагмента. 3 балла: Приведено 2 примера из текста, оба поясняют смысл фрагмента. 2 балла: 2 примера, но один из них слабо поясняет смысл. 1 балл: Приведен 1 пример, который поясняет смысл фрагмента. 0 баллов: Примеры отсутствуют или не являются аргументами.
К3 (0-2 балла): Логичность речи - смысловая цельность, связность, последовательность изложения. 0 ошибок = 2 балла, 1 ошибка = 1 балл, >1 ошибки = 0 баллов.
К4 (0-1 балл): Композиционная стройность - трехчастная композиция, завершенность. ≤1 ошибка = 1 балл, иначе 0.
По каждому критерию ты должен дать пояснения к оценке, которую ты поставил.
Если в сочинении менее 70 слов, выстави 0 баллов по всем критериям
Текст задания: {task_text}
Тип сочинения: 2 (13.2)

Верни ответ строго в указанном JSON-формате."""
        
        return ChatPromptTemplate.from_messages([
            ("system", prompt_text),
            ("human", "Текст сочинения: {essay_text}\n\n{format_instructions}")
        ]).partial(format_instructions=self.parser.get_format_instructions())
    
    def _create_prompt_type3(self):
        """Промпт для типа 3 (13.3 - морально-нравственное сочинение)"""
        prompt_text = """Ты - эксперт по проверке сочинений ОГЭ по русскому языку. 
Оцени сочинение по критериям К1-К4 для задания типа 13.3 (морально-нравственное сочинение).

КРИТЕРИИ ОЦЕНКИ для типа 3:
К1 (0-1 балл): Наличие обоснованного ответа на поставленный вопрос. 1 балл: Есть как минимум простое определение понятия И ответ на вопрос темы (тезис). 0 баллов: Определения или ответа на вопрос нет.
К2 (0-3 балла): Приведение одного примера из опорного текста и одного примера из личного опыта/литературы. 3 балла: Приведено 2 примера (1 из текста + 1 из жизни), оба иллюстрируют тезис. 2 балла: 2 примера, но один из них слабо иллюстрирует тезис. 1 балл: Приведен 1 пример (из текста или жизни), который иллюстрирует тезис. 0 баллов: Примеры отсутствуют или не являются аргументами.
К3 (0-2 балла): Логичность речи - смысловая цельность, связность, последовательность изложения. 0 ошибок = 2 балла, 1 ошибка = 1 балл, >1 ошибки = 0 баллов.
К4 (0-1 балл): Композиционная стройность - трехчастная композиция, завершенность. ≤1 ошибка = 1 балл, иначе 0.
По каждому критерию ты должен дать пояснения к оценке, которую ты поставил.
Если в сочинении менее 70 слов, выстави 0 баллов по всем критериям
Текст задания: {task_text}
Тип сочинения: 3 (13.3)

Верни ответ строго в указанном JSON-формате."""
        
        return ChatPromptTemplate.from_messages([
            ("system", prompt_text),
            ("human", "Текст сочинения: {essay_text}\n\n{format_instructions}")
        ]).partial(format_instructions=self.parser.get_format_instructions())
    
    def evaluate_single_essay(self, essay_text: str, essay_type: int, task_text: str) -> Dict[str, Any]:
        """
        Оценивает одно сочинение с проверкой объема
        
        Args:
            essay_text: текст сочинения
            essay_type: тип сочинения (2 или 3)
            task_text: текст задания
            
        Returns:
            Словарь с результатами оценки
        """
        try:
            # Проверка типа сочинения
            if essay_type not in [2, 3]:
                return {
                    "H1": 0,
                    "H1_explanation": f"Тип сочинения {essay_type} не поддерживается",
                    "H2": 0,
                    "H2_explanation": f"Тип сочинения {essay_type} не поддерживается",
                    "H3": 0,
                    "H3_explanation": f"Тип сочинения {essay_type} не поддерживается",
                    "H4": 0,
                    "H4_explanation": f"Тип сочинения {essay_type} не поддерживается"
                }
            
            # Проверка объема
            word_count = count_words_oge(essay_text)
            if word_count < 70:
                return {
                    "H1": 0,
                    "H1_explanation": f"Недостаточный объем сочинения: {word_count} слов при требуемых 70",
                    "H2": 0,
                    "H2_explanation": f"Недостаточный объем сочинения: {word_count} слов при требуемых 70",
                    "H3": 0,
                    "H3_explanation": f"Недостаточный объем сочинения: {word_count} слов при требуемых 70",
                    "H4": 0,
                    "H4_explanation": f"Недостаточный объем сочинения: {word_count} слов при требуемых 70"
                }
            
            # Получаем результат работы цепочки
            result = self.chains[essay_type].invoke({
                "essay_text": essay_text,
                "task_text": task_text
            })

            # Если LangChain вернул объект модели (EssayEvaluation)
            evaluation = result.get("output") or result.get("text") or result

            # Если это Pydantic-модель — конвертируем в dict
            if isinstance(evaluation, EssayEvaluation):
                evaluation = evaluation.dict()

            # Безопасное извлечение
            result_dict = {
                "H1": int(evaluation.get("H1", 0)),
                "H1_explanation": evaluation.get("H1_explanation", ""),
                "H2": int(evaluation.get("H2", 0)),
                "H2_explanation": evaluation.get("H2_explanation", ""),
                "H3": int(evaluation.get("H3", 0)),
                "H3_explanation": evaluation.get("H3_explanation", ""),
                "H4": int(evaluation.get("H4", 0)),
                "H4_explanation": evaluation.get("H4_explanation", "")
            }

            return result_dict

        except Exception as e:
            print(f"Ошибка при оценке сочинения: {str(e)}")
            return {
                "H1": 0,
                "H1_explanation": f"Ошибка обработки: {str(e)}",
                "H2": 0,
                "H2_explanation": f"Ошибка обработки: {str(e)}",
                "H3": 0,
                "H3_explanation": f"Ошибка обработки: {str(e)}",
                "H4": 0,
                "H4_explanation": f"Ошибка обработки: {str(e)}"
            }
    
    def evaluate_batch_essays(self, essays_data: list) -> list:
        """
        Оценивает несколько сочинений
        
        Args:
            essays_data: список словарей с ключами ['essay_text', 'essay_type', 'task_text']
            
        Returns:
            List с результатами оценок
        """
        results = []
        
        for i, essay_data in enumerate(essays_data):
            print(f"Обрабатывается сочинение {i+1}/{len(essays_data)}")
            
            result = self.evaluate_single_essay(
                essay_text=essay_data['essay_text'],
                essay_type=essay_data['essay_type'], 
                task_text=essay_data['task_text']
            )
            
            # Добавляем ID сочинения к результату
            result['essay_id'] = essay_data.get('essay_id', i+1)
            results.append(result)
        
        return results