from stairs_resource_model.models.time_models.BaseTimeModel import BaseTimeModel
import numpy as np
import math
from sklearn.linear_model import QuantileRegressor


class KSGTimeModel(BaseTimeModel):
    def __init__(self, work_name: str, unit: str, shift=11, regressor_objects = [QuantileRegressor(quantile=0.1), QuantileRegressor(quantile=0.5), QuantileRegressor(quantile=0.9)]) -> None:
        super().__init__(work_name, unit, shift)
        self.regressor_objects = regressor_objects
    def fit(self, data):
        journal = data[0]
        real_data = data[1]
        self.resources = list(journal.keys())
        self.regressor_data = []
        for model in self.regressor_objects:
            model.fit(real_data[['Total Volume', 'Volume per day']].values, real_data['Time'].values)
            self.regressor_data.append(model)
        self.regressors_journal = np.array([[journal[k]['number'], journal[k]['productivity']] for k in journal])
    def predict(self, work_volume, resources):
        min_numbers = resources / self.regressors_journal[:,0]
        coef = min(min_numbers)
        perf = coef*self.regressors_journal[0,1]
        times = []
        for model in self.regressor_data:
            times.append(math.ceil(model.predict([[work_volume, perf]])))
        times = [1 if x <= 0 else x for x in times]
        return times



