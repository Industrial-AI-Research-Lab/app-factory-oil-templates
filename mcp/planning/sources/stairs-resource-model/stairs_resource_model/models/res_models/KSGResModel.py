from stairs_resource_model.models.res_models.BaseResModel import BaseResModel
import numpy as np
from sklearn.linear_model import QuantileRegressor
import math
import xgboost as xgb




class KSGResModel(BaseResModel):
    def __init__(self, work_name: str, unit: str, shift=11, regressor_objects = [QuantileRegressor(quantile=0.1), QuantileRegressor(quantile=0.5), QuantileRegressor(quantile=0.9)]) -> None:
        super().__init__(work_name, unit, shift)
        self.regressor_objects = regressor_objects
    def fit(self, data):
        journal = data[0]
        real_data = data[1]
        self.resources = list(journal.keys())
        self.regressor_data = []
        for model in self.regressor_objects:
            model.fit(real_data[['Total Volume']].values, real_data['Volume per day'].values)
            self.regressor_data.append(model)
        self.regressors_journal = np.array([[journal[k]['number'], journal[k]['productivity']] for k in journal])
    def predict(self, work_volume):
        numbers = self.regressors_journal[:,0]
        perf_journal = self.regressors_journal[0,1]
        result = []
        for model in self.regressor_data:
            volume_per_day = model.predict([[work_volume]])[0]
            coef = volume_per_day / perf_journal
            scaled_numbers = [math.ceil(coef*n) for n in numbers]
            scaled_numbers = [1 if x <= 0 else x for x in scaled_numbers]
            result.append(scaled_numbers)
        return result



