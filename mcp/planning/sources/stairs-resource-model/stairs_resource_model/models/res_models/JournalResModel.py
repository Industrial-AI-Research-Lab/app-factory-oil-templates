from stairs_resource_model.models.res_models.BaseResModel import BaseResModel
import numpy as np



class JournalResModel(BaseResModel):
    def __init__(self, work_name: str, unit: str, shift=11) -> None:
        super().__init__(work_name, unit, shift)
    def fit(self, data):
        self.resources = list(data.keys())
        self.regressors = np.array([[data[k]['number'], data[k]['productivity']] for k in data])
    def predict(self, work_volume=None):
        numbers = self.regressors[:,0]
        upper_bound = 5*numbers
        medium_bound = 3*numbers
        return [numbers, medium_bound, upper_bound]



