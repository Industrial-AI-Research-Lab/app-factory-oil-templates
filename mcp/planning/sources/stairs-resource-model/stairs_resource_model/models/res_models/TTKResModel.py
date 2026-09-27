from stairs_resource_model.models.res_models.BaseResModel import BaseResModel

class TTKResModel(BaseResModel):
    def __init__(self, **kwargs):
        super(TTKResModel, self).__init__(**kwargs)

    def fit(self, X):
        """Fit methods initializes sources"""
        self.regressors = X["regressors"]
        self.resources = X["resources"]

    def predict(self, work_volume=None):
        numbers = self.regressors
        upper_bound = 5*numbers
        medium_bound = 3*numbers
        return [numbers, medium_bound, upper_bound]