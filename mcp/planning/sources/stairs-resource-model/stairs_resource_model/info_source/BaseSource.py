from abc import ABC


class BaseSource(ABC):
    def __init__(self, files:list) -> None:
        self.files = files

