from stairs_resource_model.models.time_models.BaseTimeModel import BaseTimeModel
import numpy as np
import math


class JournalTimeModel(BaseTimeModel):
    def __init__(self, work_name: str, unit: str, shift=11) -> None:
        super().__init__(work_name, unit, shift)
    def fit(self, data):
        self.resources = list(data.keys())
        self.regressors = np.array([[data[k]['number'], data[k]['productivity']] for k in data])
    def predict(self, work_volume, resources):
        min_numbers = resources / self.regressors[:,0]
        coef = min(min_numbers)
        perf = coef*self.regressors[0,1]
        time = work_volume / perf
        return [math.ceil(time), math.ceil(time), math.ceil(time)]



