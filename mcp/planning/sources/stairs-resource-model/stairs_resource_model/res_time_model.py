from typing import Literal
from stairs_resource_model.schema import ResourceDict, WorkerReqs, WorkUnit
import pickle

class ResTimeModel:
    def __init__(self, dbwrapper):
        self.wrapper = dbwrapper

    def get_resources_volumes(self,
                              work_name: str,
                              work_volume: float,
                              measurement: str,
                              model_type: str = None,
                              shift: float = 11.) -> WorkerReqs:
        """
        Method to calculate new volumes for resources given a volume of work
        :param work_name: name for a work
        :param work_volume: volume for a work
        :param measurement: measurement for a work
        :param shift: amount of time staff works per day
        :return: dict with staff min_count and max_count.
        Max_count means a max possible value for resources, vice versa for min.
        """

        model = self.wrapper.get_res_model(name=work_name, model_type=model_type, measurement_type=measurement)
        if not model:
            raise Exception(f"No model in DB for work, unit, model type:{work_name},{measurement},{model_type}")
        model = model[0]
        res_model = pickle.loads(model['data'])
        res = res_model.resources
        worker_reqs = {'worker_reqs': []}
        res_volumes = res_model.predict(work_volume=work_volume)
        if work_volume != 0:
            for i, r in enumerate(res):
                worker_reqs['worker_reqs'].append({'kind': r,
                                                        'volume': int(res_volumes[1][i]),
                                                        'min_count': int(res_volumes[0][i]),
                                                        'max_count': int(res_volumes[2][i])})
        else:
            for i, r in enumerate(res):
                worker_reqs['worker_reqs'].append({'kind': r,
                                                    'volume': 0,
                                                    'min_count': 0,
                                                    'max_count': 0})

        return worker_reqs

    def estimate_time(self,
                      work_unit: WorkUnit,
                      worker_list: list[ResourceDict],
                      model_type: str = None,
                      mode: Literal["0.5", "0.1", "0.9"] = '0.5') -> int:
        """
        Method to get time for given resources and work volume

        :param work_unit: work_name and its volume and its measurement
        :param worker_list: resources for work
        :param mode: quantile for calculation
        :return: number of shifts required to make work
        """
        if not worker_list:
            return 0
        if work_unit['volume'] == 0:
            return 0
        work_name = work_unit['name']
        work_volume = work_unit['volume']
        work_measurement = work_unit['measurement']
        res_dict = {req['name']: req['_count'] for req in worker_list}
        model = self.wrapper.get_perf_model(name=work_name, model_type=model_type, measurement_type=work_measurement)
        if not model:
            raise Exception(f"No model in DB for work, unit, model type:{work_name},{work_measurement},{model_type}")
        model = model[0]
        time_model = pickle.loads(model['data'])
        res = time_model.resources
        res_volumes = [res_dict[r] for r in res]
        time = time_model.predict(work_volume, res_volumes)
        match mode:
            case '0.1':
                return int(time[0])
            case '0.5':
                return int(time[1])
            case '0.9':
                return int(time[2])
        
