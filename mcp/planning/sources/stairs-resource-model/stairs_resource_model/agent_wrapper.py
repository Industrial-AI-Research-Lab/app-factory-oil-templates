from stairs_resource_model.res_time_model import ResTimeModel
from stairs_storage.adapters.mschm import ModelsAdapter
import os


def _storage():
    url = os.environ.get("RM_ADAPTER_CONN_STR")
    if not url:
        raise RuntimeError("RM_ADAPTER_CONN_STR is required")
    return ModelsAdapter(url=url)

def get_resource(work_name:str, work_volume:float, unit:str):
    db_wrapper = _storage()
    res_time_model = ResTimeModel(dbwrapper=db_wrapper)
    res_data = res_time_model.get_resources_volumes(work_name=work_name, work_volume=work_volume, measurement=unit)
    return res_data

def get_time(work_unit:dict, worker_and_resource_list:list, mode="0.5"):
    db_wrapper = _storage()
    res_time_model = ResTimeModel(dbwrapper=db_wrapper)
    time = res_time_model.estimate_time(work_unit=work_unit, worker_list=worker_and_resource_list, mode=mode)
    return time
