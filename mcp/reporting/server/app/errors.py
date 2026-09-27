FORMAT_ERROR_MESSAGES = {
    "OUTPUT_TOO_LARGE": "Файл превышает допустимый размер",
    "RENDER_FAILED": "Не удалось сформировать файл",
    "RENDER_TIMEOUT": "Истекло время формирования файла",
    "NOT_GENERATED": "Файл не сформирован",
    "UPLOAD_FAILED": "Не удалось загрузить файл",
}


class ReportError(ValueError):
    def __init__(self, code, stage, details):
        self.code = code
        self.stage = stage
        self.details = details
        super().__init__(str(details))
