from stairs_resource_model.models.time_models.BaseTimeModel import BaseTimeModel
import numpy as np

class TTKTimeModel(BaseTimeModel):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def fit(self, X):
        """Fit methods initializes sources"""
        self.regressors = X["regressors"]
        self.resources = X["resources"]

    def predict(self, work_volume, resources_volumes):
        # эвристика, так как нормализация иногда была на число меньше 1
        # разброс большой если есть ресурс с большим влиянием (песок и др.)
        if self.regressors.mean() < 1 or self.regressors.std() > 3:
            partial_impact = resources_volumes / self.regressors
        else:
            partial_impact = resources_volumes / (1 / self.regressors)

        return np.ceil(np.sum((work_volume / partial_impact) / self.shift))