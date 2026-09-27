from abc import ABC

class BaseTimeModel(ABC):
    def __init__(self, work_name:str, unit:str, shift = 11) -> None:
        self.work_name = work_name
        self.unit = unit
        self.shift = shift
        self.resources = None
        self.regressors = None
    